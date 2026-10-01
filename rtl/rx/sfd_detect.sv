// sfd_detect.sv —— 码片级 SFD 定界（吃**已消旋**的码片流）
// ---------------------------------------------------------------------------
// 为什么它必须"每通道一份"（而不是像相位那样共享）：
//   SFD = CHIP[7]++CHIP[10] 的 64 片**相干**相关。相干累加要求窗内相位一致，
//   所以它必须跑在**已消旋**的码片上 —— 而消旋是每通道的（旋性假设不同）。
//   （相位/去交错则与旋性无关，可以共享，见 rx_frontend。）
//
// 与 preamble_lock 的区别：本模块只做 SFD 定界，**不含去交错**（去交错在共享前端）。
//
// 时序：每片移入窗；sfd_E(窗 [n-63..n]) 过门限的**下一拍**给出 frame_start。
//   ⚠ 必须是下一拍而不是“下一个 chip_dv 拍”：片间隔 8 拍，若等到下一个 chip_dv，
//   frame_start 会与**第二片**同拍 → despreader 窗口整体晚 1 片 → 全帧解不出
//   （实测：窗口偏移 21 而正确为 20）。下一拍通常是片间隙，despreader 走
//   chip_cnt<=0 分支，紧跟的第一个片（= 首个 PHR 码片）成为新窗口首片。
//       frame_done（本通道解帧完成）→ 复位待下帧。
// ---------------------------------------------------------------------------
`timescale 1ns/1ps
module sfd_detect #(
    parameter W       = 21,
    parameter ACC_W   = W + 6,        // 窗累加位宽 (64×|chip|)
    parameter DRAIN_W = 17            // 无 frame_done 时的兑底超时
) (
    input  wire               clk,
    input  wire               rst_n,
    input  wire signed [W-1:0] chip_i,       // 已消旋的码片
    input  wire signed [W-1:0] chip_q,
    input  wire               chip_dv,
    input  wire [47:0]        sfd_thresh,   // 绝对能量门限（norm_th=0 时使用）
    input  wire [15:0]        norm_th = 16'd0,  // 归一化门限 Q8（ρ_th×256）; 0=关闭
    input  wire               frame_done,    // 帧尾（本通道 rx_deframer）
    output reg                frame_start
);
    localparam AW = ACC_W + 7;

    // SFD 模板: CHIP[7] (m<32) ++ CHIP[10] (m>=32)
    function t64(input integer m);
        if (m < 32)
            t64 = (32'b10011100001101010010001011101101 >> (31 - m)) & 1;
        else
            t64 = (32'b01111011100011001001011000000111 >> (63 - m)) & 1;
    endfunction

    reg signed [W-1:0] si [0:63];
    reg signed [W-1:0] sq [0:63];
    reg [6:0]          n_cnt;
    reg                found;
    reg [DRAIN_W-1:0]  drain;

    reg signed [AW-1:0] wi, wq;
    integer tj;
    always @* begin
        wi = t64(6'd63) ? {{(AW-W){chip_i[W-1]}}, chip_i}
                        : -{{(AW-W){chip_i[W-1]}}, chip_i};
        wq = t64(6'd63) ? {{(AW-W){chip_q[W-1]}}, chip_q}
                        : -{{(AW-W){chip_q[W-1]}}, chip_q};
        for (tj = 0; tj < 63; tj = tj + 1) begin
            if (t64(tj)) begin
                wi = wi + {{(AW-W){si[tj+1][W-1]}}, si[tj+1]};
                wq = wq + {{(AW-W){sq[tj+1][W-1]}}, sq[tj+1]};
            end else begin
                wi = wi - {{(AW-W){si[tj+1][W-1]}}, si[tj+1]};
                wq = wq - {{(AW-W){sq[tj+1][W-1]}}, sq[tj+1]};
            end
        end
    end
    wire [2*AW-1:0] sfd_E = wi*wi + wq*wq;

    // ---- 归一化（自适应）门限: ρ = sfd_E / (64·W) ≥ ρ_th ----
    // W = 窗内 64 片能量和（与 sfd_E 同窗: {si[1..63], 当前片}）;
    // 判据等价于 sfd_E ≥ 64·ρ_th·W = (norm_th·W)>>2（norm_th = round(ρ_th·256)）——
    // 与信号幅度无关：低功率设备的 SFD 相关能量同比变小, ρ 不变。
    wire [2*W-1:0]  sq_new = chip_i*chip_i + chip_q*chip_q;
    wire [2*W-1:0]  sq_old = si[0]*si[0] + sq[0]*sq[0];
    reg  [47:0]     w_sum;                        // 上一拍窗口能量（不含当前片）
    wire [47:0]     w_nxt = w_sum + sq_new - sq_old;   // 本拍窗口能量（含当前片）
    wire [2*AW-1:0] sfd_lim = (norm_th * w_nxt) >> 2;
    wire trig = (norm_th != 16'd0)
              ? (w_nxt != 48'd0 && sfd_E >= sfd_lim)
              : (sfd_E >= sfd_thresh);

    integer pp;

    always @(posedge clk) begin
        if (!rst_n) begin
            frame_start <= 1'b0;
            n_cnt <= 7'd0;
            found <= 1'b0;
            drain <= {DRAIN_W{1'b0}};
            w_sum <= 48'd0;
            for (pp = 0; pp < 64; pp = pp + 1) begin
                si[pp] <= {W{1'b0}};
                sq[pp] <= {W{1'b0}};
            end
        end else begin
            frame_start <= 1'b0;
            drain <= frame_done ? {DRAIN_W{1'b0}} : drain + 1'b1;

            if (frame_done) begin
                n_cnt <= 7'd0;
                found <= 1'b0;
            end else if (chip_dv) begin
                for (pp = 0; pp < 63; pp = pp + 1) begin
                    si[pp] <= si[pp+1];
                    sq[pp] <= sq[pp+1];
                end
                si[63] <= chip_i;
                sq[63] <= chip_q;
                w_sum  <= w_nxt;
                n_cnt  <= (n_cnt < 7'd127) ? n_cnt + 7'd1 : n_cnt;
                if (!found && n_cnt >= 7'd63 && trig) begin
                    frame_start <= 1'b1;   // 下一拍 (片间隙) → 下一个片成为新窗口首片
                    found <= 1'b1;
                end
            end else if (drain == {DRAIN_W{1'b1}}) begin
                n_cnt <= 7'd0;              // 兑底: 长时间无帧尾 → 复位待下帧
                found <= 1'b0;
            end
        end
    end
endmodule
