// rx_matched_filter.sv —— I/Q 两路半正弦匹配滤波 (全精度 21bit 输出)
// 输入: 16 Msps 复基带定点采样 (12bit); 输出: MF 后 I/Q (21bit) + dv
// 与 model/phy_802154.py 整数卷积 (fixed_half_sine) bit-true。
`timescale 1ns/1ps
module rx_matched_filter #(
    parameter X_W = 12,
    parameter Y_W = 21
) (
    input  wire                      clk,
    input  wire                      rst_n,
    input  wire signed [X_W-1:0]     i_in,
    input  wire signed [X_W-1:0]     q_in,
    input  wire                      dv_in,
    output wire signed [Y_W-1:0]     i_out,
    output wire signed [Y_W-1:0]     q_out,
    output wire                      dv_out
);
    half_sine_fir #(.X_W(X_W), .Y_W(Y_W)) u_fir_i (
        .clk(clk), .rst_n(rst_n), .x(i_in), .dv_in(dv_in), .y(i_out), .dv_out(dv_out)
    );
    half_sine_fir #(.X_W(X_W), .Y_W(Y_W)) u_fir_q (
        .clk(clk), .rst_n(rst_n), .x(q_in), .dv_in(dv_in), .y(q_out), .dv_out()
    );
endmodule
