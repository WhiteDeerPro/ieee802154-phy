// rx_dual.sv —— 双通道并行接收：共享前端（MF + 相位 + 持续去交错）
// ---------------------------------------------------------------------------
//     ADC ──> rx_frontend（MF + 相位恢复 + 去交错, **一份**）
//                 ──┬── rx_chip_backend(旋性 A): 消旋 → SFD → 解扩 → 解帧 ──> 码流 A
//                   └── rx_chip_backend(旋性 B): 消旋 → SFD → 解扩 → 解帧 ──> 码流 B
//
// 分界依据（docs/16 §8.5）：
//   · **相位/去交错**：与旋性无关（帧到达定时对所有通道相同）→ 共享;
//   · **SFD 定界**：64 片相干相关, 必须在消旋之后 → 每通道一份;
//   · **消旋**：每通道自己的旋性假设（码片级, 2 MHz, 比采样级省 8 倍）。
//
// phase_inc_chip_* 语义：**每码片**相位增量（满量程 2π）；若手头是"每采样"值,
// 传入前乘 8（SPS）。
// ---------------------------------------------------------------------------
`timescale 1ns/1ps
module rx_dual #(
    parameter W  = 21,
    parameter PW = 24,
    parameter SYNC_DIRECT = 1'b0,     // 0: 扫描取相位; 1: 相位外置（ext_lock_phase）
    parameter RST_EN = 1'b0           // 1: 帧到达 → 扫描重启（透传 rx_frontend）
) (
    input  wire               clk,
    input  wire               rst_n,
    // ---- ADC（两通道共享）----
    input  wire signed [11:0] adc_i,
    input  wire signed [11:0] adc_q,
    input  wire               adc_dv,
    // ---- 共享配置 ----
    input  wire [47:0]        ph_thresh,
    input  wire [47:0]        sfd_thresh,
    input  wire [15:0]        sfd_norm_th = 16'd0,   // SFD 归一化门限 Q8（0=绝对）
    // ---- 前导缓冲读口（I-13, 透传共享前端）----
    input  wire [11:0]         pbuf_addr = 12'd0,
    input  wire                pbuf_clr  = 1'b0,
    output wire signed [W-1:0] pbuf_i,
    output wire signed [W-1:0] pbuf_q,
    output wire                pbuf_done,
    input  wire               ext_lock_en,
    input  wire [3:0]         ext_lock_phase,
    input  wire               rot_load,
    // ---- 通道 A / B 消旋参数（**码片级**）----
    input  wire signed [PW-1:0] phase_inc_chip_a,
    input  wire [PW-1:0]        phase_off_a,
    input  wire signed [PW-1:0] phase_inc_chip_b,
    input  wire [PW-1:0]        phase_off_b,
    // ---- 通道 A 输出 ----
    output wire [7:0]         data_a,
    output wire               data_valid_a,
    output wire [7:0]         psdu_len_a,
    output wire               fcs_ok_a,
    output wire               frame_done_a,
    // ---- 通道 B 输出 ----
    output wire [7:0]         data_b,
    output wire               data_valid_b,
    output wire [7:0]         psdu_len_b,
    output wire               fcs_ok_b,
    output wire               frame_done_b,
    // ---- 共享前端观测 ----
    output wire [3:0]         phase_out,
    output wire               detect,
    // ---- 消旋输出观测（两通道波形对比）----
    output wire signed [W-1:0] rot_a_i, rot_a_q,
    output wire signed [W-1:0] rot_b_i, rot_b_q,
    output wire               rot_a_dv, rot_b_dv,
    // ---- 汇总 ----
    output wire               any_fcs_ok
);
    // ---------------- 共享前端 ----------------
    wire signed [W-1:0] chip_i, chip_q;
    wire                chip_dv;
    wire                fd_a, fd_b;

    rx_frontend #(.W(W), .SYNC_DIRECT(SYNC_DIRECT), .RST_EN(RST_EN)) u_fe (
        .clk(clk), .rst_n(rst_n),
        .adc_i(adc_i), .adc_q(adc_q), .adc_dv(adc_dv),
        .ph_thresh(ph_thresh), .sfd_thresh(sfd_thresh),
        .ext_lock_en(ext_lock_en), .ext_lock_phase(ext_lock_phase),
        .pbuf_addr(pbuf_addr), .pbuf_clr(pbuf_clr),
        .pbuf_i(pbuf_i), .pbuf_q(pbuf_q), .pbuf_done(pbuf_done),
        .chip_i(chip_i), .chip_q(chip_q), .chip_dv(chip_dv),
        .phase_out(phase_out), .detect(detect),
        .mf_i(), .mf_q(), .mf_dv()
    );

    // ---------------- 通道 A / B ----------------
    rx_chip_backend #(.W(W), .PW(PW)) u_be_a (
        .clk(clk), .rst_n(rst_n),
        .chip_i(chip_i), .chip_q(chip_q), .chip_dv(chip_dv),
        .phase_inc(phase_inc_chip_a), .phase_off(phase_off_a),
        .rot_load(rot_load), .sfd_thresh(sfd_thresh),
        .sfd_norm_th(sfd_norm_th),
        .data_out(data_a), .data_valid(data_valid_a),
        .psdu_len(psdu_len_a), .fcs_ok(fcs_ok_a),
        .frame_done(fd_a), .busy(), .frame_start(),
        .rot_i(rot_a_i), .rot_q(rot_a_q), .rot_dv(rot_a_dv)
    );

    rx_chip_backend #(.W(W), .PW(PW)) u_be_b (
        .clk(clk), .rst_n(rst_n),
        .chip_i(chip_i), .chip_q(chip_q), .chip_dv(chip_dv),
        .phase_inc(phase_inc_chip_b), .phase_off(phase_off_b),
        .rot_load(rot_load), .sfd_thresh(sfd_thresh),
        .sfd_norm_th(sfd_norm_th),
        .data_out(data_b), .data_valid(data_valid_b),
        .psdu_len(psdu_len_b), .fcs_ok(fcs_ok_b),
        .frame_done(fd_b), .busy(), .frame_start(),
        .rot_i(rot_b_i), .rot_q(rot_b_q), .rot_dv(rot_b_dv)
    );

    assign any_fcs_ok = fcs_ok_a | fcs_ok_b;
    assign frame_done_a = fd_a;
    assign frame_done_b = fd_b;
endmodule
