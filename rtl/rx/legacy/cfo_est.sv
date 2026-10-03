// [状态] 历史/参考实现（2026-10-02 标注）
//   用途：CFO 联合估计（8 相位候选；现行链未接入，由上层估计替代）
//   现行主链路为 rx_dual 链（rx_dual/rx_frontend/rx_chip_backend/…），本文件不在其上；
//   仍被 tb/cfo_corr 等历史测试引用，勿删；重构对照请以 rtl/rx/ 现行模块为准。
// cfo_est.sv —— 前导联合估计: 对齐点 (p_hat) + 载波频偏 (phase_inc)
// ---------------------------------------------------------------------------
// 对应 model/experiments/run_cfo_fix.py 的定点/流式版本。算法:
//   前导是 CHIP[0] 的 8 次重复 (256 码片)。取码片峰值软值 v[m], 除掉已知调制与
//   成形串扰 z[m] = v[m]·conj(c_ref[m]) —— c_ref 是理想前导的定点软值 ROM。
//   z 应是一串纯相位 (CFO 造的), 同一路相邻码片 (m 与 m+2) 的差分
//   d = z[m+2]·conj(z[m]) 相位应全部相等, 且 = 2*pi*f*(2 码片) = 2*pi*f*1us。
//   累加 acc = SUM d -> arg(acc)/(2*pi*1us) = f_hat。
//
// 为什么"联合": CFO 让复相关峰塌陷, 定时会被打瞎 (模型实测 50 kHz 时窗口内峰位
//   偏 17 采样), 先同步再估频偏会取到全错的软值 -> 死锁。这里改成**不依赖任何同步**:
//   直接扫 8 个候选对齐点, 每个都做上面的差分累加, 用质量指标
//       mag2 = |acc|^2 = acc_i^2 + acc_q^2
//   衡量"相位一致性" —— 对齐对了则各 d 同相, |acc| 大; 对齐错了则乱, |acc| 小。
//   取 mag2 最大的候选即为 p_hat。指标不需要开方也不需要除法。
//
// 采样结构 (与 rtl/rx/variants/preamble_sync.sv 完全一致, 便于级联):
//   t 相对 start 计数, k0 = !t[3] (即 t mod 16 < 8);
//   k0 拍上: 候选 ce = t[2:0] 取偶数码片 (I,Q 原值),
//            候选 co = t[2:0]+4 取奇数码片 (-j 旋转: I'=Q, Q'=-I)。
//   故每 16 采样, 每个候选收 2 片 (一偶一奇), 256 片恰好用满 2048 采样 = 前导长度。
//   差分走"同一路": 偶链与奇链各自存历史 —— O-QPSK 的 I/Q 半码片错位让相邻码片的
//   采样间隔在 12/4 采样之间交替, 拍间相位差不等, 不能相干平均。
//
// 输出 phase_inc = arg(acc)/16: 差分跨 2 个码片 = 16 采样, 故每采样相位增量
//   是 arg/16 (除以 16 即算术右移 4, 免费)。
//
// 定点约定: W=21 (与 rx_matched_filter 输出同宽), c_ref 16bit,
//   z 保留 22bit (积 38bit 右移 16), d 保留 27bit, acc 40bit。
`timescale 1ns/1ps
module cfo_est #(
    parameter W  = 21,          // 输入软值位宽 (与 rx_matched_filter 输出一致)
    parameter PW = 24,          // 相位位宽 (满量程 2*pi)
    // 收集窗长 (采样): 默认 2048 = 前导全长。起点偏移后需缩短以免伸入数据段
    // (466 + 2048 > 前导 2048); 减到 1024 时累加项减半, 增益降 √2。
    parameter integer NSMP_P = 2048
) (
    input  wire                    clk,
    input  wire                    rst_n,
    // 以下两个量都取决于**触发源**，故做成运行时输入 (rx_top 按 trig_ext 选)，
    // 不再用编译期参数 —— 否则“外部触发 vs 自主触发”得编译两份镜像。
    //   chip_off: 收集窗起点相对帧起点的整码片偏移 (8 位加法自然回绕, cref 表 256 长)
    //             外部触发(点≈帧起点) = 0; detect 触发(点在帧起点后 538 = 8*67+3) = 3
    //   skip_t3:  skip patch: 原设计针对"start 在帧起点前" (此时 t=3 对应前导外的
    //             "码片 -1" 槽位, 收进来会让整条奇链错位一片)。偏移触发 (start 在帧内)
    //             时 t=3 已是有效片, 应置 0。
    input  wire [7:0]              chip_off,
    input  wire                    skip_t3,
    input  wire signed [W-1:0]     i_in,
    input  wire signed [W-1:0]     q_in,
    input  wire                    dv_in,
    input  wire                    start,       // 单拍脉冲: 从下一拍开始收集前导
    output reg                     done,        // 单拍脉冲: phase_inc / p_hat 就绪
    output reg                     est_ok,      // 估计质量: 选中对齐峰占 8 候选总能量 >= 1/4
    output reg  [2:0]              p_hat,
    output reg  signed [PW-1:0]    phase_inc,
    output reg  signed [PW-1:0]    phase_off   // 帧起点相位 φ0 (供 cfo_rot load 时加载)
);
    `include "c_ref_rom.svh"

    localparam NCHIP = 256;
    localparam NSMP  = NSMP_P;      // 收集窗口 (= 前导长度, 可配见 CHIP_OFF)
    // ---- 定标推导 (峰值条件 v=131488≈2^17, c_ref=16436≈2^14) ----
    //   z = v·conj(c) >> ZS : 积 ~2^31, 右移 16 -> ~2^15 = 33000 (21bit 容器够)
    //   d = z·conj(h) >> DS : 积 ~2^30, DS=6 -> ~2^24
    //   acc = Σ d (126 项)  : ~2^31, 48bit 容器余量充足
    //   CORDIC 输入 = acc >>> CSH: acc 峰值约 2^33, 右移 12 -> ~2^21, 稳在 24bit 内
    //   (早期版本 DS=16 时 acc 只剩 2^21, 再右移 16 给 CORDIC 就只剩 32, 相位全成噪声)
    localparam ZS    = 16;
    localparam DS    = 6;
    localparam CSH   = 12;
    localparam ZW    = W;             // 21
    localparam DW    = 2*ZW + 1;      // 43 (不截断, 交给累加器)
    localparam ACCW  = 48;

    function signed [15:0] cref_i(input [7:0] m); cref_i = C_REF_I[m]; endfunction
    function signed [15:0] cref_q(input [7:0] m); cref_q = C_REF_Q[m]; endfunction

    typedef enum logic [2:0] {IDLE, COLLECT, CMP, CORDA, CORDB} st_e;
    st_e st;

    reg [11:0]       t;
    // 偶链/奇链必须**各自**计数: 候选 c 既在 t≡c (mod 8) 收偶数码片,
    // 又在 t≡c+4 (mod 8) 收奇数码片, 共用一个计数器会互相踩。
    reg [7:0]        mc_e [0:7];    // 已收偶数码片数 -> 码片 m = 2*mc_e
    reg [7:0]        mc_o [0:7];    // 已收奇数码片数 -> 码片 m = 2*mc_o+1
    reg signed [ZW-1:0]   he_i [0:7], he_q [0:7];   // 偶链历史
    reg signed [ZW-1:0]   ho_i [0:7], ho_q [0:7];   // 奇链历史
    reg signed [ACCW-1:0] ai   [0:7], aq   [0:7];   // Σ d
    // 帧起点相位 (φ0) 估计: 所有候选的 z 直接求和。正确对齐候选的 z 同相相干
    // (256 片×~|z|), 其它候选的 z 是散相 → 贡献小; 合计相位 ≈ φ0 + 1020·inc
    // (Σz 的相位中心在码片 m=127.5, 每码片 8·inc)。之后减去 1020·inc 即得 φ0。
    reg signed [ACCW-1:0] zacc_i, zacc_q;

    wire k0    = ~t[3];
    wire [2:0] ce = t[2:0];
    wire [2:0] co = t[2:0] + 3'd4;

    // 本拍两个候选的输入值 (奇码片做 -j 旋转, 与 preamble_sync 一致)
    wire signed [W-1:0] evi = i_in, evq = q_in;
    wire signed [W-1:0] ovi = q_in, ovq = -i_in;

    // ---- 复乘辅助 (用 >>> 显式算术右移, 再截到位宽, 避免手算位选索引出错) ----
    // z = v·conj(c): 积 21+16=37bit, 和 38bit, 右移 ZS=16 -> 21bit
    function automatic signed [ZW-1:0] mulz_i(input signed [W-1:0] vi,
                                              input signed [W-1:0] vq,
                                              input signed [15:0] ci,
                                              input signed [15:0] cq);
        logic signed [W+16:0] s;
        begin
            s = vi * ci + vq * cq;
            mulz_i = s >>> ZS;
        end
    endfunction
    function automatic signed [ZW-1:0] mulz_q(input signed [W-1:0] vi,
                                              input signed [W-1:0] vq,
                                              input signed [15:0] ci,
                                              input signed [15:0] cq);
        logic signed [W+16:0] s;
        begin
            s = vq * ci - vi * cq;
            mulz_q = s >>> ZS;
        end
    endfunction
    // d = z·conj(h): 积 21+21=42bit, 和 43bit, 右移 DS=16 -> 27bit
    function automatic signed [DW-1:0] muld_i(input signed [ZW-1:0] zi,
                                              input signed [ZW-1:0] zq,
                                              input signed [ZW-1:0] hi,
                                              input signed [ZW-1:0] hq);
        logic signed [2*ZW:0] s;
        begin
            s = zi * hi + zq * hq;
            muld_i = s >>> DS;
        end
    endfunction
    function automatic signed [DW-1:0] muld_q(input signed [ZW-1:0] zi,
                                              input signed [ZW-1:0] zq,
                                              input signed [ZW-1:0] hi,
                                              input signed [ZW-1:0] hq);
        logic signed [2*ZW:0] s;
        begin
            s = zq * hi - zi * hq;
            muld_q = s >>> DS;
        end
    endfunction

    // ROM 索引必须用**码片序号 m**, 不是"该链收到的第几片":
    //   候选 ce 收的是偶数码片 m = 0,2,4,...  -> m = 2*mc_e[ce] + CHIP_OFF
    //   候选 co 收的是奇数码片 m = 1,3,5,...  -> m = 2*mc_o[co]+1 + CHIP_OFF
    wire [7:0] me_m = {mc_e[ce], 1'b0} + chip_off;
    wire [7:0] mo_m = {mc_o[co], 1'b0} + 8'd1 + chip_off;

    // 组合算出本拍 ce / co 两路的结果
    wire signed [ZW-1:0] ze_i = mulz_i(evi, evq, cref_i(me_m), cref_q(me_m));
    wire signed [ZW-1:0] ze_q = mulz_q(evi, evq, cref_i(me_m), cref_q(me_m));
    wire signed [DW-1:0] de_i = muld_i(ze_i, ze_q, he_i[ce], he_q[ce]);
    wire signed [DW-1:0] de_q = muld_q(ze_i, ze_q, he_i[ce], he_q[ce]);

    wire signed [ZW-1:0] zo_i = mulz_i(ovi, ovq, cref_i(mo_m), cref_q(mo_m));
    wire signed [ZW-1:0] zo_q = mulz_q(ovi, ovq, cref_i(mo_m), cref_q(mo_m));
    wire signed [DW-1:0] do_i = muld_i(zo_i, zo_q, ho_i[co], ho_q[co]);
    wire signed [DW-1:0] do_q = muld_q(zo_i, zo_q, ho_i[co], ho_q[co]);
    // —— 相干因子的 L1 幅度: |d|_1 = |di| + |dq| (免开方) ——
    // 对齐正确时 d 各项同相 => |Σd|_1 ≈ Σ|d|_1; 错则散相 => |Σd|_1 ≈ Σ|d|_1/sqrt(N).
    wire [DW-1:0] ade_i = de_i[DW-1] ? (~de_i + 1'b1) : de_i;
    wire [DW-1:0] ade_q = de_q[DW-1] ? (~de_q + 1'b1) : de_q;
    wire [DW:0]   ad_e  = {1'b0, ade_i} + {1'b0, ade_q};
    wire [DW-1:0] ado_i = do_i[DW-1] ? (~do_i + 1'b1) : do_i;
    wire [DW-1:0] ado_q = do_q[DW-1] ? (~do_q + 1'b1) : do_q;
    wire [DW:0]   ad_o  = {1'b0, ado_i} + {1'b0, ado_q};

    // z 求和 (t=3 奇链不采, 只加偶链)
    wire signed [ACCW-1:0] zs_e_i = {{(ACCW-ZW){ze_i[ZW-1]}}, ze_i};
    wire signed [ACCW-1:0] zs_e_q = {{(ACCW-ZW){ze_q[ZW-1]}}, ze_q};
    wire signed [ACCW-1:0] zs_ec_i = zs_e_i + {{(ACCW-ZW){zo_i[ZW-1]}}, zo_i};
    wire signed [ACCW-1:0] zs_ec_q = zs_e_q + {{(ACCW-ZW){zo_q[ZW-1]}}, zo_q};

    // ---- CORDIC: atan2(acc_q, acc_i), 取 acc 高 24 位 ----
    reg               cord_start;
    wire              cord_done;
    wire signed [PW-1:0] cord_phi;
    reg  signed [ACCW-1:0] bi, bq;
    wire signed [ACCW-1:0] bi_sh = bi >>> CSH;
    wire signed [ACCW-1:0] bq_sh = bq >>> CSH;
    wire signed [23:0] bi24 = bi_sh[23:0];
    wire signed [23:0] bq24 = bq_sh[23:0];
    cordic_atan2 #(.W(24), .PW(PW)) u_cord (
        .clk(clk), .rst_n(rst_n), .start(cord_start),
        .x_in(bi24), .y_in(bq24),
        .done(cord_done), .phase(cord_phi)
    );

    reg  [2:0]  pbest;
    reg [2*ACCW-1:0] bmag, mag_c;
    reg [2*ACCW-1:0] mag_1, mag_2;    // 最大 / 次大候选 mag2
    reg [ACCW-1:0]   am [0:7];        // Σ|d|_1 (相干因子分母)
    reg [ACCW-1:0]   aa_i, aa_q;      // 选中候选 acc 的绝对值分量
    integer c, q;

    always @(posedge clk) begin
        if (!rst_n) begin
            st <= IDLE; t <= 12'd0; done <= 1'b0; cord_start <= 1'b0;
            p_hat <= 3'd0; phase_inc <= {PW{1'b0}}; phase_off <= {PW{1'b0}};
            bi <= {ACCW{1'b0}}; bq <= {ACCW{1'b0}}; pbest <= 3'd0;
            est_ok <= 1'b0;
            zacc_i <= {ACCW{1'b0}}; zacc_q <= {ACCW{1'b0}};
            for (c = 0; c < 8; c = c + 1) begin
                mc_e[c] <= 8'd0; mc_o[c] <= 8'd0;
                he_i[c] <= {ZW{1'b0}}; he_q[c] <= {ZW{1'b0}};
                ho_i[c] <= {ZW{1'b0}}; ho_q[c] <= {ZW{1'b0}};
                ai[c] <= {ACCW{1'b0}}; aq[c] <= {ACCW{1'b0}};
                am[c] <= {ACCW{1'b0}};
            end
        end else begin
            done <= 1'b0;
            case (st)
            IDLE: begin
                if (start) begin
                    t <= 12'd0;
                    zacc_i <= {ACCW{1'b0}}; zacc_q <= {ACCW{1'b0}};
                    for (c = 0; c < 8; c = c + 1) begin
                        mc_e[c] <= 8'd0; mc_o[c] <= 8'd0;
                        he_i[c] <= {ZW{1'b0}}; he_q[c] <= {ZW{1'b0}};
                        ho_i[c] <= {ZW{1'b0}}; ho_q[c] <= {ZW{1'b0}};
                        ai[c] <= {ACCW{1'b0}}; aq[c] <= {ACCW{1'b0}};
                        am[c] <= {ACCW{1'b0}};
                    end
                    st <= COLLECT;
                end
            end
            COLLECT: begin
                if (dv_in) begin
                    if (k0) begin
                        // 帧起点相位: 只用**短窗口**(前 256 拍 = 32 码片)。相位跨度仅 ~58°,
                        // "Σz 相位 = φ0 + 相位中心"才成立; 全前导累加会跳 1.3 圈,
                        // 矢量和旋转相消, 中心公式失效 (数值验证过: 256 片时误差 >100°)。
                        // 短窗口的代价: 相干项少 → 相位估计噪声大一些, 但 32 项增益 √32 已够。
                        if (t < 12'd256) begin
                            zacc_i <= zacc_i + ((t == 12'd3) ? zs_e_i : zs_ec_i);
                            zacc_q <= zacc_q + ((t == 12'd3) ? zs_e_q : zs_ec_q);
                        end
                        // 偶链候选 ce: 前两片只填历史 (m=0,1 无差分对象)
                        if (mc_e[ce] >= 8'd2) begin
                            ai[ce] <= ai[ce] + {{(ACCW-DW){de_i[DW-1]}}, de_i};
                            aq[ce] <= aq[ce] + {{(ACCW-DW){de_q[DW-1]}}, de_q};
                            am[ce] <= am[ce] + ad_e;
                        end
                        he_i[ce] <= ze_i; he_q[ce] <= ze_q;
                        mc_e[ce] <= mc_e[ce] + 8'd1;
                        // 奇链候选 co。SKIP_T3=1 时跳过 t=3: 原对齐下它是前导外的
                        // “码片 -1” 槽位 (候选 7 的奇序列从 t=19 起)，收进来会让整条奇链错位一片。
                        // 偏移触发下 t=3 已是帧内有效片, SKIP_T3 置 0。
                        if (!(skip_t3 && t == 12'd3)) begin
                            if (mc_o[co] >= 8'd2) begin
                                ai[co] <= ai[co] + {{(ACCW-DW){do_i[DW-1]}}, do_i};
                                aq[co] <= aq[co] + {{(ACCW-DW){do_q[DW-1]}}, do_q};
                                am[co] <= am[co] + ad_o;
                            end
                            ho_i[co] <= zo_i; ho_q[co] <= zo_q;
                            mc_o[co] <= mc_o[co] + 8'd1;
                        end
                    end
                    if (t == NSMP - 1) st <= CMP;
                    else               t  <= t + 12'd1;
                end
            end
            CMP: begin
                pbest = 3'd0;
                mag_1 = {2*ACCW{1'b0}};
                mag_2 = {2*ACCW{1'b0}};
                for (q = 0; q < 8; q = q + 1) begin
                    if ((ai[q]*ai[q] + aq[q]*aq[q]) > mag_1) begin
                        mag_2 = mag_1;
                        mag_1 = ai[q]*ai[q] + aq[q]*aq[q];
                        pbest = q[2:0];
                    end else if ((ai[q]*ai[q] + aq[q]*aq[q]) > mag_2)
                        mag_2 = ai[q]*ai[q] + aq[q]*aq[q];
                end
                mag_c = mag_1;
                bi    <= ai[pbest];
                bq    <= aq[pbest];
                p_hat <= pbest;
                // 质量判据 (相干因子 L1): |Σd|_1 >= Σ|d|_1 / 4 ⇔ 同相度 >= 25%。
                // 对齐正确 -> 各差分项同相, 比值接近 1; 错 -> 散相, 比值 ~ 1/sqrt(126) ≈ 0.09。
                aa_i = ai[pbest][ACCW-1] ? (~ai[pbest] + 1'b1) : ai[pbest];
                aa_q = aq[pbest][ACCW-1] ? (~aq[pbest] + 1'b1) : aq[pbest];
                est_ok  <= ({1'b0, aa_i} + {1'b0, aa_q}) >= {3'b0, am[pbest][ACCW-1:2]};
                cord_start <= 1'b1;
                st <= CORDA;
            end
            CORDA: begin
                cord_start <= 1'b0;
                if (cord_done) begin
                    phase_inc <= cord_phi >>> 4;
                    bi <= zacc_i; bq <= zacc_q;      // 第二次 CORDIC: arg(Σz)
                    cord_start <= 1'b1;
                    st <= CORDB;
                end
            end
            CORDB: begin
                cord_start <= 1'b0;
                if (cord_done) begin
                    // φ0 = arg(Σz) - 124·inc
                    // (32 码片窗口的相位中心 = 15.5 码片 × 8 采样/码片 = 124 采样;
                    //  124 = 128-4, 用 <<7 - <<2 实现)
                    phase_off <= cord_phi - ((phase_inc << 7) - (phase_inc << 2));
                    done <= 1'b1;
                    st <= IDLE;
                end
            end
            default: st <= IDLE;
            endcase
        end
    end
endmodule
