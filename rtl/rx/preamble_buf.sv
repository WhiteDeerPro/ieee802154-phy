// preamble_buf.sv —— 前导缓冲（I-13）：MF 输出的采样级环形缓冲 + 帧到达快照
// ---------------------------------------------------------------------------
// 用途（docs/14 I-13 / docs/12 §2 接口形状）:
//   单元 → 上层: 前导期的 MF 输出软值 + 帧到达标记;
//   上层据此做 FFT 发现（patterns.discover 口径）/ 相位扫描 / 异常帧排查。
// 为什么存**采样级**而非去交错码片（10.5 kbit → ~172 kbit 的面积取舍）:
//   去交错依赖抽样相位——异常帧场景下相位恰恰不可信; 采样级是无损的充分
//   观测, "抽哪个相位/怎么用"全部留给上层（与 patterns.discover 的
//   "按任意 offset 抽取"口径一致）。
// 机制:
//   · 持续环形写（每个 dv_in 一拍），DEPTH=2^N（地址自然回绕）;
//   · trig 上升沿（preamble_detect 电平上升沿 = 帧到达）→ 快照写指针 base;
//   · 收集 POST 拍后 done=1 并**冻结缓冲**（写入停止——保证 done 后读出的
//     3072 个地址永不失效; 上层读走 → clr → 重新武装）;
//   · 读口: rd_addr ∈ [0, PRE+POST) → mem[(base − PRE + rd_addr) mod DEPTH]
//     —— 线性化由硬件完成（上层按时间顺序读, 不需知道回绕）。
// 窗口覆盖: [trig − PRE, trig + POST)。实测帧到达沿早于帧起点约 460 采样,
//   PRE=1024 ⇒ 覆盖帧起点前 ~560 至其后 ~2500 （完整前导 2048 + 余量）。
// ---------------------------------------------------------------------------
`timescale 1ns/1ps
module preamble_buf #(
    parameter W           = 21,
    parameter integer DEPTH = 4096,     // 必须为 2 的幂（地址自然回绕）
    parameter integer PRE   = 1024,     // 触发前保留
    parameter integer POST  = 2048      // 触发后收集
) (
    input  wire                clk,
    input  wire                rst_n,
    input  wire signed [W-1:0] i_in,
    input  wire signed [W-1:0] q_in,
    input  wire                dv_in,
    input  wire                trig,        // 单拍（帧到达沿）
    input  wire                clr,         // 读出后复位
    // 读口（组合读, 硬件线性化）
    input  wire [11:0]         rd_addr,     // [0, PRE+POST)
    output wire signed [W-1:0] rd_i,
    output wire signed [W-1:0] rd_q,
    output wire                done
);
    localparam integer AW = $clog2(DEPTH);

    reg signed [W-1:0] mem_i [0:DEPTH-1];
    reg signed [W-1:0] mem_q [0:DEPTH-1];
    reg [AW-1:0] wr;
    reg [AW-1:0] base;        // trig 拍快照（= 触发后第一个写入位置）
    reg [11:0]   cnt;         // 收集计数
    reg          done_r;
    integer j;

    wire trig_ok = trig && !done_r && (cnt == 12'd0);

    always @(posedge clk) begin
        if (!rst_n) begin
            wr     <= {AW{1'b0}};
            base   <= {AW{1'b0}};
            cnt    <= 12'd0;
            done_r <= 1'b0;
        end else begin
            if (clr) begin
                cnt    <= 12'd0;
                done_r <= 1'b0;
            end
            if (dv_in && !done_r) begin
                mem_i[wr] <= i_in;
                mem_q[wr] <= q_in;
                wr <= wr + 1'b1;
                if (trig_ok) begin
                    base <= wr;                    // 快照
                    cnt  <= 12'd1;
                end else if (cnt != 12'd0) begin
                    if (cnt == POST[11:0]) done_r <= 1'b1;
                    else                   cnt <= cnt + 1'b1;
                end
            end
        end
    end

    // 线性化读: rd_a = base − PRE + rd_addr（2^N 回绕即截断加法）
    wire [AW-1:0] rd_a = base + rd_addr[AW-1:0] - PRE[AW-1:0];
    assign rd_i  = mem_i[rd_a];
    assign rd_q  = mem_q[rd_a];
    assign done  = done_r;
endmodule
