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
// ⚠ 相位必须**每帧**更新（不是只 latch 第一次）：帧到达相位逐帧不同
// （gap 抖动 → mod 16 随机），“只锁一次”的代价是除首帧外全部衰减——
// 实测 SFD 峰值 6.8e13 → 3.9e13 → 1.7e13 → 1.8e13（逐帧掉），只有首帧
// 能定界。更新时机限定在**帧到达后的前导窗口**（见下），避开帧内重锁的污染。
// 相位与旋性无关（同一份 IQ 的帧定时对所有通道相同）→ 仍可共享一份。
//
// SYNC_DIRECT：0 = 扫描取相位; 1 = 相位由 ext_lock_phase 直接给定（无扫描）。
// ---------------------------------------------------------------------------
`timescale 1ns/1ps
module rx_frontend #(
    parameter W = 21,
    parameter SYNC_DIRECT = 1'b0,
    parameter RST_EN = 1'b0,          // 1: 帧到达（preamble_detect）→ 扫描重启
    parameter integer SEG_TH = 512,   // 前导段确认长度（数据段零星段 ≤480）
    parameter integer WIN    = 1200,  // latch 窗口宽（确认后打开）
    parameter integer PH_SHIFT = 0,   // 锁定值相位修正（采样; 实测最优 off=10 vs 锁定值 8）
    parameter integer ALIGN_GATE = 1  // 1: 网格对齐门（latch 延迟到抽取网格 0/4 拍再生效）
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
    // ---- 前导缓冲读口（I-13; 仅扫描路径例化, 相位外置时 done=0）----
    input  wire [11:0]         pbuf_addr = 12'd0,
    input  wire                pbuf_clr  = 1'b0,
    output wire signed [W-1:0] pbuf_i,
    output wire signed [W-1:0] pbuf_q,
    output wire                pbuf_done,
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
    // MF 输出位宽跟随 W；缩位时高位对齐（LSB_SHIFT = ACC_W - W）——定点重定标
    rx_matched_filter #(.X_W(12), .Y_W(W), .LSB_SHIFT((12+9)-W)) u_mf (
        .clk(clk), .rst_n(rst_n),
        .i_in(adc_i), .q_in(adc_q), .dv_in(adc_dv),
        .i_out(mf_i), .q_out(mf_q), .dv_out(mf_dv)
    );

    // ---------------- 相位来源：外置 或 扫描 ----------------
    wire [3:0] scan_phase;
    wire pd_rst;                 // 前导检测电平（仅 RST_EN=1 的扫描路径使用）
    generate
        if (SYNC_DIRECT) begin : g_direct
            assign detect     = ext_lock_en;
            assign scan_phase = ext_lock_phase;
            assign pbuf_i     = {W{1'b0}};
            assign pbuf_q     = {W{1'b0}};
            assign pbuf_done  = 1'b0;
        end else begin : g_scan
            // 帧到达检测（归一化延迟自相关, 对 CFO 免疫）。
            // ⚠ det_pulse 是**电平**（前导段持续 ~1700 采样, 数据段零星段 ≤480）
            //   而非单拍事件——重构见 latch 段注释（上升沿 restart + 段确认门控）。
            preamble_detect #(.W(W)) u_pd (
                .clk(clk), .rst_n(rst_n),
                .i_in(mf_i), .q_in(mf_q), .dv_in(mf_dv),
                .det_pulse(pd_rst)
            );
            // 电平上升沿 = 一个活跃段开始 → scan_restart（每段一次）。
            // 数据段的假 restart 无害: latch 门控保证其锁定不会改写 phase_fix。
            reg pd_d;
            always @(posedge clk) begin
                if (!rst_n) pd_d <= 1'b0;
                else if (mf_dv) pd_d <= pd_rst;
            end
            wire pd_rise = pd_rst & ~pd_d;     // 帧到达沿（不受 RST_EN 门控, pbuf 也用）
            wire scan_rst = RST_EN ? pd_rise : 1'b0;
            // 只用它的相位输出: 码片端口悬空, frame_done 接 0（保持扫描, 不复位）。
            // RST_EN 时 SFD_WAIT 放长 (2^15 > 帧周期): 共享前端里 SFD 永不触发,
            // 若用原 2^12 超时会在帧内回扫→重锁→改写相位; 改由"下一帧 restart"接管。
            preamble_sync #(.W(W), .SFD_WAIT(RST_EN ? 15 : 12)) u_sync (
                .clk(clk), .rst_n(rst_n),
                .i_in(mf_i), .q_in(mf_q), .dv_in(mf_dv),
                .ph_thresh(ph_thresh), .sfd_thresh(sfd_thresh),
                .frame_done(1'b0),
                .ext_lock_en(1'b0), .ext_lock_phase(4'd0),
                .scan_restart(scan_rst),
                .chip_i(), .chip_q(), .chip_dv(),
                .detect(detect), .frame_start(), .locked_phase(scan_phase)
            );
            // —— 前导缓冲（I-13）: MF 采样级环形缓冲, 帧到达沿触发 ——
            preamble_buf #(.W(W)) u_pbuf (
                .clk(clk), .rst_n(rst_n),
                .i_in(mf_i), .q_in(mf_q), .dv_in(mf_dv),
                .trig(pd_rise), .clr(pbuf_clr),
                .rd_addr(pbuf_addr),
                .rd_i(pbuf_i), .rd_q(pbuf_q), .done(pbuf_done)
            );
        end
    endgenerate

    // ---------------- 相位 latch（每个 detect 上升沿重取）----------------
    // 帧到达相位逐帧不同 ⇒ 相位必须**每帧**重取。已知残余问题：preamble_sync 在
    // 未消旋输入上 SFD 永不触发，会周期性超时→重扫→在**帧内数据**上再次“锁定”，
    // 若其 detect 上升沿落在帧中，phase_fix 会被改写成无意义值（该帧失败）。
    // 曾试过“用 preamble_detect 的帧到达脉冲开窗（2800 采样）过滤”，在 MC 里把
    // 帧成功率从 ~42% 提到 ~49%，但窗口时机与扫描锁定不匹配会破坏既有 cocotb
    // 回归（单帧场景扫描需 >2800 采样）—— 暂回退，留作待打磨项（见 docs/16）。
    // —— RST_EN 的 latch 门控（2026-10-01 实测标定; SEG_TH/WIN 为头部参数, 可 -pvalue 覆盖）——
    // 段长 ≥ SEG_TH 才确认"前导段"（数据段零星段 99% ≤435、最大 ~480;
    // 前导段 ≥1600）。确认后打开 latch 窗口; 本帧 latch 一次即冻结——
    // 数据段任何锁定都不能改写 phase_fix。时序: restart@帧起点+460 → 重扫
    // 锁定@+1484（段确认@+972 已开窗）→ 采用 → SFD 起获得正确相位。
    reg [12:0] hi_cnt;
    reg [11:0] win_cnt;
    reg        latch_armed;
    reg [3:0] phase_fix;
    reg       phase_valid;
    reg        latch_pend;        // 网格对齐门: latch 挂起中
    reg [3:0]  latch_pend_val;    // 挂起的目标相位
    wire [3:0] deint_s16;         // 去交错自由计数器当前相位
    reg       detect_d;
    always @(posedge clk) begin
        if (!rst_n) begin
            phase_fix   <= 4'd0;
            phase_valid <= 1'b0;
            latch_pend  <= 1'b0;
            detect_d    <= 1'b0;
            hi_cnt      <= 13'd0;
            win_cnt     <= 12'd0;
            latch_armed <= 1'b0;
        end else begin
            detect_d <= detect;
            if (RST_EN && mf_dv) begin
                // 窗口超时（先判, 后置位覆盖——确认当拍必 arm）
                if (win_cnt != 12'd0) win_cnt <= win_cnt - 1'b1;
                else                  latch_armed <= 1'b0;
                if (pd_rst) begin
                    if (hi_cnt < SEG_TH) hi_cnt <= hi_cnt + 1'b1;
                    if (hi_cnt == SEG_TH - 1) begin
                        latch_armed <= 1'b1;
                        win_cnt     <= WIN - 1;   // 打开窗口: 不依赖段结束的瞬时电平
                    end
                end else begin
                    hi_cnt <= 13'd0;              // 段计数清零; armed 交给窗口超时
                end
            end
            if (SYNC_DIRECT) begin
                phase_fix   <= ext_lock_phase;
                phase_valid <= 1'b1;
            end else if (detect && !detect_d) begin
                if (!RST_EN || latch_armed) begin
                    if (ALIGN_GATE && RST_EN) begin
                        // 网格对齐门(仅 RST_EN 的新扫描路径): 不立即写, 挂起到"抽取点(落点0)或其次4拍(落点4)"再写。
                        // 边界扫描实测(dev1): 切换点在抽取网格 0/4 → 50/50; 8 → 44; 12 → 32。
                        // RST_EN=0 的旧单帧路径保持立即写（cocotb 回归时序依赖）
                        latch_pend     <= 1'b1;
                        latch_pend_val <= scan_phase + PH_SHIFT[3:0];
                    end else begin
                        phase_fix   <= scan_phase + PH_SHIFT[3:0];  // 本帧的锁定相位（含修正）
                        phase_valid <= 1'b1;
                    end
                    latch_armed <= 1'b0;        // 本帧额度用完 → 冻结
                    win_cnt     <= 12'd0;
                end
            end
            // 网格对齐门: 自由计数器到达目标相位(落点0)或目标+4(落点4)的那一拍写出
            if (!SYNC_DIRECT && latch_pend &&
                (deint_s16 == latch_pend_val || deint_s16 == latch_pend_val + 4'd4)) begin
                phase_fix   <= latch_pend_val;
                phase_valid <= 1'b1;
                latch_pend  <= 1'b0;
            end
        end
    end
    assign phase_out = phase_fix;

    // ---------------- 持续去交错（不中断）----------------
    deinterleave #(.W(W)) u_deint (
        .clk(clk), .rst_n(rst_n),
        .i_in(mf_i), .q_in(mf_q), .dv_in(mf_dv),
        .phase(phase_fix),
        .chip_i(chip_i), .chip_q(chip_q), .chip_dv(chip_dv),
        .s16_out(deint_s16)
    );
endmodule
