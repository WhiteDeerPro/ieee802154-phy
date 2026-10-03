// cmp_top.sv —— despreader 对照台顶层（原版 + TDM 并排, 同一激励）。
// cocotb 顶层（句柄 dut.u_ref / dut.u_tdm）; 片节奏 = 每片 8 拍（TDM 设计假设）。
`timescale 1ns/1ps
module cmp_top (
    input  wire               clk,
    input  wire               rst_n,
    input  wire signed [11:0] chip_i,
    input  wire signed [11:0] chip_q,
    input  wire               chip_dv,
    input  wire               frame_start,
    output wire [3:0]         sym_ref,
    output wire               sym_dv_ref,
    output wire [3:0]         sym_tdm,
    output wire               sym_dv_tdm
);
    despreader u_ref (
        .clk(clk), .rst_n(rst_n),
        .chip_i(chip_i), .chip_q(chip_q), .chip_dv(chip_dv),
        .frame_start(frame_start),
        .sym(sym_ref), .sym_dv(sym_dv_ref)
    );
    despreader_tdm u_tdm (
        .clk(clk), .rst_n(rst_n),
        .chip_i(chip_i), .chip_q(chip_q), .chip_dv(chip_dv),
        .frame_start(frame_start),
        .sym(sym_tdm), .sym_dv(sym_dv_tdm)
    );
endmodule
