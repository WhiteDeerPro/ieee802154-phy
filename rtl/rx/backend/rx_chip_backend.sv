// rx_chip_backend.sv —— 码片级执行段：消旋 → **SFD 定界** → 解扩 → 解帧
// ---------------------------------------------------------------------------
// 多通道架构的每通道部分（docs/16 §8.5"共享定时恢复"的修正版）：
//
//   ADC → rx_frontend（MF + 相位恢复 + 去交错, **共享一份**）
//            ──┬── 本模块(旋性 A): 消旋 → SFD → 解扩 → 解帧
//              └── 本模块(旋性 B): 消旋 → SFD → 解扩 → 解帧
//
// **为什么 SFD 不能像相位那样共享**：SFD 是 64 片**相干**相关，要求窗内相位一致
// → 必须跑在已消旋的码片上；而消旋是每通道的（旋性假设不同）。
// 相位/去交错则与旋性无关（帧到达的定时对所有通道相同）→ 放在共享前端。
//
// phase_inc 语义：**每码片**的相位增量（满量程 2π）。若上层给出的值是"每采样"的
// （如 cfo_est 的 phase_inc）, 传入前乘 8（SPS）。
// ---------------------------------------------------------------------------
`timescale 1ns/1ps
module rx_chip_backend #(
    parameter W  = 21,
    parameter PW = 24
) (
    input  wire               clk,
    input  wire               rst_n,
    // ---- 共享前端：去交错后的码片流（**未消旋**）----
    input  wire signed [W-1:0] chip_i,
    input  wire signed [W-1:0] chip_q,
    input  wire               chip_dv,
    // ---- 消旋参数（码片级）----
    input  wire signed [PW-1:0] phase_inc,
    input  wire [PW-1:0]        phase_off,
    input  wire               rot_load,
    // ---- SFD 门限（本通道定界用）----
    input  wire [47:0]        sfd_thresh,
    input  wire [15:0]        sfd_norm_th = 16'd0,   // 归一化门限 Q8（0=用绝对门限）
    // ---- 输出 ----
    output wire [7:0]         data_out,
    output wire               data_valid,
    output wire [7:0]         psdu_len,
    output wire               fcs_ok,
    output wire               frame_done,
    output wire               busy,
    output wire               frame_start,     // 本通道的定界（观测）
    // ---- 观测：消旋输出 ----
    output wire signed [W-1:0] rot_i,
    output wire signed [W-1:0] rot_q,
    output wire               rot_dv
);
    // ---------------- 消旋（码片级：2 MHz）----------------
    cfo_rot #(.W(W)) u_rot (
        .clk(clk), .rst_n(rst_n),
        .i_in(chip_i), .q_in(chip_q), .dv_in(chip_dv),
        .load(rot_load),
        .phase_inc(phase_inc),
        .phase_off(phase_off),
        .i_out(rot_i), .q_out(rot_q), .dv_out(rot_dv)
    );

    // ---------------- SFD 定界（消旋后, 每通道一份）----------------
    wire               fd_int;
    wire               fs_ch;
    sfd_detect #(.W(W)) u_sfd (
        .clk(clk), .rst_n(rst_n),
        .chip_i(rot_i), .chip_q(rot_q), .chip_dv(rot_dv),
        .sfd_thresh(sfd_thresh),
        .norm_th(sfd_norm_th),
        .frame_done(fd_int),
        .frame_start(fs_ch)
    );
    assign frame_start = fs_ch;
    assign frame_done  = fd_int;      // ⚠ 曾经漏接: 端口悬空 → 多帧统计拿不到帧尾

    // ---------------- 解扩 ----------------
    // 变体开关: 构建时 +define+DESP_OCT8 换装 8 边形度量解扩（0 乘法, 见
    // rtl/rx/variants/despreader_oct8.sv）。默认（未定义）= 原版精确平方, 与既有
    // 回归逐位等价。
    wire [3:0] sym;
    wire       sym_dv;
`ifdef DESP_OCT8
    initial $display("[rx_chip_backend] despreader = oct8 (0-mul)");
    despreader_oct8 #(.W(12)) u_desp (
        .clk(clk), .rst_n(rst_n),
        .chip_i(rot_i[W-3:W-14]), .chip_q(rot_q[W-3:W-14]), .chip_dv(rot_dv),
        .frame_start(fs_ch),
        .sym(sym), .sym_dv(sym_dv)
    );
`else
    despreader #(.W(12)) u_desp (
        .clk(clk), .rst_n(rst_n),
        .chip_i(rot_i[W-3:W-14]), .chip_q(rot_q[W-3:W-14]), .chip_dv(rot_dv),   // 取中间 12 位 (峰值≈2^11; W=21 时等价旧 [18:7])
        .frame_start(fs_ch),
        .sym(sym), .sym_dv(sym_dv)
    );
`endif

    // ---------------- 解帧 ----------------
    rx_deframer u_defr (
        .clk(clk), .rst_n(rst_n),
        .sym(sym), .sym_dv(sym_dv), .frame_start(fs_ch),
        .data_out(data_out), .data_valid(data_valid),
        .psdu_len(psdu_len), .fcs_ok(fcs_ok),
        .frame_done(fd_int), .busy(busy)
    );
endmodule
