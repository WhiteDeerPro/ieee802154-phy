// rx_only_top.sv —— RX 全程验证台顶层（只 RX: adc 由 tb 直接注入）。
// 配置 = zigbee_top 同源: RST_EN=1, A/B 消旋 ±100k, 阈值现役, VSHIFT=8 默认。
`timescale 1ns/1ps
module rx_only_top (
    input  wire               clk,
    input  wire               rst_n,
    input  wire signed [11:0] adc_i,
    input  wire signed [11:0] adc_q,
    input  wire               adc_dv,
    output wire [7:0]         data,
    output wire               data_valid,
    output wire [7:0]         psdu_len,
    output wire               fcs_ok,
    output wire               frame_done,
    output wire               any_fcs_ok,
    output wire [3:0]         phase_out,
    output wire               detect
);
    localparam signed [23:0] INC_CFO = (1 << 24) * 100_000 / 16_000_000;  // ≈104857

    rx_dual #(.RST_EN(1'b1)) u_rx (
        .clk(clk), .rst_n(rst_n),
        .adc_i(adc_i), .adc_q(adc_q), .adc_dv(adc_dv),
        .ph_thresh(48'd200_000_000_000),
        .sfd_thresh(48'd30_000_000_000_000),
        .sfd_norm_th(16'd90),
        .wake_dly(17'd0),
        .wake_clr(1'b0),
        .pbuf_addr(12'd0), .pbuf_clr(1'b0),
        .pbuf_i(), .pbuf_q(), .pbuf_done(),
        .ext_lock_en(1'b0), .ext_lock_phase(4'd0),
        .rot_load(1'b0),
        .phase_inc_chip_a(INC_CFO), .phase_off_a(24'd0),
        .phase_inc_chip_b(-INC_CFO), .phase_off_b(24'd0),
        .data_a(data), .data_valid_a(data_valid),
        .psdu_len_a(psdu_len), .fcs_ok_a(fcs_ok), .frame_done_a(frame_done),
        .data_b(), .data_valid_b(), .psdu_len_b(), .fcs_ok_b(), .frame_done_b(),
        .phase_out(phase_out), .detect(detect),
        .rot_a_i(), .rot_a_q(), .rot_a_dv(),
        .rot_b_i(), .rot_b_q(), .rot_b_dv(),
        .any_fcs_ok(any_fcs_ok)
    );
endmodule
