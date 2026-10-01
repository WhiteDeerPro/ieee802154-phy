// rx_chain_e2e: RX 全链端到端封装 (testbench 顶层) —— 与 rx_chain 相比补上 rx_matched_filter
// ADC(12bit 复基带采样) → MF → preamble_sync → despreader → rx_deframer → PSDU 字节流
`timescale 1ns/1ps
module rx_chain_e2e (
    input  wire                    clk,
    input  wire                    rst_n,
    input  wire signed [11:0]      i_in,        // ADC 12bit 复基带采样 @16Msps
    input  wire signed [11:0]      q_in,
    input  wire                    dv_in,
    input  wire [47:0]             ph_thresh,
    input  wire [47:0]             sfd_thresh,
    output wire                    detect,
    output wire [3:0]             locked_phase,
    output wire [7:0]              data_out,
    output wire                    data_valid,
    output wire [7:0]              psdu_len,
    output wire                    fcs_ok,
    output wire                    frame_done
);
    wire signed [20:0] mf_i, mf_q;
    wire               mf_dv;

    rx_matched_filter u_mf (
        .clk(clk), .rst_n(rst_n),
        .i_in(i_in), .q_in(q_in), .dv_in(dv_in),
        .i_out(mf_i), .q_out(mf_q), .dv_out(mf_dv)
    );

    wire signed [20:0] chip_i, chip_q;
    wire               chip_dv, frame_start;
    wire [3:0]         sym;
    wire               sym_dv;

    preamble_sync #(.W(21)) u_sync (
        .clk(clk), .rst_n(rst_n),
        .i_in(mf_i), .q_in(mf_q), .dv_in(mf_dv),
        .ph_thresh(ph_thresh), .sfd_thresh(sfd_thresh),
        .frame_done(frame_done),
        .ext_lock_en(1'b0), .ext_lock_phase(4'd0),
        .chip_i(chip_i), .chip_q(chip_q), .chip_dv(chip_dv),
        .detect(detect), .frame_start(frame_start), .locked_phase(locked_phase)
    );

    despreader #(.W(12)) u_desp (
        .clk(clk), .rst_n(rst_n),
        .chip_i(chip_i[18:7]), .chip_q(chip_q[18:7]), .chip_dv(chip_dv),
        .frame_start(frame_start),
        .sym(sym), .sym_dv(sym_dv)
    );

    rx_deframer u_defr (
        .clk(clk), .rst_n(rst_n),
        .sym(sym), .sym_dv(sym_dv), .frame_start(frame_start),
        .data_out(data_out), .data_valid(data_valid),
        .psdu_len(psdu_len), .fcs_ok(fcs_ok),
        .frame_done(frame_done), .busy()
    );
endmodule
