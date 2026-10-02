// awgn_gen.sv —— CLT 数字 AWGN 注入源（I/Q 独立；测试/标定基础设施，非功能通路）
// ---------------------------------------------------------------------------
// 定位（docs/20 §4）: 与"离线含噪波形"并存，提供**在线改 SNR** 能力——
//   回归免重生成 / γ·CFAR 门限标定 / 片上 BER 环回。
// 方法: N 路均匀求和（中心极限定理近似高斯）。外部实践: CLT 为 FPGA 轻量主流
//   （Box-Muller 级精度对本项目过重; 见 docs/20 §4 检索记录）。
//
// 结构:
//   · 64bit Fibonacci LFSR ×2（I/Q 独立, 同多项式 x^64+x^63+x^61+x^60+1,
//     不同种子）; **每拍并行生成 60 个新位**（= N×SW 位样本原材料）:
//       设本拍状态 st[63:0]（"新位从 bit0 进、向高位走"口径），
//       第 j 个新位 fb_j = st[64-j]^st[63-j]^st[61-j]^st[60-j]（j=1..60），
//       即"四个滑动 XOR 窗口"——**完全并行、免递归链**;
//       状态更新 st <= {st[3:0], nb}（nb 降序: bit59=fb_1 … bit0=fb_60）。
//   · 12 段 × 5bit 求和（CLT）→ 去均值（N*(2^SW-1)/2 = 186）→
//     定标 σ = σ0·cfg_mul/2^cfg_shift（σ0 = sqrt(N·(2^2SW−1)/12) ≈ 32）→
//     饱和 → 输出（2 级流水）。
// 面积量级: ~百 cells（64 FF ×2 + 60×4 XOR ×2 + 加法/乘法）——远小于 pbuf v2。
// 注: 输出为纯噪声样本（与信号相加在注入侧完成, 本模块不含加法）。
// ---------------------------------------------------------------------------
`timescale 1ns/1ps
module awgn_gen #(
    parameter W  = 16,          // 输出位宽（signed）
    parameter N  = 12,          // CLT 路数
    parameter SW = 5            // 每路均匀位宽（0..2^SW-1）
) (
    input  wire                clk,
    input  wire                rst_n,
    input  wire                en,          // 1: 每拍产出一对样本
    input  wire [15:0]         cfg_mul,     // σ 定标: σ = σ0*mul/2^shift
    input  wire [4:0]          cfg_shift,
    output reg  signed [W-1:0] out_i,
    output reg  signed [W-1:0] out_q,
    output reg                 out_dv
);
    localparam integer NB   = N * SW;                          // 60
    localparam integer SUMW = 16;                              // 和: 12*31=372 → 9bit, 留裕量
    localparam signed [SUMW-1:0] MEAN = N * ((1 << SW) - 1) / 2;   // 186（N 偶）

    localparam [63:0] SEED_I = 64'h0123_4567_89AB_CDEF;
    localparam [63:0] SEED_Q = 64'hFEDC_BA98_7654_3210;

    reg [63:0] st_i, st_q;

    // —— 每拍 60 个新位（并行滑窗 XOR; nb[k]=fb_{60-k}, 即 k=0 → st 低位抽头）——
    wire [NB-1:0] nb_i, nb_q;
    genvar g;
    generate
        for (g = 0; g < NB; g = g + 1) begin : g_nb
            // nb[k] 对应"第 j=NB-k 个新位": 抽头 st[(64-j)..], 以 k 表达:
            //   j = NB-k → 指数 64-j = 4+k, 63-j = 3+k, 61-j = 1+k, 60-j = k
            assign nb_i[g] = st_i[4 + g] ^ st_i[3 + g] ^ st_i[1 + g] ^ st_i[g];
            assign nb_q[g] = st_q[4 + g] ^ st_q[3 + g] ^ st_q[1 + g] ^ st_q[g];
        end
    endgenerate
    // 状态推进 60 步: 高 4 位 = 旧低位残余, [59:0] = nb（bit59 最新）
    wire [63:0] st_i_next = {st_i[3:0], nb_i};
    wire [63:0] st_q_next = {st_q[3:0], nb_q};

    // —— 拍 1: 求和（组合）+ 去均值（寄存） ——
    reg [SUMW-1:0] ssum_i, ssum_q;
    integer kk;
    always @* begin
        ssum_i = {SUMW{1'b0}};
        ssum_q = {SUMW{1'b0}};
        for (kk = 0; kk < N; kk = kk + 1) begin
            ssum_i = ssum_i + nb_i[kk*SW +: SW];
            ssum_q = ssum_q + nb_q[kk*SW +: SW];
        end
    end

    reg signed [SUMW:0] c_i, c_q;      // 和-186 ∈ [-186,186]
    reg                 en_d1;
    always @(posedge clk) begin
        if (!rst_n) begin
            st_i  <= SEED_I;
            st_q  <= SEED_Q;
            c_i   <= 0;
            c_q   <= 0;
            en_d1 <= 1'b0;
        end else if (en) begin
            st_i  <= st_i_next;
            st_q  <= st_q_next;
            c_i   <= $signed({1'b0, ssum_i}) - MEAN;
            c_q   <= $signed({1'b0, ssum_q}) - MEAN;
            en_d1 <= 1'b1;
        end
    end

    // —— 拍 2: 定标（乘+移位）+ 饱和 + 输出 ——
    localparam signed [31:0] MAXV = (1 << (W - 1)) - 1;
    localparam signed [31:0] MINV = -(1 << (W - 1));
    wire signed [31:0] p_i = c_i * $signed({1'b0, cfg_mul});
    wire signed [31:0] p_q = c_q * $signed({1'b0, cfg_mul});
    wire signed [31:0] sc_i = p_i >>> cfg_shift;
    wire signed [31:0] sc_q = p_q >>> cfg_shift;
    wire signed [31:0] sat_i = (sc_i > MAXV) ? MAXV : (sc_i < MINV) ? MINV : sc_i;
    wire signed [31:0] sat_q = (sc_q > MAXV) ? MAXV : (sc_q < MINV) ? MINV : sc_q;

    always @(posedge clk) begin
        if (!rst_n) begin
            out_i  <= {W{1'b0}};
            out_q  <= {W{1'b0}};
            out_dv <= 1'b0;
        end else if (en) begin
            out_i  <= sat_i[W-1:0];
            out_q  <= sat_q[W-1:0];
            out_dv <= en_d1;
        end else begin
            out_dv <= 1'b0;
        end
    end
endmodule
