// preamble_sync.sv —— 前馈同步: 8 相位扫描 (块间自相关) → argmax 锁定
// → 去交错码片流 → SFD 滑窗 (窗锚定模板) 定帧边界
// 短包场景无 PLL: 定时一次测定后 free-run (docs/05 及讨论记录)。
//
// 采样结构 (实测, 与黄金模型 modulate_oqpsk_fixed 约定一致):
//   每 16 采样有 2 个码片峰 (I 路偶片 + Q 路奇片), 峰间距 12/4 交替;
//   偶片峰位置 pe 与奇片峰位置 po=(pe+12)%16 中恰有一个落在任一 8 拍半周期。
//   **帧到达相位任意 ⇒ pe 可为 0..15**, 故候选必须覆盖全部 16 个采样位置:
//     候选 c = 偶片峰位置假设; 服务 = 位置 c 采偶片 (I,Q 原值),
//             位置 (c+12)%16 采奇片 (-j 旋转: I'=Q, Q'=-I)。
//   等价地, 位置 q 的采样同时服务: 偶链候选 c=q、奇链候选 c=(q+4)%16。
//   每候选每 16 采样收 2 片 → 块 = 32 片 = 256 采样。
//   (旧版只服务 cnt[3]=0 的 8 拍, 隐含假设 pe∈[0,8) —— 实测只覆盖 6/16
//    相位, 已修正为全 16 相位。)
//
// 扫描统计量 (模板无关, 避开 32 相位符号边界模糊): 前导 = CHIP[0] 周期重复,
// 块相关对 T0 只有在块对齐符号边界时才相干。改用块间延迟自相关 + I/Q 轴判别:
//   r = Σ_k ( I_m[k]·I_{m-1}[k] − Q_m[k]·Q_{m-1}[k] )
// 真相位: r ≈ +32·0.9·P² (码片能量集中实轴); 偶/奇轴互换候选: −32·0.9·P²;
// 纯噪 |r| 低 ~4 个数量级。每候选跟踪历史最大 r (bestR), 所有候选完成
// K_BLOCKS 块后, 每 k=0 采样检查: argmax(bestR) 过 ph_thresh 即锁定。
// (不用即时锁定: ±1 采样偏移候选的 r 可达真相位 ~87%, 先锁可能锁错;
//  前导 256 片 = 2048 采样, K=4 块在 ~帧内 600 采样处收尾, SFD 区尚有余量。)
//
// 锁定后按同一去交错规则输出码片流; SFD (CHIP[7]++CHIP[10]) 64 码片滑窗
// 互相关, 模板锚定窗起点 (进片乘 TS[63], 出片乘 TS[0]):
//   sfd_E(窗 [n-64..n-1]) 过 sfd_thresh 时 frame_start 与第 n 片
//   (= 首个 PHR 码片) 同拍, 仅报一次。
//
// ⚠ Phase 2 限定: 假设 CFO≈0 (cfo_corr 后续补充; |·| 检测容忍符号内残余相旋)。
// 与 model/phy_802154.py preamble_sync_mirror 逐位镜像。
`timescale 1ns/1ps
module preamble_sync #(
    parameter W = 21,
    parameter K_BLOCKS = 4,
    parameter SFD_WAIT    = 12,   // 锁定→SFD 等待超时: 2^12 = 4096 采样 (256 µs @16MHz)
    parameter FRAME_DRAIN = 17,   // SFD→frame_done 兑底超时: 2^17 = 131072 采样 (≈4.6 帧时长)
    parameter NORM_NUM    = 11,   // 归一化门限 γ = NORM_NUM/2^NORM_SHIFT = 0.344
    parameter NORM_SHIFT  = 5,
    parameter E_W         = 48    // 块能量位宽 (32 × 2×(2^20)²)
) (
    input  wire                    clk,
    input  wire                    rst_n,
    input  wire signed [W-1:0]     i_in,
    input  wire signed [W-1:0]     q_in,
    input  wire                    dv_in,
    input  wire [47:0]             ph_thresh,     // 块间自相关 r 门限 (正, 有符号比较)
    input  wire [47:0]             sfd_thresh,    // SFD 窗能量门限
    input  wire                    frame_done,    // 帧尾事件 (来自 rx_deframer): 回扫描态
    // ---- 外部定时通道（"全估计外置"的定时部分, 见 docs/16 §8）----
    // 置 ext_lock_en=1 且在扫描态时, 直接用 ext_lock_phase 去交错, 跳过 16 候选扫描。
    // 约束: 帧到达相位逐帧不同 ⇒ 外部必须"每帧"给对（上层需在前导期 128 µs 内完成分析）。
    input  wire                    ext_lock_en,
    input  wire [3:0]              ext_lock_phase,
    // ---- 扫描重启（单拍）: 帧到达时（preamble_detect）强制回扫描 ----
    // 为什么必须: 帧到达相位逐帧不同, 而本模块的扫描是自由周期的（超时→重扫）,
    // 周期与帧周期不同步 → 锁定只能碰巧落在前导期（实测命中率 = 前导/重扫周期
    // ≈ 2048/5121 ≈ 42%）。由外部"帧到达"事件重启扫描, 保证扫描总在**本帧前导**
    // 上进行。未连接（如 rx_top 单帧场景, 自包含扫描够用）时视为 0。
    input  wire                    scan_restart,
    output reg  signed [W-1:0]     chip_i,
    output reg  signed [W-1:0]     chip_q,
    output reg                     chip_dv,
    output reg                     detect,        // 高电平: 相位已锁定
    output reg                     frame_start,   // 单拍, 与 chip_dv 同拍 (首个 PHR 码片)
    output reg  [3:0]              locked_phase
);
    localparam ACC_W = W + 6;        // 64×|chip| (SFD 窗累加)
    localparam R_W   = 2*W + 6;      // 块间自相关: 32×(21b×21b) 量级, 有符号
    localparam ST_SCAN = 2'd0, ST_LOCK = 2'd1;
    // SFD 模板: CHIP[7] (m<32) ++ CHIP[10] (m>=32)
    function t64(input integer m);
        if (m < 32)
            t64 = (32'b10011100001101010010001011101101 >> (31 - m)) & 1;
        else
            t64 = (32'b01111011100011001001011000000111 >> (63 - m)) & 1;
    endfunction

    // ---------------- 扫描状态 (16 相位候选, 覆盖全部采样相位) ----------------
    // 仅 k0=0 半周期采样的旧结构只覆盖 pe∈[0,8) (实测 6/16 相位可收),
    // 现改为**每拍服务**: 位置 q 同时服务候选 q (偶链) 与 (q+4)%16 (奇链),
    // 16 候选覆盖 pe 全部可能性。帧起点相位不影响正确性。
    reg [1:0]      state;
    reg [15:0]     smp_cnt;
    reg signed [R_W-1:0]   accR  [0:15];     // 当前块间自相关累加 (实部, 有符号)
    reg signed [R_W-1:0]   accI  [0:15];     // 当前块间自相关累加 (虚部, 有符号)
    reg        [R_W:0]     bestR [0:15];     // 各候选历史最大判据量 (复相关模 |Re|+|Im|)
    reg [5:0]      mcnt [0:15];       // 各候选块内码片计数 0..31
    reg [15:0]     bcnt [0:15];       // 各候选已完成块数
    reg signed [W-1:0] prevI [0:15][0:31];   // 上一块码片 (去交错后)
    reg signed [W-1:0] prevQ [0:15][0:31];
    reg signed [W-1:0] curI  [0:15][0:31];   // 当前块码片
    reg signed [W-1:0] curQ  [0:15][0:31];

    // 归一化锁定判据配件 (缺陷 3 修复, 见 docs/07 §3.5):
    //   绝对门限 r ≥ ph_thresh 的噪声散布 ∝ σ² → 低 SNR 裕量崩缩 (2dB 仅 2.3σ);
    //   归一化比值 r / E_prev (E = 块能量 Σ(I²+Q²)) 则与噪声功率无关:
    //   前导 ≈ +0.9/信噪降级后仍 >0.4, 纯噪声 ≈ N(0, ~0.12) → γ=0.34 时裕量充足。
    //   另要求**连续 2 块**合格 (单块尾部概率再平方), 消除孤立噪声尖峰。
    //   双判据取交集: 低 SNR 由归一化主导 (消除误锁), 高 SNR 由绝对门限主导
    //   (保持原行为, 数据段真相关块的误锁水平与现役一致)。
    reg [E_W-1:0]  accE    [0:15];   // 当前块能量累加
    reg [E_W-1:0]  prevE   [0:15];   // 上一块能量 (归一化分母)
    reg            pending [0:15];   // 上一块归一化合格
    reg            ready   [0:15];   // 连续 2 块合格 → 具备锁定资格

    // 轴判别 (相位无关): 正确候选的偶服务 (位置 c, I 片峰) 与奇服务 (位置 c+12,
    // Q 片峰旋转 -90°) 同相 → 点积 偶·奇 > 0; "轴互换"候选两者反相 → < 0。
    // [2026-09-28 二次修正] 原用 Σ(I²−Q²), 它依赖绝对相位: 残留常数相位 φ0 使
    // cos(2φ0)<0 时把正确/轴互换候选判反 (实测 φ0≈91° → 逐帧随机判反, ~50% 锁定率)。
    // 点积判据中 e^{jφ} 自动抵消, 对 φ0 完全免疫。
    // 轴判别 (相位无关, 不需模板对齐): 偶/奇服务序列 lag=2 自相关相减
    //   D = Σ_m Re(偶_m·conj(偶_{m-2}))  −  Σ_m Re(奇_m·conj(奇_{m-2}))
    // 依据: CHIP0 的 I 码片序列 lag2 自相关 = +4, Q 序列 = -4 (符号相反)。
    // 正确候选 (偶命 I 片/奇命 Q 片) → 偶+4、奇-4 → D = +8; 轴互换 → -8。
    // 相减使信号翻倍 (vs 偶侧单独 +4), 提高边界相位/噪声下的存活率。
    reg signed [W-1:0]   eh1_i [0:15], eh1_q [0:15];   // 上次偶服务值
    reg signed [W-1:0]   eh2_i [0:15], eh2_q [0:15];   // 上上次偶服务值
    reg signed [W-1:0]   oh1_i [0:15], oh1_q [0:15];   // 上次奇服务值
    reg signed [W-1:0]   oh2_i [0:15], oh2_q [0:15];   // 上上次奇服务值
    reg signed [R_W-1:0] axc     [0:15];   // Σ (偶·conj(偶_{m-2}) − 奇·conj(奇_{m-2}))
    reg [3:0]            ecnt    [0:15];   // 偶服务计数 (每 16 次评估)
    reg                  axis_ok [0:15];   // 上一块末轴判别结果

    wire [3:0] s_p16  = smp_cnt[3:0];
    wire [3:0] cand_e = s_p16;
    wire [3:0] cand_o = s_p16 + 4'd4;            // (q+4)%16

    // 偶候选服务值 / 奇候选服务值 (-j 旋转)
    wire signed [W-1:0] e_vi = i_in,  e_vq = q_in;
    wire signed [W-1:0] o_vi = q_in,  o_vq = ~i_in + 1'b1;

    // 块间自相关本采样增量: vi·conj(v') 的实部 = vi·prevI + vq·prevQ
    // [2026-09-28] 原为 vi·prevI − vq·prevQ (无共轭): 相位 = θk+θk' = 2φ₀+2ωt,
    // 对绝对相位 φ₀ 敏感 —— 消旋后/带 CFO 时 φ₀ 逐帧随机 → 约一半帧锁不上
    // (实测见 out/cfo_visual/cfo_blockcorr.png)。共轭积相位 = θk−θk' = ωτ → 对 φ₀ 免疫。
    wire [5:0] me = mcnt[cand_e];
    wire [5:0] mo = mcnt[cand_o];
    wire signed [R_W-1:0] step_e = e_vi * prevI[cand_e][me] + e_vq * prevQ[cand_e][me];
    wire signed [R_W-1:0] step_o = o_vi * prevI[cand_o][mo] + o_vq * prevQ[cand_o][mo];
    // 虚部: Im(vi·conj(v')) = vq·prevI − vi·prevQ
    wire signed [R_W-1:0] stepI_e = e_vq * prevI[cand_e][me] - e_vi * prevQ[cand_e][me];
    wire signed [R_W-1:0] stepI_o = o_vq * prevI[cand_o][mo] - o_vi * prevQ[cand_o][mo];
    wire signed [R_W-1:0] accR_e_nxt = accR[cand_e] + step_e;
    wire signed [R_W-1:0] accR_o_nxt = accR[cand_o] + step_o;
    wire signed [R_W-1:0] accI_e_nxt = accI[cand_e] + stepI_e;
    wire signed [R_W-1:0] accI_o_nxt = accI[cand_o] + stepI_o;
    // ---- 判据量: 复相关**模** |Re|+|Im| (L1 范数, 免开方/免除法) ----
    // [2026-09-29] 原判据只用实部 Re(Σv·conj(v'))，而块间相位差 = ω·256 采样，
    // 故实部带 cos(ω·256) 因子: CFO=100 kHz 时 cos = -0.81 (反相) ⇒ 永不过正门限
    // ⇒ 检测死锁; CFO=450 kHz 时 cos = +0.36 ⇒ 反而能检测。这正是“检出随 CFO
    // 非单调”的根源。改用模后 |z| 与旋转无关 ⇒ 对 CFO 免疫。
    // 验证: model/experiments/run_preamble_mag.py → out/preamble_mag/report.md
    wire [R_W-1:0] absR_e = accR_e_nxt[R_W-1] ? (~accR_e_nxt + 1'b1) : accR_e_nxt;
    wire [R_W-1:0] absI_e = accI_e_nxt[R_W-1] ? (~accI_e_nxt + 1'b1) : accI_e_nxt;
    wire [R_W-1:0] absR_o = accR_o_nxt[R_W-1] ? (~accR_o_nxt + 1'b1) : accR_o_nxt;
    wire [R_W-1:0] absI_o = accI_o_nxt[R_W-1] ? (~accI_o_nxt + 1'b1) : accI_o_nxt;
    wire [R_W:0]   mag_e  = {1'b0, absR_e} + {1'b0, absI_e};
    wire [R_W:0]   mag_o  = {1'b0, absR_o} + {1'b0, absI_o};
    wire e_done = (me == 6'd31);
    wire o_done = (mo == 6'd31);

    // 块能量累加与归一化判据 (能量用无符号; 服务值平方后恒非负)
    wire [E_W-1:0] e_sq = e_vi*e_vi + e_vq*e_vq;
    wire [E_W-1:0] o_sq = o_vi*o_vi + o_vq*o_vq;
    wire [E_W-1:0] accE_e_nxt = accE[cand_e] + e_sq;
    wire [E_W-1:0] accE_o_nxt = accE[cand_o] + o_sq;
    // 轴判别增量: 偶/奇服务与各自上上次服务的共轭积实部 (含符号, 奇侧取负)
    wire signed [2*W:0] dstep_e = e_vi * eh2_i[cand_e] + e_vq * eh2_q[cand_e];
    wire signed [2*W:0] dstep_o = o_vi * oh2_i[cand_o] + o_vq * oh2_q[cand_o];
    // 阈值 = γ·E_prev (无符号), 比较时必须显式转有符号 —— accR 可为负
    wire [E_W+4:0] norm_thr_e = (NORM_NUM * prevE[cand_e]) >> NORM_SHIFT;
    wire [E_W+4:0] norm_thr_o = (NORM_NUM * prevE[cand_o]) >> NORM_SHIFT;
    wire ratio_ok_e = (mag_e >= {1'b0, norm_thr_e});
    wire ratio_ok_o = (mag_o >= {1'b0, norm_thr_o});

    reg  [15:0] bmin;
    reg  [3:0]  pmax;
    reg          [R_W:0]   rmax;
    integer qi;
    always @* begin
        bmin = bcnt[0];
        for (qi = 1; qi < 16; qi = qi + 1)
            if (bcnt[qi] < bmin) bmin = bcnt[qi];
        // 双判据 AND 语义: 只在"归一化连续 2 块合格"的候选中选 r 最大者;
        // 无 ready 候选时 rmax=0 → 不会锁定
        pmax = 4'd0;
        rmax = {(R_W+1){1'b0}};
        for (qi = 0; qi < 16; qi = qi + 1) begin
            if (ready[qi] && axis_ok[qi] && (bestR[qi] > rmax)) begin
                rmax = bestR[qi];
                pmax = qi[3:0];
            end
        end
    end
    wire scan_armed  = (bmin >= K_BLOCKS);
    // 判据量非负 ⇒ 用无符号比较（也避开 R_W+1 位有符号溢出的边角）
    wire [R_W:0] ph_thresh_u = {1'b0, ph_thresh};
    wire lock_now = scan_armed && (rmax >= ph_thresh_u);

    // ---------------- 锁定后 ----------------
    reg [3:0]      lphase;
    reg signed [W-1:0] sfd_si [0:63];
    reg signed [W-1:0] sfd_sq [0:63];
    reg [6:0]      sfd_n;
    reg            sfd_found;
    reg            fs_pending;

    // 两阶段保持超时 (锁定验证 + 解帧兑底):
    //   A. 锁定→SFD (wait_cnt): 真锁最坏 ~2816 采样内必出 SFD (前导结构确定:
    //      锁定点最晚在前导末尾, SFD 窗满在帧起点+3072, 最早锁在帧起点+256),
    //      2^12 = 4096 有 1.45x 余量; 超时 = 判误锁 → 立即回扫描 (代价 ~1 帧,
    //      而非旧单阶段 2^18 的 ~18 帧)。
    //   B. SFD→frame_done (drain_cnt): 正常帧 ~14000 采样解完; 2^17 兑底防极端卡死。
    //   (旧版单阶段 2^18 按"最长帧时长"设计——语义取错: 真正约束是"锁定→SFD"。)
    reg [SFD_WAIT-1:0]    wait_cnt;
    reg [FRAME_DRAIN-1:0] drain_cnt;
    wire wait_expire  = (&wait_cnt)  && !sfd_found;
    wire drain_expire = (&drain_cnt) &&  sfd_found;
    wire hold_expire  = (state == ST_LOCK) && (wait_expire || drain_expire);

    // 本采样是否为锁定候选的码片输出拍 (每 16 采样 2 拍), 及去交错后的码片值
    wire emit_even = (s_p16 == lphase);              // 偶片峰位置 = lphase
    wire emit_odd  = ((s_p16 + 4'd4) == lphase);     // 奇片峰位置 = (lphase+12)%16
    wire emit_chip = emit_even || emit_odd;
    wire signed [W-1:0] d_i = emit_even ? i_in : q_in;          // 奇片: I'=Q
    wire signed [W-1:0] d_q = emit_even ? q_in : (~i_in + 1'b1); // 奇片: Q'=-I

    // SFD 窗锚定全并行相关: wacc = Σ_{j=0}^{63} TS[j]·chip[n-63+j]
    // 片序号 <0 的部分由移位寄存器复位零填充; 位置 j<63 ↔ sfd_s*[j+1], j=63 ↔ d_*
    reg signed [ACC_W+6:0] waccI, waccQ;
    integer tj;
    always @* begin
        waccI = t64(6'd63) ? {{(ACC_W+7-W){d_i[W-1]}}, d_i}
                           : -{{(ACC_W+7-W){d_i[W-1]}}, d_i};
        waccQ = t64(6'd63) ? {{(ACC_W+7-W){d_q[W-1]}}, d_q}
                           : -{{(ACC_W+7-W){d_q[W-1]}}, d_q};
        for (tj = 0; tj < 63; tj = tj + 1) begin
            if (t64(tj)) begin
                waccI = waccI + {{(ACC_W+7-W){sfd_si[tj+1][W-1]}}, sfd_si[tj+1]};
                waccQ = waccQ + {{(ACC_W+7-W){sfd_sq[tj+1][W-1]}}, sfd_sq[tj+1]};
            end else begin
                waccI = waccI - {{(ACC_W+7-W){sfd_si[tj+1][W-1]}}, sfd_si[tj+1]};
                waccQ = waccQ - {{(ACC_W+7-W){sfd_sq[tj+1][W-1]}}, sfd_sq[tj+1]};
            end
        end
    end
    wire [2*(ACC_W+7)-1:0] sfd_E = waccI*waccI + waccQ*waccQ;

    integer pp, pj;

    always @(posedge clk) begin
        if (!rst_n) begin
            state       <= ST_SCAN;
            smp_cnt     <= 16'd0;
            chip_i      <= {W{1'b0}};
            chip_q      <= {W{1'b0}};
            chip_dv     <= 1'b0;
            detect      <= 1'b0;
            frame_start <= 1'b0;
            locked_phase<= 4'd0;
            sfd_n       <= 7'd0;
            sfd_found   <= 1'b0;
            fs_pending  <= 1'b0;
            wait_cnt    <= {SFD_WAIT{1'b0}};
            drain_cnt   <= {FRAME_DRAIN{1'b0}};
            for (pp = 0; pp < 16; pp = pp + 1) begin
                accR[pp]  <= {R_W{1'b0}};
                accI[pp]  <= {R_W{1'b0}};
                bestR[pp] <= {(R_W+1){1'b0}};
                mcnt[pp]  <= 6'd0;
                bcnt[pp]  <= 16'd0;
                accE[pp]    <= {E_W{1'b0}};
                prevE[pp]   <= {E_W{1'b0}};
                pending[pp] <= 1'b0;
                ready[pp]   <= 1'b0;
                axc[pp]     <= {R_W{1'b0}};
                axis_ok[pp] <= 1'b0;
                ecnt[pp]    <= 4'd0;
                eh1_i[pp]   <= {W{1'b0}};
                eh1_q[pp]   <= {W{1'b0}};
                eh2_i[pp]   <= {W{1'b0}};
                eh2_q[pp]   <= {W{1'b0}};
                oh1_i[pp]   <= {W{1'b0}};
                oh1_q[pp]   <= {W{1'b0}};
                oh2_i[pp]   <= {W{1'b0}};
                oh2_q[pp]   <= {W{1'b0}};
                for (pj = 0; pj < 32; pj = pj + 1) begin
                    prevI[pp][pj] <= {W{1'b0}};
                    prevQ[pp][pj] <= {W{1'b0}};
                    curI[pp][pj]  <= {W{1'b0}};
                    curQ[pp][pj]  <= {W{1'b0}};
                end
            end
            for (pp = 0; pp < 64; pp = pp + 1) begin
                sfd_si[pp] <= {W{1'b0}};
                sfd_sq[pp] <= {W{1'b0}};
            end
        end else if (dv_in) begin
            smp_cnt     <= smp_cnt + 16'd1;
            frame_start <= 1'b0;
            chip_dv     <= 1'b0;
            wait_cnt    <= (state == ST_SCAN || sfd_found) ? {SFD_WAIT{1'b0}}
                                                            : wait_cnt + 1'b1;
            drain_cnt   <= (state == ST_SCAN || !sfd_found) ? {FRAME_DRAIN{1'b0}}
                                                            : drain_cnt + 1'b1;

            if (ext_lock_en && state == ST_SCAN) begin
                // 外部定时直锁: 跳过 16 候选扫描, 直接用外部相位去交错
                state        <= ST_LOCK;
                lphase       <= ext_lock_phase;
                locked_phase <= ext_lock_phase;
                detect       <= 1'b1;
            end else case (state)
                ST_SCAN: begin
                    // 每拍服务 (16 相位候选全覆盖): 位置 q 的采样服务偶链候选 q
                    // 与奇链候选 (q+4)%16 —— 每候选每 16 采样被服务 2 次 (一偶一奇)
                    begin
                        // ---- 偶候选 cand_e 服务 ----
                        curI[cand_e][me] <= e_vi;
                        curQ[cand_e][me] <= e_vq;
                        if (e_done) begin
                            accR[cand_e]  <= {R_W{1'b0}};
                            accI[cand_e]  <= {R_W{1'b0}};
                            bestR[cand_e] <= (mag_e > bestR[cand_e]) ? mag_e : bestR[cand_e];
                            mcnt[cand_e]  <= 6'd0;
                            bcnt[cand_e]  <= bcnt[cand_e] + 16'd1;
                            // 归一化判据: 本块 r 与本块累计能量存档; 连续 2 块合格才置 ready
                            prevE[cand_e]   <= accE_e_nxt;
                            accE[cand_e]    <= {E_W{1'b0}};
                            pending[cand_e] <= ratio_ok_e;
                            ready[cand_e]   <= ready[cand_e] | (ratio_ok_e & pending[cand_e]);
                            for (pj = 0; pj < 31; pj = pj + 1) begin
                                prevI[cand_e][pj] <= curI[cand_e][pj];
                                prevQ[cand_e][pj] <= curQ[cand_e][pj];
                            end
                            prevI[cand_e][31] <= e_vi;   // 本采样片入 prev
                            prevQ[cand_e][31] <= e_vq;
                        end else begin
                            accR[cand_e] <= accR_e_nxt;
                            accI[cand_e] <= accI_e_nxt;
                            accE[cand_e] <= accE_e_nxt;
                            mcnt[cand_e] <= me + 6'd1;
                        end
                        // ---- 轴判别 (独立于共轭积块): 每 16 次偶服务评估一次 ----
                        eh2_i[cand_e] <= eh1_i[cand_e];
                        eh2_q[cand_e] <= eh1_q[cand_e];
                        eh1_i[cand_e] <= e_vi;
                        eh1_q[cand_e] <= e_vq;
                        if (ecnt[cand_e] == 4'd15) begin
                            axis_ok[cand_e] <= (axc[cand_e] + dstep_e) > 0;
                            axc[cand_e]  <= {R_W{1'b0}};
                            ecnt[cand_e] <= 4'd0;
                        end else begin
                            axc[cand_e]  <= axc[cand_e] + dstep_e;
                            ecnt[cand_e] <= ecnt[cand_e] + 4'd1;
                        end
                        // ---- 奇候选 cand_o 服务 ----
                        curI[cand_o][mo] <= o_vi;
                        curQ[cand_o][mo] <= o_vq;
                        if (o_done) begin
                            accR[cand_o]  <= {R_W{1'b0}};
                            accI[cand_o]  <= {R_W{1'b0}};
                            bestR[cand_o] <= (mag_o > bestR[cand_o]) ? mag_o : bestR[cand_o];
                            mcnt[cand_o]  <= 6'd0;
                            bcnt[cand_o]  <= bcnt[cand_o] + 16'd1;
                            prevE[cand_o]   <= accE_o_nxt;
                            accE[cand_o]    <= {E_W{1'b0}};
                            pending[cand_o] <= ratio_ok_o;
                            ready[cand_o]   <= ready[cand_o] | (ratio_ok_o & pending[cand_o]);
                            for (pj = 0; pj < 31; pj = pj + 1) begin
                                prevI[cand_o][pj] <= curI[cand_o][pj];
                                prevQ[cand_o][pj] <= curQ[cand_o][pj];
                            end
                            prevI[cand_o][31] <= o_vi;
                            prevQ[cand_o][31] <= o_vq;
                        end else begin
                            accR[cand_o] <= accR_o_nxt;
                            accI[cand_o] <= accI_o_nxt;
                            accE[cand_o] <= accE_o_nxt;
                            mcnt[cand_o] <= mo + 6'd1;
                        end
                        // ---- 轴判别 (奇侧): 同法但取负 (正确 → −4, 轴互换 → +4) ----
                        oh2_i[cand_o] <= oh1_i[cand_o];
                        oh2_q[cand_o] <= oh1_q[cand_o];
                        oh1_i[cand_o] <= o_vi;
                        oh1_q[cand_o] <= o_vq;
                        axc[cand_o]   <= axc[cand_o] - dstep_o;
                        // ---- 锁定判定 (与镜像一致: 每拍评估) ----
                        if (lock_now) begin
                            state        <= ST_LOCK;
                            lphase       <= pmax;
                            locked_phase <= pmax;
                            detect       <= 1'b1;
                            sfd_n        <= 7'd0;
                            sfd_found    <= 1'b0;
                            fs_pending   <= 1'b0;
                        end
                    end
                end

                ST_LOCK: begin
                    // 帧尾闭环: deframer 报告帧结束 → 回扫描态, 为下一帧重新扫描。
                    // 无可选反馈则帧后将卡死 (只能复位恢复); 假前导场景由超时兑底。
                    if (frame_done || hold_expire || scan_restart) begin
                        state      <= ST_SCAN;
                        detect     <= 1'b0;
                        sfd_n      <= 7'd0;
                        sfd_found  <= 1'b0;
                        fs_pending <= 1'b0;
                        // 扫描器状态清零: 不清则 bcnt/bestR/ready 残留会立即误锁
                        for (pp = 0; pp < 16; pp = pp + 1) begin
                            accR[pp]  <= {R_W{1'b0}};
                            bestR[pp] <= {R_W{1'b0}};
                            mcnt[pp]  <= 6'd0;
                            bcnt[pp]  <= 16'd0;
                            accE[pp]    <= {E_W{1'b0}};
                            prevE[pp]   <= {E_W{1'b0}};
                            pending[pp] <= 1'b0;
                            ready[pp]   <= 1'b0;
                            axc[pp]     <= {R_W{1'b0}};
                            axis_ok[pp] <= 1'b0;
                            ecnt[pp]    <= 4'd0;
                            eh1_i[pp]   <= {W{1'b0}};
                            eh1_q[pp]   <= {W{1'b0}};
                            eh2_i[pp]   <= {W{1'b0}};
                            eh2_q[pp]   <= {W{1'b0}};
                            oh1_i[pp]   <= {W{1'b0}};
                            oh1_q[pp]   <= {W{1'b0}};
                            oh2_i[pp]   <= {W{1'b0}};
                            oh2_q[pp]   <= {W{1'b0}};
                        end
                    end else if (emit_chip) begin
                        chip_i      <= d_i;
                        chip_q      <= d_q;
                        chip_dv     <= 1'b1;
                        frame_start <= fs_pending;   // 上一片判决通过 → 本片同拍
                        fs_pending  <= 1'b0;
                        for (pp = 0; pp < 63; pp = pp + 1) begin
                            sfd_si[pp] <= sfd_si[pp+1];
                            sfd_sq[pp] <= sfd_sq[pp+1];
                        end
                        sfd_si[63] <= d_i;
                        sfd_sq[63] <= d_q;
                        sfd_n <= (sfd_n < 7'd127) ? sfd_n + 7'd1 : sfd_n;
                        // 判决: sfd_E = 全窗相关 [n-63..n] (零填充); n>=63 时评估
                        if (!sfd_found && sfd_n >= 7'd63 && sfd_E >= sfd_thresh) begin
                            fs_pending <= 1'b1;
                            sfd_found  <= 1'b1;
                        end
                    end
                end

                default: state <= ST_SCAN;
            endcase
        end
    end
endmodule
