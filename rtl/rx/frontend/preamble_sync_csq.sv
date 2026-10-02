// preamble_sync_csq.sv —— Csq 方案: 能量定相（粗搜）+ 单路相干验证（精搜）
// =====================================================================
// 两级同步结构（依据 model/out/ps_arch + ps_e2e, 见 notes §22/§23/§24）:
//   ① 粗搜（能量定相）: 16 相能量累加器 eacc[0:15]（每拍把 i²+q² 计入相位
//      s_p16）; 窗满 EWIN 拍后选能量最大相位 c_e。能量法对 CFO/绝对相位免疫,
//      且"片对两格点"的结构使正确轴（偶片峰）能量最高 —— model 实测 ≥6dB
//      选相环距 0.00。
//   ② 精搜（单路相干验证）: 用 c_e 服务单个相干引擎（块间共轭积 |Re|+|Im|,
//      归一化 + 连续 2 块 ready）, 过门限即锁定; 3 块内未 ready 则回粗搜重来。
//   锁定后: 同原版——去交错码片流 + SFD 滑窗 (64 片) 定帧边界。
// 时序预算（前导 8 符号 = 2048 采样）: 帧到(scan_restart) → 能量 2 符号
//   → 相干 3 块 = 3 符号 → 第 5 符号锁定 → SFD 满于 +10 符号。
//
// 资源对照（预期）: 候选状态/历史存储 16→1, 相干算术 2 路→1 路, 新增 16 路
//   能量累加与选峰树。编译: 与 preamble_sync.sv **二选一**（同一模块名）。
`timescale 1ns/1ps
module preamble_sync #(
    parameter W = 21,
    parameter SFD_WAIT    = 12,   // 锁定→SFD 等待超时: 2^12 = 4096 采样
    parameter FRAME_DRAIN = 17,   // SFD→frame_done 兑底超时
    parameter NORM_NUM    = 11,   // 归一化门限 γ = NORM_NUM/2^NORM_SHIFT
    parameter NORM_SHIFT  = 5,
    parameter E_W         = 48,   // 块能量位宽
    parameter EWIN        = 512,  // 能量窗拍数（16 相各得 EWIN/16 个样本）
    parameter VWAIT       = 1024  // verify 超时拍数（3 块需 ~768 拍；无 ready 即回粗搜）
) (
    input  wire                    clk,
    input  wire                    rst_n,
    input  wire signed [W-1:0]     i_in,
    input  wire signed [W-1:0]     q_in,
    input  wire                    dv_in,
    input  wire [47:0]             ph_thresh,     // 块间自相关 r 门限 (正, 有符号比较)
    input  wire [47:0]             sfd_thresh,    // SFD 窗能量门限
    input  wire                    frame_done,    // 帧尾事件: 回扫描态
    input  wire                    ext_lock_en,   // 外部定时直锁（同原版语义）
    input  wire [3:0]              ext_lock_phase,
    input  wire                    scan_restart,  // 帧到达 → 粗搜重来（单拍）
    output reg  signed [W-1:0]     chip_i,
    output reg  signed [W-1:0]     chip_q,
    output reg                     chip_dv,
    output reg                     detect,
    output reg                     frame_start,
    output reg  [3:0]              locked_phase
);
    localparam ACC_W   = W + 6;          // 64×|chip| (SFD 窗累加)
    localparam R_W     = 2*W + 6;        // 块间自相关位宽
    localparam EACC_W  = 2*W + 10;       // 能量累加: 单样本 ≤2^(2W-1), 每相 ≤2^5 样本
    localparam ST_SCAN = 2'd0, ST_VERIFY = 2'd1, ST_LOCK = 2'd2;
    // SFD 模板: CHIP[7] (m<32) ++ CHIP[10] (m>=32)——与原版一致
    function t64(input integer m);
        if (m < 32)
            t64 = (32'b10011100001101010010001011101101 >> (31 - m)) & 1;
        else
            t64 = (32'b01111011100011001001011000000111 >> (63 - m)) & 1;
    endfunction

    // ---------------- 公共 ----------------
    reg [1:0]      state;
    reg [15:0]     smp_cnt;
    wire [3:0] s_p16 = smp_cnt[3:0];

    wire signed [W-1:0] v_i = i_in, v_q = q_in;
    wire [2*W:0] v_sq = v_i*v_i + v_q*v_q;      // 单采样能量（无符号）

    // ---------------- ① 粗搜: 16 相能量窗 ----------------
    reg [EACC_W-1:0] eacc [0:15];
    reg [15:0]       ewcnt;
    reg [3:0]        c_e;                        // 选出的相位（锁定后 = lphase 源）

    // 候选能量 = 两格点之和 eacc[c] + eacc[(c+12)%16]（与 model 的 en16 定义一致）:
    //   正确轴 c* 含两个片峰（I 峰 @c*、Q 峰 @c*+12）→ 唯一最大;
    //   若只比"单格点"，I 峰与 Q 峰等强会二选一 → 约一半帧选错轴致 verify 失败。
    reg [3:0]      c_sel;
    reg [EACC_W:0] emax;
    reg [EACC_W:0] esum;
    integer ei;
    always @* begin
        c_sel = 4'd0;
        emax  = eacc[0] + eacc[12];
        for (ei = 1; ei < 16; ei = ei + 1) begin
            esum = eacc[ei] + eacc[(ei + 12) & 15];
            if (esum > emax) begin
                emax  = esum;
                c_sel = ei[3:0];
            end
        end
    end

    // ---------------- ② 精搜: 单候选相干（块间共轭积） ----------------
    // 服务拍: 位置 c_e 采偶片、位置 (c_e+12)%16 采奇片 —— 与"候选 c_e"语义一致。
    // 历史存原始 (i,q)（奇片不预旋转）: 共轭积里"偶-偶 / 奇-奇"配对, 旋转自动抵消。
    wire serve = (s_p16 == c_e) || ((s_p16 + 4'd4) == c_e);

    reg signed [W-1:0] curI  [0:31], curQ  [0:31];
    reg signed [W-1:0] prevI [0:31], prevQ [0:31];
    reg [5:0]          pcnt;                     // 块内片计数 0..31
    reg signed [R_W-1:0] accR, accI;
    reg [E_W-1:0]      accE, prevE;
    reg [R_W:0]        bestR;
    reg                pending, ready;
    reg [15:0]         vcnt;                     // verify 超时计数

    wire signed [R_W-1:0] step  = v_i*prevI[pcnt] + v_q*prevQ[pcnt];
    wire signed [R_W-1:0] stepI = v_q*prevI[pcnt] - v_i*prevQ[pcnt];
    wire signed [R_W-1:0] accR_nxt = accR + step;
    wire signed [R_W-1:0] accI_nxt = accI + stepI;
    wire [R_W-1:0] absR = accR_nxt[R_W-1] ? (~accR_nxt + 1'b1) : accR_nxt;
    wire [R_W-1:0] absI = accI_nxt[R_W-1] ? (~accI_nxt + 1'b1) : accI_nxt;
    wire [R_W:0]   mag  = {1'b0, absR} + {1'b0, absI};
    wire [E_W-1:0] accE_nxt = accE + v_sq;
    wire [E_W+4:0] norm_thr = (NORM_NUM * prevE) >> NORM_SHIFT;
    wire ratio_ok = (mag >= {1'b0, norm_thr});
    wire pc_last  = (pcnt == 6'd31);
    wire [R_W:0] bestR_nxt = (mag > bestR) ? mag : bestR;
    wire ready_nxt = ready | (ratio_ok & pending);
    // 锁定: 连续 2 块合格 + 历史最大过绝对门限（+PH=0 时绝对侧无约束）
    wire lock_now = ready_nxt && (bestR_nxt >= {1'b0, ph_thresh});

    // ---------------- 锁定后（SFD 窗/输出, 与原版一致） ----------------
    reg [3:0]          lphase;
    reg signed [W-1:0] sfd_si [0:63];
    reg signed [W-1:0] sfd_sq [0:63];
    reg [6:0]          sfd_n;
    reg                sfd_found;
    reg                fs_pending;
    reg [SFD_WAIT-1:0]    wait_cnt;
    reg [FRAME_DRAIN-1:0] drain_cnt;
    wire wait_expire  = (&wait_cnt)  && !sfd_found;
    wire drain_expire = (&drain_cnt) &&  sfd_found;
    wire hold_expire  = (state == ST_LOCK) && (wait_expire || drain_expire);

    wire emit_even = (s_p16 == lphase);
    wire emit_odd  = ((s_p16 + 4'd4) == lphase);
    wire emit_chip = emit_even || emit_odd;
    wire signed [W-1:0] d_i = emit_even ? v_i : v_q;              // 奇片: I'=Q
    wire signed [W-1:0] d_q = emit_even ? v_q : (~v_i + 1'b1);    // 奇片: Q'=-I

    // SFD 窗锚定全并行相关（照抄原版）
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
            ewcnt       <= 16'd0;
            c_e         <= 4'd0;
            chip_i      <= {W{1'b0}};
            chip_q      <= {W{1'b0}};
            chip_dv     <= 1'b0;
            detect      <= 1'b0;
            frame_start <= 1'b0;
            locked_phase<= 4'd0;
            pcnt        <= 6'd0;
            accR        <= {R_W{1'b0}};
            accI        <= {R_W{1'b0}};
            accE        <= {E_W{1'b0}};
            prevE       <= {E_W{1'b0}};
            bestR       <= {(R_W+1){1'b0}};
            pending     <= 1'b0;
            ready       <= 1'b0;
            vcnt        <= 16'd0;
            lphase      <= 4'd0;
            sfd_n       <= 7'd0;
            sfd_found   <= 1'b0;
            fs_pending  <= 1'b0;
            wait_cnt    <= {SFD_WAIT{1'b0}};
            drain_cnt   <= {FRAME_DRAIN{1'b0}};
            for (pp = 0; pp < 16; pp = pp + 1) eacc[pp] <= {EACC_W{1'b0}};
            for (pp = 0; pp < 32; pp = pp + 1) begin
                curI[pp]  <= {W{1'b0}};
                curQ[pp]  <= {W{1'b0}};
                prevI[pp] <= {W{1'b0}};
                prevQ[pp] <= {W{1'b0}};
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
                // 外部定时直锁: 跳过两级搜索, 直接用外部相位去交错
                state        <= ST_LOCK;
                lphase       <= ext_lock_phase;
                locked_phase <= ext_lock_phase;
                detect       <= 1'b1;
            end else case (state)
                ST_SCAN: begin
                    // ① 粗搜: 能量累加（每拍 1 相）。窗满拍 = 选峰并清窗（同拍不累加）。
                    if (scan_restart) begin
                        // 帧到达: 清窗重来（本拍样本弃掉, 从下一拍起重新积累）
                        ewcnt <= 16'd0;
                        for (pp = 0; pp < 16; pp = pp + 1)
                            eacc[pp] <= {EACC_W{1'b0}};
                    end else if (ewcnt >= EWIN) begin
                        // 窗满: 选峰（c_sel 基于含至上一拍的全部样本）, 进入精搜
                        c_e    <= c_sel;
                        ewcnt  <= 16'd0;
                        for (pp = 0; pp < 16; pp = pp + 1)
                            eacc[pp] <= {EACC_W{1'b0}};
                        state  <= ST_VERIFY;
                        vcnt   <= 16'd0;
                        pcnt   <= 6'd0;
                        accR   <= {R_W{1'b0}};
                        accI   <= {R_W{1'b0}};
                        accE   <= {E_W{1'b0}};
                        prevE  <= {E_W{1'b0}};
                        bestR  <= {(R_W+1){1'b0}};
                        pending<= 1'b0;
                        ready  <= 1'b0;
                    end else begin
                        eacc[s_p16] <= eacc[s_p16] + v_sq;
                        ewcnt <= ewcnt + 16'd1;
                    end
                end
                ST_VERIFY: begin
                    vcnt <= (vcnt == 16'hFFFF) ? vcnt : vcnt + 16'd1;
                    if (scan_restart) begin
                        state <= ST_SCAN;
                        ewcnt <= 16'd0;
                        for (pp = 0; pp < 16; pp = pp + 1)
                            eacc[pp] <= {EACC_W{1'b0}};
                    end else if (serve) begin
                        // ② 精搜: 单候选相干服务
                        curI[pcnt] <= v_i;
                        curQ[pcnt] <= v_q;
                        if (pc_last) begin
                            bestR   <= bestR_nxt;
                            prevE   <= accE_nxt;
                            accE    <= {E_W{1'b0}};
                            pending <= ratio_ok;
                            ready   <= ready_nxt;
                            for (pj = 0; pj < 31; pj = pj + 1) begin
                                prevI[pj] <= curI[pj];
                                prevQ[pj] <= curQ[pj];
                            end
                            prevI[31] <= v_i;
                            prevQ[31] <= v_q;
                            pcnt <= 6'd0;
                            accR <= {R_W{1'b0}};
                            accI <= {R_W{1'b0}};
                            if (lock_now) begin
                                state        <= ST_LOCK;
                                lphase       <= c_e;
                                locked_phase <= c_e;
                                detect       <= 1'b1;
                                sfd_n        <= 7'd0;
                                sfd_found    <= 1'b0;
                                fs_pending   <= 1'b0;
                            end
                        end else begin
                            accR <= accR_nxt;
                            accI <= accI_nxt;
                            accE <= accE_nxt;
                            pcnt <= pcnt + 6'd1;
                        end
                    end else if (vcnt >= VWAIT) begin
                        // 超时: 无 ready → 回粗搜（清窗）——用 >= 防"serve 拍跳过检查"卡死
                        state <= ST_SCAN;
                        ewcnt <= 16'd0;
                        for (pp = 0; pp < 16; pp = pp + 1)
                            eacc[pp] <= {EACC_W{1'b0}};
                    end
                end
                ST_LOCK: begin
                    if (frame_done || hold_expire || scan_restart) begin
                        state      <= ST_SCAN;
                        detect     <= 1'b0;
                        sfd_n      <= 7'd0;
                        sfd_found  <= 1'b0;
                        fs_pending <= 1'b0;
                        ewcnt      <= 16'd0;
                        for (pp = 0; pp < 16; pp = pp + 1)
                            eacc[pp] <= {EACC_W{1'b0}};
                    end else if (emit_chip) begin
                        chip_i      <= d_i;
                        chip_q      <= d_q;
                        chip_dv     <= 1'b1;
                        frame_start <= fs_pending;
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
                    end
                end
                default: state <= ST_SCAN;
            endcase
        end
    end
endmodule
