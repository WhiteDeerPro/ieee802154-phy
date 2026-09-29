// preamble_detect.sv —— 短窗归一化延迟自相关前导检测器
// ---------------------------------------------------------------------------
// 用途: 为 cfo_est 提供"帧到达"触发 (docs/08 I-6)。§
//
// 判据 (归一化延迟自相关, 对 CFO 免疫 —— 差分自动消掉相位旋转):
//     Λ[n] = |Σ_{k=0}^{L-1} r[n-k]·conj(r[n-k-D])| / Σ_{k=0}^{L-1} |r[n-k]|²  >  γ
//
// 设计选择:
//   * **下采样 DEC=8**: 定时精度只要求 ±1 码片(=8 采样), 因此每 8 拍取一个样本,
//     延迟线与运算量降 8 倍。D = 256/DEC = 32 (前导周期), L = 8 (窗长 = 64 采样)。
//   * **L1 幅度近似**: |acc| ≈ |acc_i| + |acc_q| (避免开方), 最坏偏高 √2 倍,
//     由 γ 标定吸收。前导期 |acc|≈pwr (Λ≈1.0), 噪声期 |acc|≈0.35·pwr。
//   * 判据变形为 **|acc| > γ·pwr** (免除法): γ 为 Q0.24 常数, 默认 0.5。
//
// 参考实验: model/experiments/run_preamble_detect.py (四类检测器对比, autocorr
// 在 0–200 kHz CFO 下 Pd=1.00)。
// ---------------------------------------------------------------------------
`timescale 1ns/1ps
module preamble_detect #(
    parameter W = 21,                          // 输入位宽 (与 MF 输出同宽)
    parameter integer DEC = 8,                 // 下采样倍数 (定时精度 = 1 码片)
    parameter integer D = 32,                  // 延迟 (样本数) = 256/DEC
    parameter integer L = 32,                  // 窗长 (样本数) = 256 采样 (与前导试验一致)
    parameter integer AW = 2*W + 6,            // acc 累加位宽
    parameter integer PW = 2*W + 6,            // pwr 累加位宽
    parameter signed [23:0] GAMMA_Q = 24'sd0,  // 占位 (由 GAMMA_NUM/SHIFT 给 γ)
    parameter integer GAMMA_NUM = 21,          // γ = GAMMA_NUM / 2^GAMMA_SHIFT
    parameter integer GAMMA_SHIFT = 5          // 默认 γ ≈ 0.66
) (
    input  wire                 clk,
    input  wire                 rst_n,
    input  wire signed [W-1:0]  i_in,
    input  wire signed [W-1:0]  q_in,
    input  wire                 dv_in,
    output reg                  det_pulse      // 单拍脉冲 (每 DEC 拍最多一次)
);
    localparam integer DEPTH = D + L + 1;      // 样本历史深度 (抽头最大索引 = D+L)
    localparam integer DCW = $clog2(DEC);      // 下采样计数位宽

    // ---------------- 下采样与延迟线 ----------------
    reg  [DCW-1:0] dcnt;
    reg  signed [W-1:0] si [0:DEPTH-1];
    reg  signed [W-1:0] sq [0:DEPTH-1];
    integer j;

    wire sample_en = (dcnt == DEC - 1);

    always @(posedge clk) begin
        if (!rst_n) begin
            dcnt <= {DCW{1'b0}};
            for (j = 0; j < DEPTH; j = j + 1) begin
                si[j] <= {W{1'b0}};
                sq[j] <= {W{1'b0}};
            end
        end else if (dv_in) begin
            dcnt <= sample_en ? {DCW{1'b0}} : (dcnt + 1'b1);
            if (sample_en) begin
                for (j = DEPTH - 1; j > 0; j = j - 1) begin
                    si[j] <= si[j-1];
                    sq[j] <= sq[j-1];
                end
                si[0] <= i_in;
                sq[0] <= q_in;
            end
        end
    end

    // ---------------- 相关项 (新 / 老) ----------------
    // 新项: z_new = s[0]·conj(s[D])   老项: z_old = s[L]·conj(s[D+L])
    wire signed [2*W+1:0] nz_i = si[0]*si[D] + sq[0]*sq[D];
    wire signed [2*W+1:0] nz_q = sq[0]*si[D] - si[0]*sq[D];
    wire signed [2*W+1:0] oz_i = si[L]*si[D+L] + sq[L]*sq[D+L];
    wire signed [2*W+1:0] oz_q = sq[L]*si[D+L] - si[L]*sq[D+L];
    // 能量项
    wire [2*W+1:0] n_pwr = si[0]*si[0] + sq[0]*sq[0];
    wire [2*W+1:0] o_pwr = si[L]*si[L] + sq[L]*sq[L];

    // ---------------- 滑窗累加 ----------------
    reg signed [AW-1:0] acc_i, acc_q;
    reg        [PW-1:0] pwr;

    always @(posedge clk) begin
        if (!rst_n) begin
            acc_i <= {AW{1'b0}};
            acc_q <= {AW{1'b0}};
            pwr   <= {PW{1'b0}};
        end else if (dv_in && sample_en) begin
            acc_i <= acc_i + nz_i - oz_i;
            acc_q <= acc_q + nz_q - oz_q;
            pwr   <= pwr + n_pwr - o_pwr;
        end
    end

    // ---------------- 判据: |acc| > γ·pwr ----------------
    // 幅度用 max + 3/8·min 近似 L2 范数 (误差 ~5%), 比 L1 准确得多。
    wire signed [AW:0] acc_i_ext = {acc_i[AW-1], acc_i};
    wire signed [AW:0] acc_q_ext = {acc_q[AW-1], acc_q};
    wire [AW:0] ai_abs = acc_i_ext[AW] ? -acc_i_ext : acc_i_ext;
    wire [AW:0] aq_abs = acc_q_ext[AW] ? -acc_q_ext : acc_q_ext;
    wire [AW:0] a_max = (ai_abs > aq_abs) ? ai_abs : aq_abs;
    wire [AW:0] a_min = (ai_abs > aq_abs) ? aq_abs : ai_abs;
    wire [AW:0] mag_est = a_max + (a_min >> 1) - (a_min >> 3);   // max + 3/8·min
    // γ·pwr = (GAMMA_NUM · pwr) >> GAMMA_SHIFT
    wire [PW+15:0] thr = (pwr * GAMMA_NUM) >> GAMMA_SHIFT;
    wire [PW+15:0] mag_ext = {{(PW+16-AW-1){1'b0}}, mag_est};

    // 连续 2 个采样点确认 (抗虚警): 前导期 Λ 持续超阈, 噪声尖峰则难连续
    reg hit_d;
    always @(posedge clk) begin
        if (!rst_n) begin
            det_pulse <= 1'b0;
            hit_d     <= 1'b0;
        end else if (dv_in && sample_en) begin
            hit_d     <= (mag_ext > thr);
            det_pulse <= (mag_ext > thr) && hit_d;
        end
    end
endmodule
