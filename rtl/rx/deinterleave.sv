// deinterleave.sv —— 持续去交错器（按给定相位抽取码片, 与旋性无关）
// ---------------------------------------------------------------------------
// 用途：共享前端里, 把"抽样→码片"这一步与"估计相位"解耦。
//   preamble_sync 只在锁定态输出码片, 且未消旋下 SFD 不触发会周期性超时重扫
//   → 码片流断续。本模块用**固定的外给相位**做去交错, 持续输出、不中断。
//
// 相位语义（与 preamble_sync 一致）：phase = 偶片峰在 16 采样周期内的位置；
// 奇片峰 = (phase+12)%16, 采样时做 -j 旋转（I'=Q, Q'=-I）。
// ---------------------------------------------------------------------------
`timescale 1ns/1ps
module deinterleave #(
    parameter W = 21
) (
    input  wire               clk,
    input  wire               rst_n,
    input  wire signed [W-1:0] i_in,
    input  wire signed [W-1:0] q_in,
    input  wire               dv_in,
    input  wire [3:0]         phase,
    output reg  signed [W-1:0] chip_i,
    output reg  signed [W-1:0] chip_q,
    output reg                chip_dv
);
    reg [15:0] smp_cnt;

    wire [3:0] s16  = smp_cnt[3:0];
    wire       even = (s16 == phase);             // 偶片峰位置 = phase
    wire       odd  = ((s16 + 4'd4) == phase);    // 奇片峰 = (phase+12)%16

    always @(posedge clk) begin
        if (!rst_n) begin
            smp_cnt <= 16'd0;
            chip_i  <= {W{1'b0}};
            chip_q  <= {W{1'b0}};
            chip_dv <= 1'b0;
        end else if (dv_in) begin
            smp_cnt <= smp_cnt + 16'd1;
            chip_dv <= even | odd;
            if (even) begin
                chip_i <= i_in;                   // 偶片: I/Q 原值
                chip_q <= q_in;
            end else if (odd) begin
                chip_i <= q_in;                   // 奇片: I'=Q, Q'=-I
                chip_q <= ~i_in + 1'b1;
            end
        end
    end
endmodule
