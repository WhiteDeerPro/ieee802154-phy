// rx_backend.sv —— RX 执行段（消旋 → 同步 → 解扩 → 解帧），可多实例并行
// ---------------------------------------------------------------------------
// 切分线在**匹配滤波输出**：前端（ADC/MF、以及可选的估计器）共享一份，
// 执行段每通道一份 —— 这是"多假设并行接收"的自然形态（docs/16 §8.5）：
//
//     共享前端 ──┬── rx_backend(phase_inc = A) ──> 码流 A
//   (ADC/MF/估计)└── rx_backend(phase_inc = B) ──> 码流 B
//
// 与 rx_top 的分工：
//   rx_top     = 共享前端 + 估计/触发（自包含模式）+ 本模块
//   rx_dual    = 共享前端 + 两个本模块（外部参数模式，各带自己的旋性假设）
//
// 本模块**不含估计器**：phase_inc 由外部（上层/前端估计）给定 —— 这正是
// "单元只做执行"的边界（docs/12）。ext_lock 通道透传（定时也可外置）。
// ---------------------------------------------------------------------------
`timescale 1ns/1ps
module rx_backend #(
    parameter W  = 21,               // 匹配滤波输出位宽
    parameter PW = 24,               // 相位定点位宽 (满量程 2π)
    // 同步器形态: 0 = preamble_sync（16 候选扫描, 自包含, ~43 kbit）
    //             1 = preamble_lock（定时外置, 无扫描, ~3 kbit）
    // 对应 docs/16 §8.5 的"同步器极简化"；两者功能等价（去交错 + SFD 定界）。
    parameter SYNC_DIRECT = 1'b0
) (
    input  wire               clk,
    input  wire               rst_n,
    // ---- 匹配滤波输出（来自共享前端）----
    input  wire signed [W-1:0] mf_i,
    input  wire signed [W-1:0] mf_q,
    input  wire               mf_dv,
    // ---- 消旋参数（由上层或前端估计决定）----
    input  wire signed [PW-1:0] phase_inc,   // 每采样相位增量（满量程 2π）
    input  wire [PW-1:0]        phase_off,   // 帧起点相位（默认 0）
    input  wire               rot_load,      // 单拍脉冲: 相位累加器归零
    // ---- 外部定时通道（可选, 见 docs/16 §8）----
    input  wire               ext_lock_en,
    input  wire [3:0]         ext_lock_phase,
    // ---- 门限 ----
    input  wire [47:0]        ph_thresh,
    input  wire [47:0]        sfd_thresh,
    // ---- 输出 ----
    output wire               detect,
    output wire [3:0]         locked_phase,
    output wire               frame_start,
    output wire               busy,
    output wire [7:0]         data_out,
    output wire               data_valid,
    output wire [7:0]         psdu_len,
    output wire               fcs_ok,
    output wire               frame_done
);
    // ---------------- 消旋 ----------------
    wire signed [W-1:0] rot_i, rot_q;
    wire                rot_dv;
    cfo_rot #(.W(W)) u_rot (
        .clk(clk), .rst_n(rst_n),
        .i_in(mf_i), .q_in(mf_q), .dv_in(mf_dv),
        .load(rot_load),
        .phase_inc(phase_inc),
        .phase_off(phase_off),
        .i_out(rot_i), .q_out(rot_q), .dv_out(rot_dv)
    );

    // ---------------- 同步 / 解扩 / 解帧 ----------------
    wire signed [20:0] chip_i, chip_q;
    wire               chip_dv;
    wire               fd_int;        // deframer → sync 的帧尾闭环

    generate
        if (SYNC_DIRECT) begin : g_direct
            // 精简路径: 定时已外置（phase 由上层/前级给定, 每帧更新）
            // 不含 16 候选扫描 —— 面积 ~3 kbit（SFD 窗为主）
            assign detect       = ext_lock_en;          // 相位已给定 → 视为常锁定
            assign locked_phase = ext_lock_phase;
            preamble_lock #(.W(W)) u_lock (
                .clk(clk), .rst_n(rst_n),
                .i_in(rot_i), .q_in(rot_q), .dv_in(rot_dv),
                .phase(ext_lock_phase),
                .sfd_thresh(sfd_thresh),
                .frame_done(fd_int),
                .chip_i(chip_i), .chip_q(chip_q), .chip_dv(chip_dv),
                .frame_start(frame_start)
            );
        end else begin : g_scan
            // 完整路径: 16 候选扫描（自包含, 自己估定时）
            preamble_sync #(.W(W)) u_sync (
                .clk(clk), .rst_n(rst_n),
                .i_in(rot_i), .q_in(rot_q), .dv_in(rot_dv),
                .ph_thresh(ph_thresh), .sfd_thresh(sfd_thresh),
                .frame_done(fd_int),
                .ext_lock_en(ext_lock_en), .ext_lock_phase(ext_lock_phase),
                .scan_restart(1'b0),
                .chip_i(chip_i), .chip_q(chip_q), .chip_dv(chip_dv),
                .detect(detect), .frame_start(frame_start), .locked_phase(locked_phase)
            );
        end
    endgenerate

    wire [3:0] sym;
    wire       sym_dv;
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
        .frame_done(fd_int), .busy(busy)
    );

    assign frame_done = fd_int;
endmodule
