// rx_dual.sv —— 双通道并行接收：**只有管线中的执行段并行**
// ---------------------------------------------------------------------------
// 与上一版（例化两个完整 rx_top, 连 MF 都各一份）的区别：本版**共享前端**。
//
//     ADC ──> rx_matched_filter（共享, 一份）──┬── rx_backend(phase_inc_a) ──> 码流 A
//                                              └── rx_backend(phase_inc_b) ──> 码流 B
//
// 这正是"管线中的特定一块并行化"：前端（ADC/MF）一份，执行段（消旋+同步+解扩+
// 解帧）每通道一份 —— 假设殊异 ⇒ 旋性不同 ⇒ 必须各持消旋器（docs/16 §8.4/§8.5）。
// 后级按 FCS 选优（两条都过 = 同一帧被两个假设都解出，需上层去重）。
//
// 观察点（VCD/FSDB）：`u_mf` 之后的 mf_i/mf_q 只有一份（证明前端共享），
// 而 u_be_a.u_rot / u_be_b.u_rot 的输出因 phase_inc 不同而呈现不同的旋转特征。
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
    input  wire               rot_load,     // 单拍脉冲: 相位累加器归零 (可接 0)
    input  wire [47:0]        ph_thresh,
    input  wire [47:0]        sfd_thresh,
    // ---- 通道 A 参数（旋性假设 A）----
    input  wire signed [PW-1:0] phase_inc_a,
    input  wire [PW-1:0]        phase_off_a,
    input  wire               ext_lock_en_a,
    input  wire [3:0]         ext_lock_phase_a,
    // ---- 通道 B 参数（旋性假设 B）----
    input  wire signed [PW-1:0] phase_inc_b,
    input  wire [PW-1:0]        phase_off_b,
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
    output wire               any_fcs_ok
);
    // ---------------- 共享前端：ADC → 匹配滤波（**一份**）----------------
    wire signed [W-1:0] mf_i, mf_q;
    wire                mf_dv;
    rx_matched_filter u_mf (
        .clk(clk), .rst_n(rst_n),
        .i_in(adc_i), .q_in(adc_q), .dv_in(adc_dv),
        .i_out(mf_i), .q_out(mf_q), .dv_out(mf_dv)
    );

    // ---------------- 通道 A：执行段（自带消旋器；完整同步器, 16 候选扫描）----------------
    rx_backend #(.W(W), .PW(PW), .SYNC_DIRECT(1'b0)) u_be_a (
        .clk(clk), .rst_n(rst_n),
        .mf_i(mf_i), .mf_q(mf_q), .mf_dv(mf_dv),
        .phase_inc(phase_inc_a), .phase_off(phase_off_a),
        .rot_load(rot_load),
        .ext_lock_en(ext_lock_en_a), .ext_lock_phase(ext_lock_phase_a),
        .ph_thresh(ph_thresh), .sfd_thresh(sfd_thresh),
        .detect(detect_a), .locked_phase(locked_phase_a),
        .frame_start(), .busy(),
        .data_out(data_a), .data_valid(data_valid_a),
        .psdu_len(psdu_len_a), .fcs_ok(fcs_ok_a), .frame_done(frame_done_a)
    );

    // ---------------- 通道 B：执行段（自带消旋器；**精简同步器**, 定时外置无扫描）----------------
    // 与通道 A 形成对比: 同样的执行功能, 但同步器从 16 候选扫描换成
    // preamble_lock（相位由 ext_lock_phase 给定）—— docs/16 §8.5。
    rx_backend #(.W(W), .PW(PW), .SYNC_DIRECT(1'b1)) u_be_b (
        .clk(clk), .rst_n(rst_n),
        .mf_i(mf_i), .mf_q(mf_q), .mf_dv(mf_dv),
        .phase_inc(phase_inc_b), .phase_off(phase_off_b),
        .rot_load(rot_load),
        .ext_lock_en(ext_lock_en_b), .ext_lock_phase(ext_lock_phase_b),
        .ph_thresh(ph_thresh), .sfd_thresh(sfd_thresh),
        .detect(detect_b), .locked_phase(locked_phase_b),
        .frame_start(), .busy(),
        .data_out(data_b), .data_valid(data_valid_b),
        .psdu_len(psdu_len_b), .fcs_ok(fcs_ok_b), .frame_done(frame_done_b)
    );

    assign any_fcs_ok = fcs_ok_a | fcs_ok_b;
endmodule
