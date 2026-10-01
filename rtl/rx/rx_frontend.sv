// rx_frontend.sv —— 共享前端：MF + 相位恢复 + **持续去交错**（一份）
// ---------------------------------------------------------------------------
// 多通道架构的共享部分（docs/16 §8.5"共享定时恢复"，修正版）：
//
//     ADC → [本模块: MF + 相位恢复 + 去交错] ──┬── rx_chip_backend(旋性 A)
//                                              └── rx_chip_backend(旋性 B)
//
// 为什么只有"相位/去交错"能共享, 而 SFD 不能：
//   · 帧到达的**定时（相位）**对所有通道相同（同一份 IQ）, 且"按定时抽取"与
//     "复乘消旋"是可交换的线性操作 → 相位可以共享、且消旋可移到去交错之后;
//   · **SFD 定界是 64 片相干相关**, 要求窗内相位一致 → 必须跑在**已消旋**的
//     码片上 → 只能每通道一份（见 rx_chip_backend 的 sfd_detect）。
//
// 相位 latching：preamble_sync 在未消旋输入下 SFD 不触发, 会周期性超时重扫
// （码片流因此断续）；故本模块只取它的**相位**, 用 latch 住的值驱动独立的
// 持续去交错器（deinterleave）——码片流不再中断。
//
// SYNC_DIRECT：0 = 扫描取相位; 1 = 相位由 ext_lock_phase 直接给定（无扫描）。
// ---------------------------------------------------------------------------
`timescale 1ns/1ps
module rx_frontend #(
    parameter W = 21,
    parameter SYNC_DIRECT = 1'b0
) (
    input  wire               clk,
    input  wire               rst_n,
    // ---- ADC ----
    input  wire signed [11:0] adc_i,
    input  wire signed [11:0] adc_q,
    input  wire               adc_dv,
    // ---- 门限 / 定时外置 ----
    input  wire [47:0]        ph_thresh,
    input  wire [47:0]        sfd_thresh,
    input  wire               ext_lock_en,
    input  wire [3:0]         ext_lock_phase,
    // ---- 去交错后的码片流（**未消旋**, 供各通道消旋）----
    output wire signed [W-1:0] chip_i,
    output wire signed [W-1:0] chip_q,
    output wire               chip_dv,
    output wire [3:0]         phase_out,     // 当前生效的相位（观测）
    output wire               detect,        // 扫描锁定指示（SYNC_DIRECT=1 时=ext_lock_en）
    // ---- MF 引出（供估计器/观测器）----
    output wire signed [W-1:0] mf_i,
    output wire signed [W-1:0] mf_q,
    output wire               mf_dv
);
    // ---------------- 匹配滤波（一份）----------------
    rx_matched_filter u_mf (
        .clk(clk), .rst_n(rst_n),
        .i_in(adc_i), .q_in(adc_q), .dv_in(adc_dv),
        .i_out(mf_i), .q_out(mf_q), .dv_out(mf_dv)
    );

    // ---------------- 相位来源：外置 或 扫描 ----------------
    wire [3:0] scan_phase;
    generate
        if (SYNC_DIRECT) begin : g_direct
            assign detect     = ext_lock_en;
            assign scan_phase = ext_lock_phase;
        end else begin : g_scan
            // 只用它的相位输出: 码片端口悬空, frame_done 接 0（保持扫描, 不复位）
            preamble_sync #(.W(W)) u_sync (
                .clk(clk), .rst_n(rst_n),
                .i_in(mf_i), .q_in(mf_q), .dv_in(mf_dv),
                .ph_thresh(ph_thresh), .sfd_thresh(sfd_thresh),
                .frame_done(1'b0),
                .ext_lock_en(1'b0), .ext_lock_phase(4'd0),
                .chip_i(), .chip_q(), .chip_dv(),
                .detect(detect), .frame_start(), .locked_phase(scan_phase)
            );
        end
    endgenerate

    // ---------------- 相位 latch（首次锁定值）----------------
    reg [3:0] phase_fix;
    reg       phase_valid;
    always @(posedge clk) begin
        if (!rst_n) begin
            phase_fix   <= 4'd0;
            phase_valid <= 1'b0;
        end else if (SYNC_DIRECT) begin
            phase_fix   <= ext_lock_phase;
            phase_valid <= 1'b1;
        end else if (detect && !phase_valid) begin
            phase_fix   <= scan_phase;      // 首个锁定相位
            phase_valid <= 1'b1;
        end
    end
    assign phase_out = phase_fix;

    // ---------------- 持续去交错（不中断）----------------
    deinterleave #(.W(W)) u_deint (
        .clk(clk), .rst_n(rst_n),
        .i_in(mf_i), .q_in(mf_q), .dv_in(mf_dv),
        .phase(phase_fix),
        .chip_i(chip_i), .chip_q(chip_q), .chip_dv(chip_dv)
    );
endmodule
