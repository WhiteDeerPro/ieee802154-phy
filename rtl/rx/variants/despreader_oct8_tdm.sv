// despreader_oct8_tdm.sv —— 8 边形度量 + 判决串行化（"TDM 判决"）变体
// ---------------------------------------------------------------------------
// 两条正交的资源削减（相对 despreader_oct8）:
//  ① PN 表重排: 原实现每路一个 32bit 变量移位器（16 个 barrel shifter ≈ 2.5k mux,
//     占现版 mux 的绝大多数）。改为 32 个 16bit"片列常量" + 按 chip_cnt 选列
//     （32:1×16 ≈ 0.5k mux）——纯等价重构（列常量由 pn()/CHIP 表转置生成,
//     生成时已做逐位一致性校验）。
//  ② 判决串行化: 16 路度量/比较从"每片并行一份"改为"每拍 2 路、8 拍扫完"——
//     度量单元 16→2、比较器 15→2; 扫描窗口 = 末片后 8 拍空闲（acc 稳定期）。
//
// 时序: 末片拍 T → 扫描拍 T+1..T+4（每拍读 acc[4t..4t+3] 算度量并更新 best）
//       → sym_dv/sym 于 T+5 输出（比组合版晚 4 拍; 符号间隔 256 拍, 无碍）。
// 平局语义: 扫描按 k 递增、严格大于才更新 ⇒ 平局取先出现（小索引）——与原版一致
//   （启动重置 best=0 + 无符号度量, 与"best 初值=met[0]"等价）。
// 设计假设（= deinterleave 的真实节奏, 2026-10-04 由整链失败暴露后修正）:
//   片间隔 12/4 拍**交替**（偶片峰→奇片峰 12 拍、奇→偶 4 拍; 平均 8 拍）;
//   **片 31（奇）→ 下一符号片 0（偶）恒为 4 拍** ⇒ 扫描窗口取 4 拍、每拍 4 路。
//   窗外无 dv（无写）不影响; 仅假设"末片后 4 拍内 acc 不被改写"（真实节奏下恒成立）。
// 度量系数: MODE 同 oct8（默认 1 = 15/16, 15/32）。
// ---------------------------------------------------------------------------
`timescale 1ns/1ps
module despreader_oct8_tdm #(
    parameter W = 12,              // 码片软值位宽
    parameter ACC_W = W + 5,       // 相关累加位宽 (32×|x|)
    parameter MODE = 1             // 系数档: 0=(1,1/2); 1=(15/16,15/32); 2=(1,0)
) (
    input  wire                      clk,
    input  wire                      rst_n,
    input  wire signed [W-1:0]       chip_i,
    input  wire signed [W-1:0]       chip_q,
    input  wire                      chip_dv,
    input  wire                      frame_start,
    output reg  [3:0]                sym,
    output reg                       sym_dv
);
    localparam ABW = ACC_W + 1;    // 18: |·| 与度量的无符号位宽

    reg [5:0] chip_cnt;

    reg signed [ACC_W-1:0] acc_i [0:15];
    reg signed [ACC_W-1:0] acc_q [0:15];

    integer kk;

    // ---- PN 片列常量（转置表; bit k = PN(k,m), 1=+1/0=-1; m=0 → C0 先传）----
    wire [15:0] pn_col [0:31];
    assign pn_col[ 0] = 16'hc3c3;
    assign pn_col[ 1] = 16'hac53;
    assign pn_col[ 2] = 16'h2e2e;
    assign pn_col[ 3] = 16'h4eb1;
    assign pn_col[ 4] = 16'h8787;
    assign pn_col[ 5] = 16'h59a6;
    assign pn_col[ 6] = 16'h5c5c;
    assign pn_col[ 7] = 16'h9c63;
    assign pn_col[ 8] = 16'h0f0f;
    assign pn_col[ 9] = 16'hb24d;
    assign pn_col[10] = 16'hb8b8;
    assign pn_col[11] = 16'h39c6;
    assign pn_col[12] = 16'h1e1e;
    assign pn_col[13] = 16'h659a;
    assign pn_col[14] = 16'h7171;
    assign pn_col[15] = 16'h728d;
    assign pn_col[16] = 16'h3c3c;
    assign pn_col[17] = 16'hca35;
    assign pn_col[18] = 16'he2e2;
    assign pn_col[19] = 16'he41b;
    assign pn_col[20] = 16'h7878;
    assign pn_col[21] = 16'h956a;
    assign pn_col[22] = 16'hc5c5;
    assign pn_col[23] = 16'hc936;
    assign pn_col[24] = 16'hf0f0;
    assign pn_col[25] = 16'h2bd4;
    assign pn_col[26] = 16'h8b8b;
    assign pn_col[27] = 16'h936c;
    assign pn_col[28] = 16'he1e1;
    assign pn_col[29] = 16'h56a9;
    assign pn_col[30] = 16'h1717;
    assign pn_col[31] = 16'h27d8;

    wire [15:0] pn_cur = pn_col[chip_cnt[4:0]];
    wire [15:0] pn_c0  = pn_col[0];

    // 组合前馈: 当前片并入后的累加值（与 oct8 同语义, PN 改列选择）
    reg signed [ACC_W-1:0] nxt_i [0:15];
    reg signed [ACC_W-1:0] nxt_q [0:15];
    always @(*) begin
        for (kk = 0; kk < 16; kk = kk + 1) begin
            if (frame_start || chip_cnt == 6'd0) begin
                nxt_i[kk] = pn_c0[kk] ? {{(ACC_W-W){chip_i[W-1]}}, chip_i}
                                      : -{{(ACC_W-W){chip_i[W-1]}}, chip_i};
                nxt_q[kk] = pn_c0[kk] ? {{(ACC_W-W){chip_q[W-1]}}, chip_q}
                                      : -{{(ACC_W-W){chip_q[W-1]}}, chip_q};
            end else begin
                nxt_i[kk] = pn_cur[kk] ? acc_i[kk] + {{(ACC_W-W){chip_i[W-1]}}, chip_i}
                                       : acc_i[kk] - {{(ACC_W-W){chip_i[W-1]}}, chip_i};
                nxt_q[kk] = pn_cur[kk] ? acc_q[kk] + {{(ACC_W-W){chip_q[W-1]}}, chip_q}
                                       : acc_q[kk] - {{(ACC_W-W){chip_q[W-1]}}, chip_q};
            end
        end
    end

    // ---- 度量函数（与 oct8 完全等价; 综合时按 MODE 常量展开）----
    function [ABW-1:0] metric(input signed [ACC_W-1:0] ci, input signed [ACC_W-1:0] cq);
        reg signed [ABW-1:0] xi, xq;
        reg [ABW-1:0] ai_, aq_, h, l;
        begin
            xi = {{(ABW-ACC_W){ci[ACC_W-1]}}, ci};
            xq = {{(ABW-ACC_W){cq[ACC_W-1]}}, cq};
            ai_ = xi[ABW-1] ? (~xi + 1'b1) : xi;
            aq_ = xq[ABW-1] ? (~xq + 1'b1) : xq;
            h = (ai_ >= aq_) ? ai_ : aq_;
            l = (ai_ >= aq_) ? aq_ : ai_;
            if (MODE == 0)      metric = h + (l >> 1);
            else if (MODE == 2) metric = h;
            else                metric = h - (h >> 4) + (l >> 1) - (l >> 5);
        end
    endfunction

    // ---- 串行判决扫描（末片后 4 拍, 每拍 4 路; 贴住"片31→片0 恒 4 拍"窗口）----
    reg           scan_act;
    reg [1:0]     scan_t;
    reg [ABW-1:0] best_v;
    reg [3:0]     best_k;

    wire last_chip = chip_dv && (chip_cnt == 6'd31);
    wire [3:0] q0 = {scan_t, 2'b00};        // 4t ∈ {0,4,8,12}
    wire [3:0] q1 = q0 + 4'd1;
    wire [3:0] q2 = q0 + 4'd2;
    wire [3:0] q3 = q0 + 4'd3;

    wire [ABW-1:0] met_a = metric(acc_i[q0], acc_q[q0]);
    wire [ABW-1:0] met_b = metric(acc_i[q1], acc_q[q1]);
    wire [ABW-1:0] met_c = metric(acc_i[q2], acc_q[q2]);
    wire [ABW-1:0] met_d = metric(acc_i[q3], acc_q[q3]);

    wire [ABW-1:0] bv1 = (met_a > best_v) ? met_a   : best_v;
    wire [3:0]     bk1 = (met_a > best_v) ? q0      : best_k;
    wire [ABW-1:0] bv2 = (met_b > bv1)    ? met_b   : bv1;
    wire [3:0]     bk2 = (met_b > bv1)    ? q1      : bk1;
    wire [ABW-1:0] bv3 = (met_c > bv2)    ? met_c   : bv2;
    wire [3:0]     bk3 = (met_c > bv2)    ? q2      : bk2;
    wire [ABW-1:0] bv4 = (met_d > bv3)    ? met_d   : bv3;
    wire [3:0]     bk4 = (met_d > bv3)    ? q3      : bk3;

    always @(posedge clk) begin
        if (!rst_n) begin
            scan_act <= 1'b0;
            scan_t   <= 3'd0;
            best_v   <= {ABW{1'b0}};
            best_k   <= 4'd0;
            sym      <= 4'd0;
            sym_dv   <= 1'b0;
        end else begin
            sym_dv <= 1'b0;
            if (!scan_act) begin
                if (last_chip) begin          // 末片拍: 启动扫描（acc 在本沿后即完整）
                    scan_act <= 1'b1;
                    scan_t   <= 2'd0;
                    best_v   <= {ABW{1'b0}};
                    best_k   <= 4'd0;
                end
            end else begin
                best_v <= bv4;
                best_k <= bk4;
                if (scan_t == 2'd3) begin     // 第 4 拍: 出结果（T+5 输出）
                    scan_act <= 1'b0;
                    sym      <= bk4;
                    sym_dv   <= 1'b1;
                end else begin
                    scan_t <= scan_t + 2'd1;
                end
            end
        end
    end

    // ---- 累加器 / 片计数（同 oct8）----
    always @(posedge clk) begin
        if (!rst_n) begin
            chip_cnt <= 6'd0;
        end else begin
            if (frame_start) begin
                if (chip_dv) begin
                    for (kk = 0; kk < 16; kk = kk + 1) begin
                        acc_i[kk] <= nxt_i[kk];
                        acc_q[kk] <= nxt_q[kk];
                    end
                    chip_cnt <= 6'd1;
                end else begin
                    chip_cnt <= 6'd0;
                end
            end else if (chip_dv) begin
                for (kk = 0; kk < 16; kk = kk + 1) begin
                    acc_i[kk] <= nxt_i[kk];
                    acc_q[kk] <= nxt_q[kk];
                end
                if (chip_cnt == 6'd31) begin
                    chip_cnt <= 6'd0;
                end else begin
                    chip_cnt <= chip_cnt + 6'd1;
                end
            end
        end
    end
endmodule
