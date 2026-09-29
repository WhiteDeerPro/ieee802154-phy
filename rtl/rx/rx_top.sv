// rx_top.sv —— RX 数字基带顶层 (Phase 3 集成)
// ---------------------------------------------------------------------------
// 链路: ADC(12bit) → MF → cfo_rot → preamble_sync → despreader → rx_deframer → PSDU
//                        ↑ cfo_est
//
// 与验证用 TB (mc_cfo_tb) 的关键差异 —— 本模块是可综合的"真实"拓扑:
//   * 数据通路 (MF/rot/sync/despread/deframer) 完全自含, 无需外部干预。
//   * CFO 估计的**触发源**需外部提供 (est_start): cfo_est 的 cref 相位对齐要求
//     收集窗起点 = 帧起点 (绝对对齐), free-running 周期触发撞上对齐窗需 ~1024 帧
//     (帧间隔 mod 2048 的漂移步长), 不可用。真实触发源应为**前导能量检测**
//     (见 docs/08 I-6), 验证阶段由 TB 以"帧起点"信息驱动。
//   * rot_load 保留端口: 当前相位无失判据下不必须, 默认可悬空接 0。
// ---------------------------------------------------------------------------
`timescale 1ns/1ps
module rx_top #(
    parameter W = 21,                                  // MF 输出位宽
    parameter PW = 24,                                 // 相位定点位宽 (满量程 2π)
    parameter integer EST_PERIOD = 2048,               // (保留) CFO 估计轮转周期
    // |inc| 上限: 对估计算术留余量，但不放宽到物理上无意义的范围。
    // 450 kHz 时 phase_inc = 471859.2，原限 471859 会把它判超限 (实测: CFO=450 kHz 的
    // 正确估计 +448~453 kHz 因此全被丢弃，只能靠非相干解扩降级工作 31/50)。
    // 取 500 kHz 留出 ~50 kHz 余量。
    parameter signed [PW-1:0] INC_LIMIT = 24'sd524288,
    // 一致性确认: 相邻两次估计相差在 INC_TOL 内才采纳。
    // 动机 (实测): cfo_est 的**首次**估计不可靠 (CFO=0 时 -365 kHz, 100 kHz 时符号错 -103 kHz,
    // 450 kHz 时 -22 kHz); 单次采纳会把错误值写进 inc_reg 并污染后续帧
    // (100 kHz 自主触发 0/50)。不采纳是安全的 —— 未消旋时非相干解扩仍能降级工作。
    // INC_TOL 默认 ~16 kHz (估计噪声实测 ~4 kHz)。
    parameter signed [PW-1:0] INC_TOL   = 24'sd16777,
    // 连续 INC_CONFIRM 次估计落在 INC_TOL 内才采纳。实测: 2 次不够 —— CFO=450 kHz 时
    // -337.6/-341.2 kHz 两个「彼此自洽但都错」的估计会触发采纳 (31/50 -> 0/50)。
    // 需要 3 次是因为错误序列很少能连续三次跟前。
    parameter integer         INC_CONFIRM = 1,
    // 采纳前要求估计质量达标 (cfo_est 的 est_ok: 对齐峰占 8 候选总能量 >= 1/4)。
    // 实测: 仅靠"两次一致"挡不住 CFO=450 kHz 时 -337/-341 kHz 这类**彼此自洽但都错**的
    // 估计 (31/50 -> 0/50); 而"三次一致"会因未消旋下对齐漂移导致一致链断裂
    // (100 kHz: 34/50 -> 0/50)。质量是能同时分开这两类序列的判据。
    parameter                 EST_REQ_QUAL = 1'b1,
    // ---------------- 跨帧投票累积器 (acquisition) ----------------
    // 动机: 冷启动时单帧估计不可靠 —— 未消旋下每帧都是“无偏但有噪”的样本。
    // 方案 C 修好对齐后这一点才成立（之前对齐本身在漂，样本有偏、无法累积）：
    // 实测 CFO=100 kHz 的前 15 次估计中位数 = 72.3 kHz 而真值 100 kHz。
    // 做法: 把 [−INC_LIMIT, +INC_LIMIT] 均匀分箱，每次估计给所在 bin 投一票;
    //       某 bin 达 VOTE_TH 票 → 采纳该 bin 中心并清空所有票。
    // 离线验证 (model/out/cfo_trigger/report.md §10): 64 bin + 3 票 → 第 17 次采纳,
    //       中心 +101.6 kHz (残差 1.6 kHz); 而 2 票会在第 8 次误采纳到 −273 kHz。
    // 面积: 64 × 5bit 计数器 + 一个加法器 (无乘法、无排序、无除法)。
    parameter integer         VOTE_SHIFT = 14,      // bin 宽 = 2^14 = 15.625 kHz
    parameter integer         VOTE_TH    = 3,       // 达此票数即采纳
    parameter                 USE_VOTE   = 1'b0,    // 投票累积 (实测在当前架构下无效: 未消旋时估计 bin 分散, 见 report §10)
    // CFO 估计收集窗配置。**两套** —— 触发源不同，窗起点相对帧起点的偏移不同:
    //   外部触发 (trig_ext=1): 触发点在帧起点附近 (TB 用 fdelay 精确控制) → chip_off=0
    //   自主触发 (trig_ext=0): detect 完成于帧起点后 538 采样 = 8x67+3 (结构常数)
    //                          → chip_off=3 (码片部分 mod 32), 余数 3 由 p_hat 吸收
    //   实测: 自主触发下 chip_off 用 0 时 cref 索引错位, CFO=0 也只有 0/50;
    //         用 3 时 50/50。见 model/out/cfo_trigger/report.md §3。
    //   skip_t3: 原设计针对"start 在帧起点前" (t=3 对应前导外的码片 -1 槽位)。
    //   自主触发时触发点已在帧内, t=3 是有效片, 必须置 0 (实测 31/50 vs 50/50)。
    parameter [7:0]   EST_CHIP_OFF      = 8'd0,   // 外部触发
    parameter [7:0]   EST_CHIP_OFF_AUTO = 8'd3,   // 自主触发
    parameter integer EST_NSMP          = 2048,
    parameter         EST_SKIP_T3       = 1'b1,   // 外部触发
    parameter         EST_SKIP_T3_AUTO  = 1'b0    // 自主触发
) (
    input  wire               clk,
    input  wire               rst_n,
    // ---- ADC 接口 (12bit I/Q @16 Msps) ----
    input  wire signed [11:0] adc_i,
    input  wire signed [11:0] adc_q,
    input  wire               adc_dv,
    // ---- 配置 ----
    input  wire               cfo_en,       // 1: 启用消旋; 0: inc=0 旁路
    input  wire               est_start,    // 外部触发 (诊断/对比用, trig_ext=1 时生效)
    input  wire               rot_load,     // 单拍脉冲: 相位累加器归零 (可接 0)
    input  wire               trig_ext,     // 0: 用内部检测器触发 (自主); 1: 用外部 est_start
    input  wire [47:0]        ph_thresh,    // 前导锁定门限
    input  wire [47:0]        sfd_thresh,   // SFD 相关门限
    // ---- 状态 ----
    output wire               detect,
    output wire [3:0]         locked_phase,
    output wire               frame_start,
    output wire               busy,
    output wire               pd_det,       // 前导检测器输出 (观测用; 后续可接 est_start)
    // ---- 数据输出 ----
    output wire [7:0]         data_out,
    output wire               data_valid,
    output wire [7:0]         psdu_len,
    output wire               fcs_ok,
    output wire               frame_done
);
    // ---------------- 匹配滤波 ----------------
    wire signed [W-1:0] mf_i, mf_q;
    wire                mf_dv;
    rx_matched_filter u_mf (
        .clk(clk), .rst_n(rst_n),
        .i_in(adc_i), .q_in(adc_q), .dv_in(adc_dv),
        .i_out(mf_i), .q_out(mf_q), .dv_out(mf_dv)
    );

    // ---------------- CFO 估计 (触发: 内部检测器 / 外部) ----------------
    wire                 est_done, est_ok;
    wire [2:0]           p_hat;
    wire signed [PW-1:0] phase_inc, phase_off;
    reg  signed [PW-1:0] inc_reg;
    reg  signed [PW-1:0] inc_prev;      // 上一次采纳范围合法的估计 (一致性确认用)
    reg                  inc_prev_v;    // 上一次估计是否有效
    reg  [3:0]           inc_agree_cnt; // 当前连续一致次数
    // 跨帧投票累积器: [−INC_LIMIT, +INC_LIMIT] 分 64 个 bin, 每估计一票
    reg  [4:0]           votes [0:63];
    integer              vk;
    wire [PW-1:0]        bin_off = phase_inc + INC_LIMIT - 1'b1;   // 偏到 [0, 2·INC_LIMIT)
    wire [5:0]           bin_now = bin_off[19:VOTE_SHIFT];        // 6 位 -> 64 bin
    wire signed [PW-1:0] bin_ctr = {4'b0, bin_now, {VOTE_SHIFT{1'b0}}}
                                   - INC_LIMIT + (24'sd1 <<< (VOTE_SHIFT-1));
    wire                 fd_int;         // deframer → sync 的帧尾闭环 (前向声明)

    // 内部触发: sync 的 detect 上升沿 + 6 拍延迟。
    // detect 相对帧起点偏移实测 538±1 采样 (结构决定: ready 需连续 2 块=512 + 相位对齐),
    // 比独立检测器 (±18) 精确一个量级。延迟 6 拍使收集窗起点落在整片边界 (544 = 68×8),
    // 对应 EST_CHIP_OFF = 68 (偶数, 满足 cref 奇偶相位)。
    localparam [14:0] TRIG_TIMEOUT  = 15'd20000;
    localparam [3:0]  TRIG_DLY_N    = 4'd2;      // detect 后延迟 6 拍 (0..5)

    reg        det_d, trig_pend;
    reg [3:0]  trig_dly;
    reg [14:0] trig_to;
    reg        trig_busy;
    wire       det_rise = detect & ~det_d;
    wire       est_start_i = trig_ext ? est_start
                                      : (trig_pend && trig_dly == TRIG_DLY_N);

    always @(posedge clk) begin
        if (!rst_n) begin
            det_d     <= 1'b0;
            trig_pend <= 1'b0;
            trig_dly  <= 4'd0;
            trig_busy <= 1'b0;
            trig_to   <= 15'd0;
        end else begin
            det_d <= detect;
            if (det_rise && !trig_busy) begin
                trig_pend <= 1'b1;
                trig_dly  <= 4'd0;
            end else if (trig_pend) begin
                if (trig_dly == TRIG_DLY_N) trig_pend <= 1'b0;
                else trig_dly <= trig_dly + 4'd1;
            end
            if (est_start_i) begin
                trig_busy <= 1'b1;
                trig_to   <= 15'd0;
            end else if (trig_busy) begin
                if (fd_int || trig_to >= TRIG_TIMEOUT) begin
                    trig_busy <= 1'b0;
                    trig_to   <= 15'd0;
                end else
                    trig_to <= trig_to + 15'd1;
            end
        end
    end

    always @(posedge clk) begin
        if (!rst_n) begin
            inc_reg   <= {PW{1'b0}};
            inc_prev  <= {PW{1'b0}};
            inc_prev_v <= 1'b0;
            inc_agree_cnt <= 4'd0;
            for (vk = 0; vk < 64; vk = vk + 1) votes[vk] <= 5'd0;
        end
        // 门控: 物理范围外的估计直接丢弃 (保持旧值); 堵住"收集窗落在数据段"的垃圾估计
        else if (est_done && cfo_en &&
                 phase_inc <= INC_LIMIT && phase_inc >= -INC_LIMIT) begin
            // 路径 A (快速): 质量门控 —— 单次“好估计”即采纳。
            // 实测: 未消旋下估计呈“宽而平”分布 (−400~+400 kHz), 等 3 次同 bin 很难;
            // 而等一次高 cf 估计容易得多 (CFO=100 kHz 第 15 次 cf=0.890 即收敛)。
            if (!EST_REQ_QUAL || est_ok)
                inc_reg <= phase_inc;
            // 路径 B (慢速补充): 分箱投票。用**实际估计值**而非 bin 中心 ——
            // 用 bin 中心会引入 ±7.8 kHz 量化误差 (实测 CFO=0 会因此从 48/50 降到 35/50)。
            else if (USE_VOTE) begin
                if (votes[bin_now] + 5'd1 >= VOTE_TH[4:0]) begin
                    inc_reg <= phase_inc;
                    for (vk = 0; vk < 64; vk = vk + 1) votes[vk] <= 5'd0;
                end else
                    votes[bin_now] <= votes[bin_now] + 5'd1;
            end
        end
    end

    cfo_est #(.W(W), .PW(PW), .NSMP_P(EST_NSMP)) u_est (
        .clk(clk), .rst_n(rst_n),
        .chip_off(trig_ext ? EST_CHIP_OFF : EST_CHIP_OFF_AUTO),
        .skip_t3 (trig_ext ? EST_SKIP_T3  : EST_SKIP_T3_AUTO),
        .i_in(mf_i), .q_in(mf_q), .dv_in(mf_dv),
        .start(est_start_i),
        .done(est_done), .est_ok(est_ok), .p_hat(p_hat), .phase_inc(phase_inc), .phase_off(phase_off)
    );

    // ---------------- 消旋 ----------------
    wire signed [W-1:0] rot_i, rot_q;
    wire                rot_dv;
    cfo_rot #(.W(W)) u_rot (
        .clk(clk), .rst_n(rst_n),
        .i_in(mf_i), .q_in(mf_q), .dv_in(mf_dv),
        .load(rot_load), .phase_inc(inc_reg), .phase_off({PW{1'b0}}),
        .i_out(rot_i), .q_out(rot_q), .dv_out(rot_dv)
    );

    // ---------------- 前导检测器 (短窗归一化延迟自相关) ----------------
    // 参考实验 model/experiments/run_preamble_detect.py: 该方法在 0–200 kHz CFO
    // 下 Pd=1.00 且对 CFO 免疫。其输出经上升沿检测 + 帧间屏蔽驱动 est_start。
    preamble_detect #(.W(W)) u_pdet (
        .clk(clk), .rst_n(rst_n),
        .i_in(mf_i), .q_in(mf_q), .dv_in(mf_dv),
        .det_pulse(pd_det)
    );

    // ---------------- 同步 / 解扩 / 解帧 ----------------
    wire signed [20:0] chip_i, chip_q;
    wire               chip_dv;

    preamble_sync #(.W(W)) u_sync (
        .clk(clk), .rst_n(rst_n),
        .i_in(rot_i), .q_in(rot_q), .dv_in(rot_dv),
        .ph_thresh(ph_thresh), .sfd_thresh(sfd_thresh),
        .frame_done(fd_int),
        .chip_i(chip_i), .chip_q(chip_q), .chip_dv(chip_dv),
        .detect(detect), .frame_start(frame_start), .locked_phase(locked_phase)
    );

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
