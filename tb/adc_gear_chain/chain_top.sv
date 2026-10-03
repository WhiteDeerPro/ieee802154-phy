// chain_top —— adc_gear_ctrl(决策) + adc_quant_if(执行) 集成顶层（仅接线, 无逻辑）
// 数据流: 事件帧 → gear_ctrl → set 序列 → quant_if → adc_gear/握手
//         反馈:    cur_gear ←────────────── adc_gear
// 监督:   sup_force(决策侧计数清零) 与 force_full(执行侧强制) **同源**——监督层绕过决策层
//         直连执行器（docs/21 §16.3 独立性）。
`timescale 1ns/1ps
module chain_top (
    input  wire       clk,
    input  wire       rst_n,
    // 事件（到决策层）
    input  wire       frame_ev,
    input  wire       snr_valid,
    input  wire [7:0] snr_est,
    input  wire       fcs_ok,
    input  wire       sup_force,     // 监督广播（电平; 与 force_full 同源）
    input  wire       force_full,    // 外部代理（直连执行侧, 电平）
    // 执行侧（到 ADC/RF）
    output wire [2:0] adc_gear,
    output wire       adc_req,
    input  wire       adc_ack,
    output wire       denied
);
    wire [2:0] set_gear;
    wire       set_req, set_low_ok;

    adc_gear_ctrl u_ctrl (
        .clk(clk), .rst_n(rst_n),
        .frame_ev(frame_ev), .snr_valid(snr_valid), .snr_est(snr_est),
        .fcs_ok(fcs_ok), .sup_force(sup_force), .cur_gear(adc_gear),
        .set_gear(set_gear), .set_req(set_req), .set_low_ok(set_low_ok)
    );
    adc_quant_if u_if (
        .clk(clk), .rst_n(rst_n),
        .set_gear(set_gear), .set_req(set_req), .set_low_ok(set_low_ok),
        .set_force_full(force_full),
        .adc_gear(adc_gear), .adc_req(adc_req), .adc_ack(adc_ack),
        .denied(denied)
    );
endmodule
