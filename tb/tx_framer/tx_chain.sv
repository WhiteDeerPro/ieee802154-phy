// tx_chain: tx_framer + oqpsk_modulator 链级封装 (testbench 顶层)
`timescale 1ns/1ps
module tx_chain (
    input  wire               clk,
    input  wire               rst_n,
    input  wire               ce_2m,
    input  wire               start,
    input  wire [7:0]         len,
    input  wire [7:0]         data_in,
    output wire               data_ready,
    output wire signed [11:0] i_out,
    output wire signed [11:0] q_out,
    output wire               busy,
    output wire               done
);
    wire [3:0] sym;
    wire sym_valid, sym_ready;

    tx_framer u_framer (
        .clk(clk), .rst_n(rst_n),
        .len(len), .start(start), .ce_2m(ce_2m),
        .data_in(data_in), .data_valid(1'b0), .data_ready(data_ready),
        .sym(sym), .sym_valid(sym_valid), .sym_ready(sym_ready),
        .busy(busy), .done(done)
    );

    oqpsk_modulator u_mod (
        .clk(clk), .rst_n(rst_n), .ce_2m(ce_2m),
        .sym(sym), .sym_valid(sym_valid), .sym_ready(sym_ready),
        .i_out(i_out), .q_out(q_out), .sample_dv()
    );
endmodule
