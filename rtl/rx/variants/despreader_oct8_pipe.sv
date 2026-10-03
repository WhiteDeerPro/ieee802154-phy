// despreader_oct8_pipe.sv —— oct8 度量的"流水化判决树"变体
// ---------------------------------------------------------------------------
// 参考 ip/mcdf/rtl/units/mcdf_piro_encoder.v 的 16→8→4→2→1 四级流水
// min-selector 树结构（每级打拍 + vld_pipe 位移管道 + 2ch 比较器平局语义），
// 对偶为 **max-selector 树** 用于 despreader 的 argmax 判决。
//
// 动机: 组合版 despreader_oct8 在符号末片拍完成 "累加 → 16 路度量 → 15 比较
//   argmax" 一整条组合链再寄存。本变体把判决切成 4 级流水（每级只留 1 个
//   2ch 比较器 + 打拍），组合路径大幅缩短（时序余量↑）；代价 = 4 拍判决延迟
//   （符号间隔 256 拍, 下游无感; sym_dv 与 sym 同拍输出, 整体晚 4 拍）。
//
// 平局语义与原版一致（原版"严格大于才更新" ⇒ 平局取先出现/小索引）:
//   2ch 选择器用 m0 >= m1 取左——左子树索引恒小于右, 归纳即全局一致。
//   注: mcdf 的 base 值逐级跟随机制此处略去（本项目无对应语义）。
// 度量/累加/片计数 = despreader_oct8（MODE 默认 1 = 15/16, 15/32）。
// ---------------------------------------------------------------------------
`timescale 1ns/1ps
module despreader_oct8_pipe #(
    parameter W = 12,              // 码片软值位宽
    parameter ACC_W = W + 5,       // 相关累加位宽 (32×|x|)
    parameter MODE = 1             // 系数档: 0=(1,1/2) 8边形; 1=(15/16,15/32) 8边形; 2=(1,0) 四边形
) (
    input  wire                      clk,
    input  wire                      rst_n,
    input  wire signed [W-1:0]       chip_i,
    input  wire signed [W-1:0]       chip_q,
    input  wire                      chip_dv,
    input  wire                      frame_start,
    output wire [3:0]                sym,
    output wire                      sym_dv
);
    localparam ABW = ACC_W + 1;    // 18: |·| 与度量的无符号位宽

    reg [5:0] chip_cnt;

    reg signed [ACC_W-1:0] acc_i [0:15];
    reg signed [ACC_W-1:0] acc_q [0:15];

    integer kk;

    // PN 表 (与 chip_lut / _CHIP_BITS 一致), pn(k, m): 1=+1, 0=-1; m=0 → bit31 (C0)
    function pn(input integer k, input integer m);
        case (k)
            0 : pn = (32'b11011001110000110101001000101110 >> (31 - m)) & 1;
            1 : pn = (32'b11101101100111000011010100100010 >> (31 - m)) & 1;
            2 : pn = (32'b00101110110110011100001101010010 >> (31 - m)) & 1;
            3 : pn = (32'b00100010111011011001110000110101 >> (31 - m)) & 1;
            4 : pn = (32'b01010010001011101101100111000011 >> (31 - m)) & 1;
            5 : pn = (32'b00110101001000101110110110011100 >> (31 - m)) & 1;
            6 : pn = (32'b11000011010100100010111011011001 >> (31 - m)) & 1;
            7 : pn = (32'b10011100001101010010001011101101 >> (31 - m)) & 1;
            8 : pn = (32'b10001100100101100000011101111011 >> (31 - m)) & 1;
            9 : pn = (32'b10111000110010010110000001110111 >> (31 - m)) & 1;
            10: pn = (32'b01111011100011001001011000000111 >> (31 - m)) & 1;
            11: pn = (32'b01110111101110001100100101100000 >> (31 - m)) & 1;
            12: pn = (32'b00000111011110111000110010010110 >> (31 - m)) & 1;
            13: pn = (32'b01100000011101111011100011001001 >> (31 - m)) & 1;
            14: pn = (32'b10010110000001110111101110001100 >> (31 - m)) & 1;
            15: pn = (32'b11001001011000000111011110111000 >> (31 - m)) & 1;
            default: pn = 1'b0;
        endcase
    endfunction

    // 组合前馈: 当前片并入后的累加值 (与 oct8 相同)
    reg signed [ACC_W-1:0] nxt_i [0:15];
    reg signed [ACC_W-1:0] nxt_q [0:15];
    always @(*) begin
        for (kk = 0; kk < 16; kk = kk + 1) begin
            if (frame_start || chip_cnt == 6'd0) begin
                nxt_i[kk] = pn(kk, 0) ? {{(ACC_W-W){chip_i[W-1]}}, chip_i}
                                      : -{{(ACC_W-W){chip_i[W-1]}}, chip_i};
                nxt_q[kk] = pn(kk, 0) ? {{(ACC_W-W){chip_q[W-1]}}, chip_q}
                                      : -{{(ACC_W-W){chip_q[W-1]}}, chip_q};
            end else begin
                nxt_i[kk] = pn(kk, chip_cnt)
                          ? acc_i[kk] + {{(ACC_W-W){chip_i[W-1]}}, chip_i}
                          : acc_i[kk] - {{(ACC_W-W){chip_i[W-1]}}, chip_i};
                nxt_q[kk] = pn(kk, chip_cnt)
                          ? acc_q[kk] + {{(ACC_W-W){chip_q[W-1]}}, chip_q}
                          : acc_q[kk] - {{(ACC_W-W){chip_q[W-1]}}, chip_q};
            end
        end
    end

    // ---------------- 8 边形度量 (0 乘法, 组合) ----------------
    wire signed [ABW-1:0] sx_i [0:15];
    wire signed [ABW-1:0] sx_q [0:15];
    wire [ABW-1:0]        ai   [0:15];
    wire [ABW-1:0]        aq   [0:15];
    wire [ABW-1:0]        amax [0:15];
    wire [ABW-1:0]        amin [0:15];
    wire [ABW-1:0]        met  [0:15];
    genvar gi;
    generate
        for (gi = 0; gi < 16; gi = gi + 1) begin : g_metric
            assign sx_i[gi] = {{(ABW-ACC_W){nxt_i[gi][ACC_W-1]}}, nxt_i[gi]};
            assign sx_q[gi] = {{(ABW-ACC_W){nxt_q[gi][ACC_W-1]}}, nxt_q[gi]};
            assign ai[gi]   = sx_i[gi][ABW-1] ? (~sx_i[gi] + 1'b1) : sx_i[gi];
            assign aq[gi]   = sx_q[gi][ABW-1] ? (~sx_q[gi] + 1'b1) : sx_q[gi];
            assign amax[gi] = (ai[gi] >= aq[gi]) ? ai[gi] : aq[gi];
            assign amin[gi] = (ai[gi] >= aq[gi]) ? aq[gi] : ai[gi];
            if (MODE == 0) begin : g_m0
                assign met[gi] = amax[gi] + (amin[gi] >> 1);
            end else if (MODE == 2) begin : g_m2
                assign met[gi] = amax[gi];
            end else begin : g_m1
                assign met[gi] = amax[gi] - (amax[gi] >> 4)
                               + (amin[gi] >> 1) - (amin[gi] >> 5);
            end
        end
    endgenerate

    // ---------------- 流水化判决树 (参考 mcdf_piro_encoder) ----------------
    wire last_chip = chip_dv && (chip_cnt == 6'd31);

    reg [3:0] vld_pipe;                       // valid 位移管道: last_chip 起向后 4 拍
    always @(posedge clk) begin
        if (!rst_n) vld_pipe <= 4'd0;
        else        vld_pipe <= {vld_pipe[2:0], last_chip};
    end

    // ---- Stage1: 16 → 8 ----
    wire [ABW-1:0] s1_met_w [0:7];
    wire [3:0]     s1_idx_w [0:7];
    reg  [ABW-1:0] s1_met_r [0:7];
    reg  [3:0]     s1_idx_r [0:7];
    genvar gj;
    generate
        for (gj = 0; gj < 8; gj = gj + 1) begin : g_s1
            localparam C0 = 2 * gj;
            localparam C1 = 2 * gj + 1;
            disp_2ch_max #(.W(ABW)) u_s1 (
                .m0(met[C0]), .i0(C0[3:0]),
                .m1(met[C1]), .i1(C1[3:0]),
                .mo(s1_met_w[gj]), .io(s1_idx_w[gj])
            );
        end
    endgenerate

    integer k1;
    always @(posedge clk) begin
        if (!rst_n) begin
            for (k1 = 0; k1 < 8; k1 = k1 + 1) begin
                s1_met_r[k1] <= {ABW{1'b0}};
                s1_idx_r[k1] <= 4'd0;
            end
        end else if (last_chip) begin
            for (k1 = 0; k1 < 8; k1 = k1 + 1) begin
                s1_met_r[k1] <= s1_met_w[k1];
                s1_idx_r[k1] <= s1_idx_w[k1];
            end
        end
    end

    // ---- Stage2: 8 → 4 ----
    wire [ABW-1:0] s2_met_w [0:3];
    wire [3:0]     s2_idx_w [0:3];
    reg  [ABW-1:0] s2_met_r [0:3];
    reg  [3:0]     s2_idx_r [0:3];
    generate
        for (gj = 0; gj < 4; gj = gj + 1) begin : g_s2
            localparam C0 = 2 * gj;
            localparam C1 = 2 * gj + 1;
            disp_2ch_max #(.W(ABW)) u_s2 (
                .m0(s1_met_r[C0]), .i0(s1_idx_r[C0]),
                .m1(s1_met_r[C1]), .i1(s1_idx_r[C1]),
                .mo(s2_met_w[gj]), .io(s2_idx_w[gj])
            );
        end
    endgenerate

    integer k2;
    always @(posedge clk) begin
        if (!rst_n) begin
            for (k2 = 0; k2 < 4; k2 = k2 + 1) begin
                s2_met_r[k2] <= {ABW{1'b0}};
                s2_idx_r[k2] <= 4'd0;
            end
        end else if (vld_pipe[0]) begin
            for (k2 = 0; k2 < 4; k2 = k2 + 1) begin
                s2_met_r[k2] <= s2_met_w[k2];
                s2_idx_r[k2] <= s2_idx_w[k2];
            end
        end
    end

    // ---- Stage3: 4 → 2 ----
    wire [ABW-1:0] s3_met_w [0:1];
    wire [3:0]     s3_idx_w [0:1];
    reg  [ABW-1:0] s3_met_r [0:1];
    reg  [3:0]     s3_idx_r [0:1];
    generate
        for (gj = 0; gj < 2; gj = gj + 1) begin : g_s3
            localparam C0 = 2 * gj;
            localparam C1 = 2 * gj + 1;
            disp_2ch_max #(.W(ABW)) u_s3 (
                .m0(s2_met_r[C0]), .i0(s2_idx_r[C0]),
                .m1(s2_met_r[C1]), .i1(s2_idx_r[C1]),
                .mo(s3_met_w[gj]), .io(s3_idx_w[gj])
            );
        end
    endgenerate

    integer k3;
    always @(posedge clk) begin
        if (!rst_n) begin
            s3_met_r[0] <= {ABW{1'b0}}; s3_met_r[1] <= {ABW{1'b0}};
            s3_idx_r[0] <= 4'd0;        s3_idx_r[1] <= 4'd0;
        end else if (vld_pipe[1]) begin
            s3_met_r[0] <= s3_met_w[0]; s3_met_r[1] <= s3_met_w[1];
            s3_idx_r[0] <= s3_idx_w[0]; s3_idx_r[1] <= s3_idx_w[1];
        end
    end

    // ---- Stage4: 2 → 1 ----
    wire [ABW-1:0] f_met_w;
    wire [3:0]     f_idx_w;
    reg  [3:0]     f_idx_r;
    disp_2ch_max #(.W(ABW)) u_s4 (
        .m0(s3_met_r[0]), .i0(s3_idx_r[0]),
        .m1(s3_met_r[1]), .i1(s3_idx_r[1]),
        .mo(f_met_w), .io(f_idx_w)
    );

    always @(posedge clk) begin
        if (!rst_n)              f_idx_r <= 4'd0;
        else if (vld_pipe[2])    f_idx_r <= f_idx_w;
    end

    // ---- 输出（valid 与数据同拍, 整体晚 last_chip 4 拍）----
    assign sym    = f_idx_r;
    assign sym_dv = vld_pipe[3];

    // ---------------- 累加器 / 片计数（同 oct8） ----------------
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


// 2 通道 max 选择器：平局取左（左子树索引恒小 → 与原版"严格大于才更新"等价）
module disp_2ch_max #(
    parameter W = 18
) (
    input  wire [W-1:0] m0,
    input  wire [3:0]   i0,
    input  wire [W-1:0] m1,
    input  wire [3:0]   i1,
    output wire [W-1:0] mo,
    output wire [3:0]   io
);
    wire take0 = (m0 >= m1);
    assign mo = take0 ? m0 : m1;
    assign io = take0 ? i0 : i1;
endmodule
