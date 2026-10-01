// rx_dual_wrap.sv —— rx_dual 的波形包装（透传 + VCD dump）
// ---------------------------------------------------------------------------
// 用途：让 cocotb 测试在不改动的情况下产出波形，供 **gtkwave / Verdi** 查看。
// 背景（tb/runutil.py 的注释）：VCS 下 cocotb 的 waves=True 走 -qwavedb 实测不出
// 文件，可靠做法是**包装层**：只做透传 + $dumpfile/$dumpvars（端口同名同宽）。
//
// 建议观察的信号（层次，见 docs/16 §8.5）：
//   .u_mf.i_out/q_out/dv_out          —— 共享前端的匹配滤波输出（**只有一份**）
//   .u_be_a.u_rot.i_out/q_out         —— 通道 A 的消旋输出（phase_inc_a 决定）
//   .u_be_b.u_rot.i_out/q_out         —— 通道 B 的消旋输出（phase_inc_b 决定）
//   .u_be_a.u_sync.detect / frame_start、u_be_b 同 —— 两通道各自的锁定情况
//   .fcs_ok_a / .fcs_ok_b / .any_fcs_ok
//
// 生成: tb/rx_dual/run.py（见其 build_args）；输出 rx_dual.vcd（在 sim_build 下）
// 查看: gtkwave rx_dual.vcd      或     verdi -ssv rx_dual.vcd
// ---------------------------------------------------------------------------
`timescale 1ns/1ps
module rx_dual_wrap #(
    parameter W  = 21,
    parameter PW = 24
) (
    input  wire               clk,
    input  wire               rst_n,
    input  wire signed [11:0] adc_i,
    input  wire signed [11:0] adc_q,
    input  wire               adc_dv,
    input  wire               rot_load,
    input  wire [47:0]        ph_thresh,
    input  wire [47:0]        sfd_thresh,
    input  wire signed [PW-1:0] phase_inc_a,
    input  wire [PW-1:0]        phase_off_a,
    input  wire               ext_lock_en_a,
    input  wire [3:0]         ext_lock_phase_a,
    input  wire signed [PW-1:0] phase_inc_b,
    input  wire [PW-1:0]        phase_off_b,
    input  wire               ext_lock_en_b,
    input  wire [3:0]         ext_lock_phase_b,
    output wire [7:0]         data_a,
    output wire               data_valid_a,
    output wire [7:0]         psdu_len_a,
    output wire               fcs_ok_a,
    output wire               frame_done_a,
    output wire               detect_a,
    output wire [3:0]         locked_phase_a,
    output wire [7:0]         data_b,
    output wire               data_valid_b,
    output wire [7:0]         psdu_len_b,
    output wire               fcs_ok_b,
    output wire               frame_done_b,
    output wire               detect_b,
    output wire [3:0]         locked_phase_b,
    output wire               any_fcs_ok
);
    rx_dual #(.W(W), .PW(PW)) dut (
        .clk(clk), .rst_n(rst_n),
        .adc_i(adc_i), .adc_q(adc_q), .adc_dv(adc_dv),
        .rot_load(rot_load),
        .ph_thresh(ph_thresh), .sfd_thresh(sfd_thresh),
        .phase_inc_a(phase_inc_a), .phase_off_a(phase_off_a),
        .ext_lock_en_a(ext_lock_en_a), .ext_lock_phase_a(ext_lock_phase_a),
        .phase_inc_b(phase_inc_b), .phase_off_b(phase_off_b),
        .ext_lock_en_b(ext_lock_en_b), .ext_lock_phase_b(ext_lock_phase_b),
        .data_a(data_a), .data_valid_a(data_valid_a),
        .psdu_len_a(psdu_len_a), .fcs_ok_a(fcs_ok_a), .frame_done_a(frame_done_a),
        .detect_a(detect_a), .locked_phase_a(locked_phase_a),
        .data_b(data_b), .data_valid_b(data_valid_b),
        .psdu_len_b(psdu_len_b), .fcs_ok_b(fcs_ok_b), .frame_done_b(frame_done_b),
        .detect_b(detect_b), .locked_phase_b(locked_phase_b),
        .any_fcs_ok(any_fcs_ok)
    );

    // ---- 波形探针（带通道名, VCS 的 VCD 信号名是扁平的, 同名信号难分）----
    wire signed [W-1:0] wav_mf_i   = dut.mf_i;                  // 共享前端（**一份**）
    wire signed [W-1:0] wav_mf_q   = dut.mf_q;
    wire               wav_mf_dv   = dut.mf_dv;
    wire signed [W-1:0] wav_rotA_i = dut.u_be_a.u_rot.i_out;    // 通道 A 消旋输出
    wire signed [W-1:0] wav_rotA_q = dut.u_be_a.u_rot.q_out;
    wire signed [W-1:0] wav_rotB_i = dut.u_be_b.u_rot.i_out;    // 通道 B 消旋输出
    wire signed [W-1:0] wav_rotB_q = dut.u_be_b.u_rot.q_out;
    wire [3:0]         wav_symA   = dut.u_be_a.u_desp.sym;
    wire [3:0]         wav_symB   = dut.u_be_b.u_desp.sym;

    initial begin
        $dumpfile("rx_dual.vcd");
        $dumplimit(30000000);
        // 顶层端口 + 探针（wav_* 带通道名, 这是给波形用的主要观察点）
        $dumpvars(1, rx_dual_wrap);
        // 同步/锁定只取关键信号（逐个列出, 避免拉进 16 候选寄存器阵列）
        $dumpvars(0,
            dut.u_be_a.u_sync.detect, dut.u_be_a.u_sync.frame_start,
            dut.u_be_a.u_sync.locked_phase,
            dut.u_be_b.u_sync.detect, dut.u_be_b.u_sync.frame_start,
            dut.u_be_b.u_sync.locked_phase);
    end
endmodule
