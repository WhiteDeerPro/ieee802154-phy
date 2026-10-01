// rx_dual_wrap.sv —— rx_dual 的波形包装（透传 + VCD dump, 供 gtkwave/Verdi）
// ---------------------------------------------------------------------------
// 共享定时恢复版本：观察点全部由 rx_dual 直接引出（含 rot_a_*/rot_b_* 与定时信号），
// 不再需要层次探针。
//
// 建议观察：
//   .frame_start / .locked_phase      —— 共享前端的定界与相位（**只有一套**）
//   .rot_a_i/q vs .rot_b_i/q          —— 两通道消旋输出（旋性假设不同 → 形态不同）
//   .fcs_ok_a / .fcs_ok_b / .any_fcs_ok
//
// 生成: tb/rx_dual/run.py    查看: gtkwave tb/rx_dual/sim_build/rx_dual.vcd
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
    input  wire [47:0]        ph_thresh,
    input  wire [47:0]        sfd_thresh,
    input  wire               ext_lock_en,
    input  wire [3:0]         ext_lock_phase,
    input  wire               rot_load,
    input  wire signed [PW-1:0] phase_inc_chip_a,
    input  wire [PW-1:0]        phase_off_a,
    input  wire signed [PW-1:0] phase_inc_chip_b,
    input  wire [PW-1:0]        phase_off_b,
    output wire [7:0]         data_a,
    output wire               data_valid_a,
    output wire [7:0]         psdu_len_a,
    output wire               fcs_ok_a,
    output wire               frame_done_a,
    output wire [7:0]         data_b,
    output wire               data_valid_b,
    output wire [7:0]         psdu_len_b,
    output wire               fcs_ok_b,
    output wire               frame_done_b,
    output wire [3:0]         phase_out,
    output wire               detect,
    output wire signed [W-1:0] rot_a_i,
    output wire signed [W-1:0] rot_a_q,
    output wire signed [W-1:0] rot_b_i,
    output wire signed [W-1:0] rot_b_q,
    output wire               rot_a_dv,
    output wire               rot_b_dv,
    output wire               any_fcs_ok
);
    rx_dual #(.W(W), .PW(PW)) dut (
        .clk(clk), .rst_n(rst_n),
        .adc_i(adc_i), .adc_q(adc_q), .adc_dv(adc_dv),
        .ph_thresh(ph_thresh), .sfd_thresh(sfd_thresh),
        .ext_lock_en(ext_lock_en), .ext_lock_phase(ext_lock_phase),
        .rot_load(rot_load),
        .phase_inc_chip_a(phase_inc_chip_a), .phase_off_a(phase_off_a),
        .phase_inc_chip_b(phase_inc_chip_b), .phase_off_b(phase_off_b),
        .data_a(data_a), .data_valid_a(data_valid_a),
        .psdu_len_a(psdu_len_a), .fcs_ok_a(fcs_ok_a), .frame_done_a(frame_done_a),
        .data_b(data_b), .data_valid_b(data_valid_b),
        .psdu_len_b(psdu_len_b), .fcs_ok_b(fcs_ok_b), .frame_done_b(frame_done_b),
        .phase_out(phase_out), .detect(detect),
        .rot_a_i(rot_a_i), .rot_a_q(rot_a_q),
        .rot_b_i(rot_b_i), .rot_b_q(rot_b_q),
        .rot_a_dv(rot_a_dv), .rot_b_dv(rot_b_dv),
        .any_fcs_ok(any_fcs_ok)
    );

    initial begin
        $dumpfile("rx_dual.vcd");
        $dumplimit(30000000);
        $dumpvars(1, rx_dual_wrap);       // 顶层端口（含 rot_a/b 与相位）
        // 诊断探针：共享前端码片 + 各通道 SFD 定界 + 解扩符号
        $dumpvars(0, dut.u_fe.chip_dv, dut.u_be_a.fs_ch, dut.u_be_b.fs_ch,
                  dut.u_be_a.u_desp.sym, dut.u_be_a.u_desp.sym_dv,
                  dut.u_be_a.u_desp.chip_cnt,
                  dut.u_be_b.u_desp.sym, dut.u_be_b.u_desp.sym_dv);
    end
endmodule
