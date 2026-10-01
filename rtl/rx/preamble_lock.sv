// preamble_lock.sv —— 精简同步器：**去交错 + SFD 定界**（无扫描）
// ---------------------------------------------------------------------------
// 与 preamble_sync 的关系（docs/16 §8.5 "同步器极简化"）：
//
//   preamble_sync = 16 候选扫描（**定时估计**, ~43 kbit: 16 × 32 码片 × 2(I/Q)
//                   × 21 bit 的码片缓冲 + 各候选累加器/计数器/轴判别寄存器）
//                 + 去交错 + SFD 定界
//   本模块        = 去交错 + SFD 定界（**相位由外部给定**, 无扫描, ~3 kbit）
//
// 用途：`rx_backend` 的 `ext_lock` 通道给了"定时外置"的入口；本模块是那条路
// 的执行体 —— 估计（相位从哪来）留在外面，执行（按相位取片、定帧界）在这里。
//
// 相位语义（与 preamble_sync 一致）：phase = 偶片峰在 16 采样周期内的位置；
// 奇片峰 = (phase+12)%16，采样时做 -j 旋转（I'=Q, Q'=-I）。
//
// 时序：每 16 采样出 2 片（一偶一奇）；SFD = CHIP[7]++CHIP[10] 的 64 片滑窗
// 相关过门限时给出 frame_start（与首个 PHR 码片同拍，仅报一次）。
// ---------------------------------------------------------------------------
`timescale 1ns/1ps
module preamble_lock #(
    parameter W  = 21,
    parameter ACC_W = W + 6 ,        // SFD 窗累加位宽 (64×|chip|)
    parameter FRAME_DRAIN = 17       // 帧尾兑底超时 (2^17 采样)
) (
    input  wire               clk,
    input  wire               rst_n,
    input  wire signed [W-1:0] i_in,
    input  wire signed [W-1:0] q_in,
    input  wire               dv_in,
    input  wire [3:0]         phase,        // 外部定时相位（偶片峰位置 0..15）
    input  wire [47:0]        sfd_thresh,   // SFD 窗能量门限
    input  wire               frame_done,   // 帧尾事件（来自 rx_deframer）: 复位待下帧
    output reg  signed [W-1:0] chip_i,
    output reg  signed [W-1:0] chip_q,
    output reg                chip_dv,
    output reg                frame_start   // 单拍, 与首个 PHR 码片同拍
);
    localparam AW = ACC_W + 7;       // 窗相关累加位宽

    // SFD 模板: CHIP[7] (m<32) ++ CHIP[10] (m>=32)
    function t64(input integer m);
        if (m < 32)
            t64 = (32'b10011100001101010010001011101101 >> (31 - m)) & 1;
        else
            t64 = (32'b01111011100011001001011000000111 >> (63 - m)) & 1;
    endfunction

    // ---------------- 去交错（相位由外部给定, 无扫描）----------------
    reg  [15:0] smp_cnt;
    wire [3:0]  s_p16     = smp_cnt[3:0];
    wire        emit_even = (s_p16 == phase);            // 偶片峰位置 = phase
    wire        emit_odd  = ((s_p16 + 4'd4) == phase);   // 奇片峰位置 = (phase+12)%16
    wire        emit_chip = emit_even || emit_odd;
    wire signed [W-1:0] d_i = emit_even ? i_in : q_in;          // 奇片: I'=Q
    wire signed [W-1:0] d_q = emit_even ? q_in : (~i_in + 1'b1); // 奇片: Q'=-I

    // ---------------- SFD 窗（64 片锚定全并行相关）----------------
    reg signed [W-1:0] sfd_si [0:63];
    reg signed [W-1:0] sfd_sq [0:63];
    reg [6:0] sfd_n;                 // 已移入片数
    reg       sfd_found;
    reg       fs_pending;
    reg [FRAME_DRAIN-1:0] drain_cnt;

    reg signed [AW-1:0] waccI, waccQ;
    integer tj;
    always @* begin
        waccI = t64(6'd63) ? {{(AW-W){d_i[W-1]}}, d_i}
                           : -{{(AW-W){d_i[W-1]}}, d_i};
        waccQ = t64(6'd63) ? {{(AW-W){d_q[W-1]}}, d_q}
                           : -{{(AW-W){d_q[W-1]}}, d_q};
        for (tj = 0; tj < 63; tj = tj + 1) begin
            if (t64(tj)) begin
                waccI = waccI + {{(AW-W){sfd_si[tj+1][W-1]}}, sfd_si[tj+1]};
                waccQ = waccQ + {{(AW-W){sfd_sq[tj+1][W-1]}}, sfd_sq[tj+1]};
            end else begin
                waccI = waccI - {{(AW-W){sfd_si[tj+1][W-1]}}, sfd_si[tj+1]};
                waccQ = waccQ - {{(AW-W){sfd_sq[tj+1][W-1]}}, sfd_sq[tj+1]};
            end
        end
    end
    wire [2*AW-1:0] sfd_E = waccI*waccI + waccQ*waccQ;

    integer pp;

    always @(posedge clk) begin
        if (!rst_n) begin
            smp_cnt     <= 16'd0;
            chip_i      <= {W{1'b0}};
            chip_q      <= {W{1'b0}};
            chip_dv     <= 1'b0;
            frame_start <= 1'b0;
            sfd_n       <= 7'd0;
            sfd_found   <= 1'b0;
            fs_pending  <= 1'b0;
            drain_cnt   <= {FRAME_DRAIN{1'b0}};
            for (pp = 0; pp < 64; pp = pp + 1) begin
                sfd_si[pp] <= {W{1'b0}};
                sfd_sq[pp] <= {W{1'b0}};
            end
        end else if (dv_in) begin
            smp_cnt     <= smp_cnt + 16'd1;   // 自由跑，相位由 phase 对齐
            chip_dv     <= 1'b0;
            frame_start <= 1'b0;
            drain_cnt   <= frame_done ? {FRAME_DRAIN{1'b0}} : drain_cnt + 1'b1;

            if (frame_done) begin
                // 帧尾: 复位 SFD 状态, 准备下一帧（相位不变 —— 外部负责每帧更新）
                sfd_n      <= 7'd0;
                sfd_found  <= 1'b0;
                fs_pending <= 1'b0;
            end else if (emit_chip) begin
                chip_i      <= d_i;
                chip_q      <= d_q;
                chip_dv     <= 1'b1;
                frame_start <= fs_pending;    // 上一片判决通过 → 本片同拍
                fs_pending  <= 1'b0;
                for (pp = 0; pp < 63; pp = pp + 1) begin
                    sfd_si[pp] <= sfd_si[pp+1];
                    sfd_sq[pp] <= sfd_sq[pp+1];
                end
                sfd_si[63] <= d_i;
                sfd_sq[63] <= d_q;
                sfd_n <= (sfd_n < 7'd127) ? sfd_n + 7'd1 : sfd_n;
                if (!sfd_found && sfd_n >= 7'd63 && sfd_E >= sfd_thresh) begin
                    fs_pending <= 1'b1;
                    sfd_found  <= 1'b1;
                end
            end else if (drain_cnt == {FRAME_DRAIN{1'b1}}) begin
                // 兜底: 长时间无 frame_done（假前导/丢帧）→ 复位待下一帧
                sfd_n      <= 7'd0;
                sfd_found  <= 1'b0;
                fs_pending <= 1'b0;
            end
        end
    end
endmodule
