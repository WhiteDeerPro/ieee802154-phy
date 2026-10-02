// preamble_buf.sv —— 前导缓冲（I-13）: 采样级环形缓冲 + 帧到达快照 + BFP8 压缩
// ---------------------------------------------------------------------------
// 用途（docs/14 I-13 / docs/12 §2 接口形状）:
//   单元 → 上层: 前导期的 MF 输出软值 + 帧到达标记;
//   上层据此做 FFT 发现（patterns.discover 口径）/ 相位扫描 / 异常帧排查。
// 为什么存**采样级**而非去交错码片（10.5 kbit → ~172 kbit 的面积取舍）:
//   去交错依赖抽样相位——异常帧场景下相位恰恰不可信; 采样级是无损的充分
//   观测, "抽哪个相位/怎么用"全部留给上层（与 patterns.discover 的
//   "按任意 offset 抽取"口径一致）。
//
// v2（2026-10-02）: 1024 环 + 8bit 块浮点（BFP8）
//   · DEPTH 4096→1024, 窗 3072→1024（PRE=512/POST=512, 覆盖"前导头 ~460 +
//     中段 512"——实测 pd 触发沿 = 物理帧起点 +460, §29/§37）;
//   · 存储 BFP8: 每轴 8bit 尾数（含符号） + 每 BLK 个采样共享 1 个 6bit 指数
//     （块内归一化到 max——帧内量程一致（发射恒定功率, 无 AGC）, 大块有效;
//     跨帧量程变化由"每块独立归一化"吸收, 弱帧不吃亏）;
//   · 编码 = 块延迟乒乓: 当前半区收集（递推 max 1 比较器/拍）, 另半区回写
//     （移位量化 → mem）; 触发后写到 trig+POST 精确停（恰好不覆盖窗首）;
//   · 读出侧定浮转换器（移位）复原为线性 W 位——上层/tb 接口不变。
//   存储: mem 1024×8×2 + exp 16×6 + 乒乓缓存 128×21×2 ≈ 22 kbit（原 172 kbit,
//   −87%）。_notes §37 口径。
//
// 机制:
//   · 持续环形写（每个 dv_in 一拍; BFP 编码后 1 样本/拍回写）;
//   · trig 上升沿（preamble_detect 电平上升沿 = 帧到达）→ 锁存 trig_stream;
//     之后回写至 触发样本+POST 即停（冻结）——窗 [trig−PRE, trig+POST) 恒有效;
//   · 读口: rd_addr ∈ [0, PRE+POST) → 线性化地址 + 指数查表 + 移位复原
//     （上层按时间顺序读, 不需知道回绕/编码）;
//   · clr → 重新武装（done 后读走 → clr）。
// ---------------------------------------------------------------------------
`timescale 1ns/1ps
module preamble_buf #(
    parameter W           = 21,
    parameter integer DEPTH = 1024,     // 必须为 2 的幂（地址自然回绕）
    parameter integer PRE   = 512,      // 触发前保留
    parameter integer POST  = 512,      // 触发后收集（PRE+POST ≤ DEPTH）
    parameter integer BLK   = 64,       // BFP 块长（指数粒度; DEPTH % BLK == 0）
    parameter integer MB    = 8         // 尾数位宽（含符号）
) (
    input  wire                clk,
    input  wire                rst_n,
    input  wire signed [W-1:0] i_in,
    input  wire signed [W-1:0] q_in,
    input  wire                dv_in,
    input  wire                trig,        // 单拍（帧到达沿）
    input  wire                clr,         // 读出后复位（重武装）
    // 读口（组合读, 硬件线性化 + 定浮复原）
    input  wire [11:0]         rd_addr,     // [0, PRE+POST)
    output wire signed [W-1:0] rd_i,
    output wire signed [W-1:0] rd_q,
    output wire                done
);
    localparam integer AW   = $clog2(DEPTH);        // 环地址位宽
    localparam integer BW   = $clog2(BLK);          // 块内偏移位宽
    localparam integer NBLK = DEPTH / BLK;          // 块数
    localparam integer NBW  = $clog2(NBLK);         // 块号位宽
    localparam integer CB   = 2 * BLK;              // 乒乓缓存深度
    localparam integer TW   = 24;                   // 样本流计数位宽

    // ---- BFP 存储 ----
    reg signed [MB-1:0] mem_i [0:DEPTH-1];
    reg signed [MB-1:0] mem_q [0:DEPTH-1];
    // exp 双代（new=最新块, old=上一代块）: 块级指数 vs 样本级 mem 的"环回绕新
    // 旧代"边界相差 0..BLK 个样本; 读侧用"每槽写入进度 xq"判该地址属于哪代,
    // 否则"窗首跨界块"会被下一代块的指数解读（B 差→复原倍数错, §38 实测）。
    reg        [5:0]    exp_new [0:NBLK-1];         // 块指数 B ∈ [1, W]
    reg        [5:0]    exp_old [0:NBLK-1];
    reg        [BW:0]   xq      [0:NBLK-1];         // 该槽当前块已写样本数 (1..BLK)

    // ---- 乒乓收集缓存 + 递推 max ----
    reg signed [W-1:0]  buf_i [0:CB-1];
    reg signed [W-1:0]  buf_q [0:CB-1];
    reg [W:0]           bmax0, bmax1;               // 半区幅度 max（0..2^(W-1)）

    // ---- 控制 ----
    reg [BW-1:0]  c_cnt;                            // 收集/回写同步指针 0..BLK-1
    reg           phase;                            // 当前收集半区
    reg           fill;                             // 首半区填充期
    reg [NBW-1:0] blk_no;                           // 回写目标块号（环）
    reg [TW-1:0]  total_in, wr_stream, trig_stream;
    reg           armed, frozen, done_r;
    integer       j;

    // ---- 收集侧 ----
    wire accept = dv_in && !frozen;
    wire [W:0] ai   = i_in[W-1] ? (~{i_in[W-1], i_in} + 1'b1) : {1'b0, i_in};  // |i|（W+1 位无符号）
    wire [W:0] aq   = q_in[W-1] ? (~{q_in[W-1], q_in} + 1'b1) : {1'b0, q_in};
    wire [W:0] amax = (ai > aq) ? ai : aq;
    wire c_last = (c_cnt == BLK-1);

    // ---- 回写侧 ----
    wire wb_en = dv_in && !frozen && !fill &&
                 (!armed || (wr_stream < (trig_stream + POST)));
    wire [W:0]  bm  = phase ? bmax0 : bmax1;        // 回写半区 = !phase 的 max
    reg  [5:0]  Bq;                                 // 块指数（组合, 块内恒定）
    always @* begin
        Bq = 6'd1;                                  // 全零块 → B=1（量化后仍 0）
        for (j = 0; j < W; j = j + 1)
            if (bm > ((1 << j) - 1)) Bq = j + 1;    // B = ceil(log2(bm+1))
    end
    wire signed [5:0] qsh = $signed(Bq) - 6'sd7;    // 量化移位: 尾数幅度 MB-1=7bit（正=右移）
    wire signed [W-1:0] s_i = buf_i[(phase ? 0 : 1)*BLK + c_cnt];
    wire signed [W-1:0] s_q = buf_q[(phase ? 0 : 1)*BLK + c_cnt];
    localparam [W+7:0] ONE = {{(W+7){1'b0}}, 1'b1};
    wire signed [W+7:0] e_i = {{8{s_i[W-1]}}, s_i};
    wire signed [W+7:0] e_q = {{8{s_q[W-1]}}, s_q};
    wire signed [W+7:0] r_i = (qsh > 0) ? (e_i + (ONE << (qsh - 1))) : e_i;  // 半 LSB 舍入
    wire signed [W+7:0] r_q = (qsh > 0) ? (e_q + (ONE << (qsh - 1))) : e_q;
    wire signed [W+7:0] v_i = (qsh >= 0) ? (r_i >>> qsh) : (r_i <<< (-qsh));  // 压缩: 右移
    wire signed [W+7:0] v_q = (qsh >= 0) ? (r_q >>> qsh) : (r_q <<< (-qsh));
    wire signed [MB-1:0] t_i = (v_i > 127) ? 8'sd127 : (v_i < -128) ? 8'sh80 : v_i[MB-1:0];
    wire signed [MB-1:0] t_q = (v_q > 127) ? 8'sd127 : (v_q < -128) ? 8'sh80 : v_q[MB-1:0];

    always @(posedge clk) begin
        if (!rst_n) begin
            c_cnt    <= {BW{1'b0}};
            phase    <= 1'b0;
            fill     <= 1'b1;
            blk_no   <= {NBW{1'b0}};
            total_in <= {TW{1'b0}};
            wr_stream<= {TW{1'b0}};
            trig_stream <= {TW{1'b0}};
            armed    <= 1'b0;
            frozen   <= 1'b0;
            done_r   <= 1'b0;
            bmax0    <= {(W+1){1'b0}};
            bmax1    <= {(W+1){1'b0}};
            for (j = 0; j < NBLK; j = j + 1) begin
                exp_new[j] <= 6'd0;
                exp_old[j] <= 6'd0;
                xq[j]      <= 7'd0;
            end
        end else begin
            if (clr) begin
                c_cnt    <= {BW{1'b0}};
                phase    <= 1'b0;
                fill     <= 1'b1;
                blk_no   <= {NBW{1'b0}};
                total_in <= {TW{1'b0}};
                wr_stream<= {TW{1'b0}};
                trig_stream <= {TW{1'b0}};
                armed    <= 1'b0;
                frozen   <= 1'b0;
                done_r   <= 1'b0;
                bmax0    <= {(W+1){1'b0}};
                bmax1    <= {(W+1){1'b0}};
            end else begin
                // ---- 触发锁存（首个上升沿, 防抖后忽略）----
                if (trig && !armed && !frozen) begin
                    armed       <= 1'b1;
                    trig_stream <= total_in;        // 触发样本号
                end
                // ---- 冻结: 回写已覆盖到 触发样本+POST ----
                if (!frozen && !fill && armed &&
                    (wr_stream >= (trig_stream + POST)))
                    frozen <= 1'b1;
                if (frozen) done_r <= 1'b1;

                // ---- 收集（与回写同拍、同步指针）----
                if (accept) begin
                    buf_i[phase*BLK + c_cnt] <= i_in;
                    buf_q[phase*BLK + c_cnt] <= q_in;
                    total_in <= total_in + 1'b1;
                    if (phase) begin
                        if (amax > bmax1) bmax1 <= amax;
                    end else begin
                        if (amax > bmax0) bmax0 <= amax;
                    end
                    if (c_last) begin
                        phase <= !phase;
                        c_cnt <= {BW{1'b0}};
                        fill  <= 1'b0;              // 首半区收满
                        if (phase) bmax0 <= {(W+1){1'b0}};   // 新的收集半区清零
                        else       bmax1 <= {(W+1){1'b0}};
                    end else begin
                        c_cnt <= c_cnt + 1'b1;
                    end
                end

                // ---- 回写（BFP 编码 → mem）----
                if (wb_en) begin
                    mem_i[blk_no*BLK + c_cnt] <= t_i;
                    mem_q[blk_no*BLK + c_cnt] <= t_q;
                    wr_stream <= wr_stream + 1'b1;
                    xq[blk_no] <= c_cnt + 1'b1;     // 本槽进度 (块首=1, 满=BLK)
                    if (c_cnt == {BW{1'b0}}) begin
                        exp_old[blk_no] <= exp_new[blk_no];   // 上一代下移
                        exp_new[blk_no] <= Bq;                // 块首写新指数
                    end
                    if (c_last)
                        blk_no <= (blk_no == NBLK-1) ? {NBW{1'b0}} : (blk_no + 1'b1);
                end
            end
        end
    end

    // ---- 读口: 线性化地址 → 尾数 + 块指数 → 定浮复原 ----
    localparam [AW-1:0] PRE_A = PRE;   // 参数截断到环地址宽度
    wire [AW-1:0] base = trig_stream[AW-1:0] - PRE_A;            // mod 2^AW
    wire [AW-1:0] ra   = base + rd_addr[AW-1:0];
    // 该地址属"最新代"当且仅当其块内偏移 < 该槽当前块已写样本数;
    // 否则属上一代（更早 16 块的样本, 可能仍占据该地址）。
    wire          use_new = (ra[BW-1:0] < xq[ra[AW-1:BW]]);
    wire [5:0]    rexp = use_new ? exp_new[ra[AW-1:BW]] : exp_old[ra[AW-1:BW]];
    wire signed [5:0] rsh = $signed(rexp) - 6'sd7;
    wire signed [W-1:0] ext_i = {{(W-MB){mem_i[ra][MB-1]}}, mem_i[ra]};
    wire signed [W-1:0] ext_q = {{(W-MB){mem_q[ra][MB-1]}}, mem_q[ra]};
    assign rd_i = (rsh >= 0) ? (ext_i <<< rsh) : (ext_i >>> (-rsh));
    assign rd_q = (rsh >= 0) ? (ext_q <<< rsh) : (ext_q >>> (-rsh));
    assign done = done_r;
endmodule
