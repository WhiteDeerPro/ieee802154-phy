// rx_dual.sv —— 双通道并行接收（每通道自带消旋器）
// ---------------------------------------------------------------------------
// 动机（docs/16 §8.3）：**"谁规定只能一个消旋器"** —— 不同假设的旋性不同，
// 每通道必须持有自己的消旋器才能同时工作。这是"多假设接收机"的自然形态：
//
//     ADC ──┬── rx_top(A 参数: ext_inc_a / ext_lock_a) ──> 码流 A + fcs_ok_a
//           └── rx_top(B 参数: ext_inc_b / ext_lock_b) ──> 码流 B + fcs_ok_b
//
// 后级按 FCS 选优（两条都过 = 同一帧被两个假设都解出，需上层去重）。
// 通道数 = 分辨单元数（不是设备数）：一群晶振相近的设备共享同一个通道。
//
// 注：当前实现是"两个完整 rx_top"（连 MF 也各一份）—— 先验证形态；
//     **前端共享（MF 单份）**与**同步器极简化（ext_lock 已具备前提）**是
//     后续的面积优化，见 docs/16 §8.3。
// ---------------------------------------------------------------------------
`timescale 1ns/1ps
module rx_dual #(
    parameter W  = 21,
    parameter PW = 24
) (
    input  wire               clk,
    input  wire               rst_n,
    // ---- ADC 接口（两通道共享同一份 IQ）----
    input  wire signed [11:0] adc_i,
    input  wire signed [11:0] adc_q,
    input  wire               adc_dv,
    // ---- 共享配置 ----
    input  wire               cfo_en,
    input  wire               est_start,
    input  wire               rot_load,
    input  wire               trig_ext,
    input  wire [47:0]        ph_thresh,
    input  wire [47:0]        sfd_thresh,
    // ---- 通道 A 参数（外部参数/定时通道）----
    input  wire               ext_inc_en_a,
    input  wire signed [PW-1:0] ext_inc_a,
    input  wire [PW-1:0]        ext_phase_off_a,
    input  wire               ext_lock_en_a,
    input  wire [3:0]         ext_lock_phase_a,
    // ---- 通道 B 参数 ----
    input  wire               ext_inc_en_b,
    input  wire signed [PW-1:0] ext_inc_b,
    input  wire [PW-1:0]        ext_phase_off_b,
    input  wire               ext_lock_en_b,
    input  wire [3:0]         ext_lock_phase_b,
    // ---- 通道 A 输出 ----
    output wire [7:0]         data_a,
    output wire               data_valid_a,
    output wire [7:0]         psdu_len_a,
    output wire               fcs_ok_a,
    output wire               frame_done_a,
    output wire               detect_a,
    output wire [3:0]         locked_phase_a,
    // ---- 通道 B 输出 ----
    output wire [7:0]         data_b,
    output wire               data_valid_b,
    output wire [7:0]         psdu_len_b,
    output wire               fcs_ok_b,
    output wire               frame_done_b,
    output wire               detect_b,
    output wire [3:0]         locked_phase_b,
    // ---- 汇总 ----
    output wire               any_fcs_ok      // 任一通道解出（后级选优的粗信号）
);
    rx_top #(.W(W), .PW(PW)) u_a (
        .clk(clk), .rst_n(rst_n),
        .adc_i(adc_i), .adc_q(adc_q), .adc_dv(adc_dv),
        .cfo_en(cfo_en), .est_start(est_start), .rot_load(rot_load),
        .ext_inc_en(ext_inc_en_a), .ext_inc(ext_inc_a),
        .ext_phase_off(ext_phase_off_a),
        .ext_lock_en(ext_lock_en_a), .ext_lock_phase(ext_lock_phase_a),
        .trig_ext(trig_ext), .ph_thresh(ph_thresh), .sfd_thresh(sfd_thresh),
        .detect(detect_a), .locked_phase(locked_phase_a),
        .frame_start(), .busy(), .pd_det(),
        .data_out(data_a), .data_valid(data_valid_a),
        .psdu_len(psdu_len_a), .fcs_ok(fcs_ok_a), .frame_done(frame_done_a)
    );

    rx_top #(.W(W), .PW(PW)) u_b (
        .clk(clk), .rst_n(rst_n),
        .adc_i(adc_i), .adc_q(adc_q), .adc_dv(adc_dv),
        .cfo_en(cfo_en), .est_start(est_start), .rot_load(rot_load),
        .ext_inc_en(ext_inc_en_b), .ext_inc(ext_inc_b),
        .ext_phase_off(ext_phase_off_b),
        .ext_lock_en(ext_lock_en_b), .ext_lock_phase(ext_lock_phase_b),
        .trig_ext(trig_ext), .ph_thresh(ph_thresh), .sfd_thresh(sfd_thresh),
        .detect(detect_b), .locked_phase(locked_phase_b),
        .frame_start(), .busy(), .pd_det(),
        .data_out(data_b), .data_valid(data_valid_b),
        .psdu_len(psdu_len_b), .fcs_ok(fcs_ok_b), .frame_done(frame_done_b)
    );

    assign any_fcs_ok = fcs_ok_a | fcs_ok_b;
endmodule
