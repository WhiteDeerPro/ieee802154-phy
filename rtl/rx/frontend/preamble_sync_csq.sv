// [状态: 现役 (v1.2 起, I-18 转正)] Csq v3b——能量定相 + 连续滑窗相干统计;
//   裁剪后 6,456 cells（-23.2% vs A16）; 边界回归 + 影子对比（460 帧, 交换 3 帧对称）打平。
//   默认 SOURCES 编译; A16 保留为对照变体（build_variants.py）。
// preamble_sync_csq.sv —— Csq-v3b: 能量定相（粗搜）+ **连续消费**相干统计（精搜）
// =====================================================================
// [v3b, 2026-10-02] 精搜由"评估-验证两段式"改为"滑动相关连续统计"：
//   每服务拍（位置 c_e / (c_e+12)%16 的采样）取片 y[k]，计算延迟 32 片的共轭积
//     s[k] = y[k]·conj(y[k−32])
//   维护 32 项滑窗和 S = Σ s（与"块间相关"同一物理量：块版是它对齐块边界的特例）
//   与片能量滑窗 Ewin = Σ|y|²；判据 mag=|Re S|+|Im S| ≥ γ·Ewin 逐片评估，
//   连续 NCNT 片合格即锁定。
//   ⇒ restart 只损失个别片（不作废整段）；无"评估点/窗口连续 512 拍"要求；
//     脱锁后（滑窗在 LOCK 期照常更新）随即可重锁。
//   相位 c_e 由能量窗每 CREF 拍刷新; 变化时清滑窗（片来源变了）。
// 编译: 与 preamble_sync.sv **二选一**（同一模块名, drop-in 替换）。
`timescale 1ns/1ps
module preamble_sync #(
    parameter W = 21,
    parameter SFD_WAIT    = 12,   // 锁定→SFD 等待超时: 2^12 = 4096 采样
    parameter FRAME_DRAIN = 17,   // SFD→frame_done 兑底超时
    parameter NORM_NUM    = 11,   // 归一化门限 γ = NORM_NUM/2^NORM_SHIFT
    parameter NORM_SHIFT  = 5,
    parameter E_W         = 48,
    parameter EWIN        = 512,  // 能量窗拍数（定相用）
    parameter NCNT        = 16,   // 连续合格片数（重叠评估, ≈持久 47 片）
    parameter CREF        = 128,  // 相位刷新周期（拍）
    parameter SFD_EN     = 1'b1  // 1: 生成 SFD 窗/frame_start（单通道）; 0: 裁剪（共享前端下输出悬空）
) (
    input  wire                    clk,
    input  wire                    rst_n,
    input  wire signed [W-1:0]     i_in,
    input  wire signed [W-1:0]     q_in,
    input  wire                    dv_in,
    input  wire [47:0]             ph_thresh,
    input  wire [47:0]             sfd_thresh,
    input  wire                    frame_done,
    input  wire                    ext_lock_en,
    input  wire [3:0]              ext_lock_phase,
    input  wire                    scan_restart,
    output reg  signed [W-1:0]     chip_i,
    output reg  signed [W-1:0]     chip_q,
    output reg                     chip_dv,
    output reg                     detect,
    output reg                     frame_start,
    output reg  [3:0]              locked_phase
);
    localparam ACC_W = W + 6;
    localparam EACC_W = 2*W + 10;        // 能量累加位宽
    localparam ST_SCAN = 2'd0, ST_LOCK = 2'd1;
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

    // ---------------- ① 粗搜: 16 相滑窗能量（512 拍环形缓冲, 定相） ----------------
    localparam VW = 2*W - 8;
    wire [2*W:0] v_sq = v_i*v_i + v_q*v_q;
    wire [VW-1:0] v_sq_c = v_sq[2*W : 9];
    reg [EACC_W:0] eacc [0:15];
    reg [VW-1:0]     ebuf [0:511];
    reg [8:0]        ebp;
    reg [9:0]        warm_cnt;
    reg [9:0]        rst_cnt;
    wire             warm = (warm_cnt >= 10'd512);
    reg [15:0]       ewcnt;
    reg [3:0]        c_e;
    wire [VW-1:0]    v_old = ebuf[ebp];
    reg [3:0]        c_sel;
    reg [EACC_W:0]   emax, esum;
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

    // ---------------- ② 精搜: 连续消费（滑窗相关 + 连续片计数） ----------------
    localparam SW  = 2*W + 2;            // 片共轭积位宽
    localparam EWB = VW;                 // 片能量样本位宽（同 ebuf 截位）
    // 片延迟线（32 片）
    reg signed [W-1:0]  ybi [0:31], ybq [0:31];
    reg [4:0]           ybp;
    // 共轭积滑窗缓冲（32 项）与能量滑窗缓冲（32 项）
    reg signed [SW-1:0] sbi [0:31], sbq [0:31];
    reg [4:0]           sbp;
    reg [EWB-1:0]       ebf [0:31];
    reg [4:0]           eb2p;
    // 滑窗和与计数
    reg signed [SW+5:0] Sre, Sim;
    reg [EWB+5:0]       Ewin;
    reg [5:0]           okcnt;
    reg [5:0]           ywarm;
    reg [7:0]           cref_cnt;

    wire serve = (s_p16 == c_e) || ((s_p16 + 4'd4) == c_e);
    wire signed [W-1:0] yi_o = ybi[ybp], yq_o = ybq[ybp];
    wire signed [SW-1:0] sre_n = v_i*yi_o + v_q*yq_o;
    wire signed [SW-1:0] sim_n = v_q*yi_o - v_i*yq_o;
    wire signed [SW-1:0] sre_o = sbi[sbp], sim_o = sbq[sbp];
    wire signed [SW+5:0] Sre_n = Sre + sre_n - sre_o;
    wire signed [SW+5:0] Sim_n = Sim + sim_n - sim_o;
    wire [EWB-1:0]       e_o   = ebf[eb2p];
    wire [EWB+5:0]       Ewin_n = Ewin + v_sq_c - e_o;   // 片能量滑窗（截位样本）
    wire [SW+5:0] absR = Sre_n[SW+5] ? (~Sre_n + 1'b1) : Sre_n;
    wire [SW+5:0] absI = Sim_n[SW+5] ? (~Sim_n + 1'b1) : Sim_n;
    wire [SW+6:0] mag  = {1'b0, absR} + {1'b0, absI};
    wire [SW+6:0] thr  = (NORM_NUM * Ewin_n) >> NORM_SHIFT;
    wire ratio_ok = (mag >= thr);
    wire [5:0] okcnt_n = ratio_ok ? (okcnt + 1'b1) : 6'd0;
    wire lock_now = (state == ST_SCAN) && (ywarm >= 6'd32) && (okcnt_n >= NCNT[5:0]);

    // ---------------- 锁定后（SFD 窗/输出, 同前版） ----------------
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
    wire signed [W-1:0] d_i = emit_even ? v_i : v_q;
    wire signed [W-1:0] d_q = emit_even ? v_q : (~v_i + 1'b1);

    reg signed [ACC_W+6:0] waccI, waccQ;
    integer tj;
    always @* begin
        if (SFD_EN) begin
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
        end else begin
            waccI = {(ACC_W+7){1'b0}};
            waccQ = {(ACC_W+7){1'b0}};
        end
    end
    wire [2*(ACC_W+7)-1:0] sfd_E = SFD_EN ? (waccI*waccI + waccQ*waccQ)
                                          : {2*(ACC_W+7){1'b0}};

    integer pp, pj;

    always @(posedge clk) begin
        if (!rst_n) begin
            state       <= ST_SCAN;
            smp_cnt     <= 16'd0;
            ewcnt       <= 16'd0;
            c_e         <= 4'd0;
            c_sel       <= 4'd0;
            chip_i      <= {W{1'b0}};
            chip_q      <= {W{1'b0}};
            chip_dv     <= 1'b0;
            detect      <= 1'b0;
            frame_start <= 1'b0;
            locked_phase<= 4'd0;
            lphase      <= 4'd0;
            sfd_n       <= 7'd0;
            sfd_found   <= 1'b0;
            fs_pending  <= 1'b0;
            wait_cnt    <= {SFD_WAIT{1'b0}};
            drain_cnt   <= {FRAME_DRAIN{1'b0}};
            ebp         <= 9'd0;
            warm_cnt    <= 10'd0;
            ybp         <= 5'd0;
            sbp         <= 5'd0;
            eb2p        <= 5'd0;
            Sre         <= {(SW+6){1'b0}};
            Sim         <= {(SW+6){1'b0}};
            Ewin        <= {(EWB+6){1'b0}};
            okcnt       <= 6'd0;
            ywarm       <= 6'd0;
            cref_cnt    <= 8'd0;
            for (pp = 0; pp < 16; pp = pp + 1) eacc[pp] <= {(EACC_W+1){1'b0}};
            for (pp = 0; pp < 512; pp = pp + 1) ebuf[pp] <= {VW{1'b0}};
            for (pp = 0; pp < 32; pp = pp + 1) begin
                ybi[pp] <= {W{1'b0}};  ybq[pp] <= {W{1'b0}};
                sbi[pp] <= {SW{1'b0}}; sbq[pp] <= {SW{1'b0}};
                ebf[pp] <= {EWB{1'b0}};
            end
            for (pp = 0; pp < 64; pp = pp + 1) begin
                if (SFD_EN) begin
                    sfd_si[pp] <= {W{1'b0}};
                    sfd_sq[pp] <= {W{1'b0}};
                end
            end
        end else if (dv_in) begin
            smp_cnt     <= smp_cnt + 16'd1;
            frame_start <= 1'b0;
            chip_dv     <= 1'b0;
            // 能量滑窗（定相用; 任何状态连续更新）
            ebuf[ebp]   <= v_sq_c;
            ebp         <= ebp + 9'd1;
            eacc[s_p16] <= eacc[s_p16] + v_sq_c - v_old;
            if (!warm) warm_cnt <= warm_cnt + 10'd1;
            cref_cnt <= (cref_cnt == CREF[7:0] - 1'b1) ? 8'd0 : cref_cnt + 8'd1;

            // 相位刷新（每 CREF 拍）: 变了则清滑窗（片来源变化）
            if (cref_cnt == CREF[7:0] - 1'b1 && warm && (c_sel != c_e)) begin
                c_e     <= c_sel;
                Sre     <= {(SW+6){1'b0}};
                Sim     <= {(SW+6){1'b0}};
                Ewin    <= {(EWB+6){1'b0}};
                okcnt   <= 6'd0;
                ywarm   <= 6'd0;
                ybp     <= 5'd0;
                sbp     <= 5'd0;
                eb2p    <= 5'd0;
                for (pp = 0; pp < 32; pp = pp + 1) begin
                    sbi[pp] <= {SW{1'b0}}; sbq[pp] <= {SW{1'b0}};
                    ebf[pp] <= {EWB{1'b0}};
                end
            end

            // 连续消费引擎（服务拍; 所有状态更新 —— LOCK 期照常, 支撑无缝重锁）
            if (serve) begin
                ybi[ybp]  <= v_i;
                ybq[ybp]  <= v_q;
                ybp       <= ybp + 5'd1;
                sbi[sbp]  <= sre_n;
                sbq[sbp]  <= sim_n;
                sbp       <= sbp + 5'd1;
                ebf[eb2p] <= v_sq_c;
                eb2p      <= eb2p + 5'd1;
                Sre       <= Sre_n;
                Sim       <= Sim_n;
                Ewin      <= Ewin_n;
                okcnt     <= okcnt_n;
                if (ywarm < 6'd32) ywarm <= ywarm + 6'd1;
            end

            if (ext_lock_en && state == ST_SCAN) begin
                state        <= ST_LOCK;
                lphase       <= ext_lock_phase;
                locked_phase <= ext_lock_phase;
                detect       <= 1'b1;
            end else case (state)
                ST_SCAN: begin
                    // 连续合格即锁定（无评估点/无 verify 态）
                    if (lock_now) begin
                        state        <= ST_LOCK;
                        lphase       <= c_e;
                        locked_phase <= c_e;
                        detect       <= 1'b1;
                        sfd_n        <= 7'd0;
                        sfd_found    <= 1'b0;
                        fs_pending   <= 1'b0;
                    end
                end
                ST_LOCK: begin
                    if (frame_done || hold_expire || scan_restart) begin
                        state      <= ST_SCAN;
                        detect     <= 1'b0;
                        sfd_n      <= 7'd0;
                        sfd_found  <= 1'b0;
                        fs_pending <= 1'b0;
                    end else if (emit_chip) begin
                        chip_i      <= d_i;
                        chip_q      <= d_q;
                        chip_dv     <= 1'b1;
                        frame_start <= SFD_EN ? fs_pending : 1'b0;
                        fs_pending  <= 1'b0;
                        if (SFD_EN) begin
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
                end
                default: state <= ST_SCAN;
            endcase
        end
    end
endmodule
