// despreader_tdm.sv —— despreader 的时分复用变体（TDM-1; 候选, 放 variants/）。
// ---------------------------------------------------------------------------
// 动机（notes §65）: 原版 80,436 cells 中 **32 个平方器占 82%**（63.5K）——
// 因为 pwr/argmax 是 always @(*) 全并行组合（每拍都算, 无时分）。
//
// 本版: **相关累加保持原结构**（16 路 ±/PN, 片到达拍更新）; **平方+argmax 时分**:
//   片到达（chip_dv）拍 = sched 0; 其 后 sched 1..6 六拍, 每拍算 3 路功率
//   （6 乘法器 = 3 路 × i²,q²）并串行比较; sched 7 拍输出 sym/sym_dv。
//   32 乘法器 → 6; 且 **无需快照寄存器**——sched 1..6 全部落在
//   "acc 稳定窗"内（下次 chip_dv 前）。
//
// 时序/行为: 判决序列与原版一致（严格 > 的"第一个最大"语义）;
//   sym/sym_dv 相对原版晚 ~7 拍（流式下游无影响——符号间隔 256 拍）。
//   帧间（无 chip_dv）不出 sym_dv（had_cdv 门控）。
// ---------------------------------------------------------------------------
`timescale 1ns/1ps
module despreader_tdm #(
    parameter W = 12,               // 码片软值位宽
    parameter ACC_W = W + 5         // 相关累加位宽 (32×|x|)
) (
    input  wire                      clk,
    input  wire                      rst_n,
    input  wire signed [W-1:0]       chip_i,
    input  wire signed [W-1:0]       chip_q,
    input  wire                      chip_dv,
    input  wire                      frame_start,  // 片计数相位重置（锚定符号边界）
    output reg  [3:0]                sym,
    output reg                       sym_dv
);

    reg [5:0] chip_cnt;

    // ---------------- 相关累加（与原版一致） ----------------
    reg signed [ACC_W-1:0] acc_i [0:15];
    reg signed [ACC_W-1:0] acc_q [0:15];

    integer kk;

    // PN 表 (与 chip_lut / _CHIP_BITS 一致), pn(k, m): 1=+1, 0=-1; m=0 → bit31 (C0)
    function pn(input integer k, input integer m);
        case (k)
            0 : pn = (32'b11011001110000110101001000101110 >> (31 - m)) & 1;
            1 : pn = (32'b11101101100111000011010100100010 >> (31 - m)) & 1;
            2 : pn = (32'b00101110110110011100001101010010 >> (31 - m)) & 1;
            3 : pn = (32'b00100010111011011001110000110101 >> (31 - m)) & 1;
            4 : pn = (32'b01010010001011101101100111000011 >> (31 - m)) & 1;
            5 : pn = (32'b00110101001000101110110110011100 >> (31 - m)) & 1;
            6 : pn = (32'b11000011010100100010111011011001 >> (31 - m)) & 1;
            7 : pn = (32'b10011100001101010010001011101101 >> (31 - m)) & 1;
            8 : pn = (32'b10001100100101100000011101111011 >> (31 - m)) & 1;
            9 : pn = (32'b10111000110010010110000001110111 >> (31 - m)) & 1;
            10: pn = (32'b01111011100011001001011000000111 >> (31 - m)) & 1;
            11: pn = (32'b01110111101110001100100101100000 >> (31 - m)) & 1;
            12: pn = (32'b00000111011110111000110010010110 >> (31 - m)) & 1;
            13: pn = (32'b01100000011101111011100011001001 >> (31 - m)) & 1;
            14: pn = (32'b10010110000001110111101110001100 >> (31 - m)) & 1;
            15: pn = (32'b11001001011000000111011110111000 >> (31 - m)) & 1;
            default: pn = 1'b0;
        endcase
    endfunction

    // 组合前馈: 当前片并入后的累加值（与原版逐字一致）
    reg signed [ACC_W-1:0] nxt_i [0:15];
    reg signed [ACC_W-1:0] nxt_q [0:15];
    always @(*) begin
        for (kk = 0; kk < 16; kk = kk + 1) begin
            if (frame_start || chip_cnt == 6'd0) begin
                nxt_i[kk] = pn(kk, 0) ? {{(ACC_W-W){chip_i[W-1]}}, chip_i}
                                      : -{{(ACC_W-W){chip_i[W-1]}}, chip_i};
                nxt_q[kk] = pn(kk, 0) ? {{(ACC_W-W){chip_q[W-1]}}, chip_q}
                                      : -{{(ACC_W-W){chip_q[W-1]}}, chip_q};
            end else begin
                nxt_i[kk] = pn(kk, chip_cnt)
                          ? acc_i[kk] + {{(ACC_W-W){chip_i[W-1]}}, chip_i}
                          : acc_i[kk] - {{(ACC_W-W){chip_i[W-1]}}, chip_i};
                nxt_q[kk] = pn(kk, chip_cnt)
                          ? acc_q[kk] + {{(ACC_W-W){chip_q[W-1]}}, chip_q}
                          : acc_q[kk] - {{(ACC_W-W){chip_q[W-1]}}, chip_q};
            end
        end
    end

    // ---------------- 时分调度（每拍 3 路; 6 拍 = 16 路） ----------------
    reg [2:0] sched;                 // 0=片到达; 1..6=调度; 7=输出
    reg       had_cdv;               // 本轮是否有片（帧间不出 sym_dv）

    always @(posedge clk) begin
        if (!rst_n) begin
            sched   <= 3'd0;
            had_cdv <= 1'b0;
        end else if (chip_dv) begin
            sched   <= 3'd0;
            had_cdv <= 1'b1;
        end else begin
            sched   <= sched + 3'd1;         // 0→7→0 循环（无片的空转轮由 had_cdv 门控输出）
            if (sched == 3'd7) had_cdv <= 1'b0;
        end
    end

    // 本拍处理的路: base = 3*(sched-1) —— sched=1..6 → base = 0,3,6,9,12,15
    // （查找表而非常数乘法: 保持 $mul 恰好 6 个 = 设计意图）
    reg [4:0] base;
    always @(*) begin
        case (sched)
            3'd1: base = 5'd0;
            3'd2: base = 5'd3;
            3'd3: base = 5'd6;
            3'd4: base = 5'd9;
            3'd5: base = 5'd12;
            3'd6: base = 5'd15;
            default: base = 5'd0;
        endcase
    end
    wire       sched_active = (sched >= 3'd1) && (sched <= 3'd6);
    wire       idx0_ok = sched_active;                 // base   ≤ 15
    wire       idx1_ok = sched_active && (base + 5'd1 < 5'd16);
    wire       idx2_ok = sched_active && (base + 5'd2 < 5'd16);

    wire signed [ACC_W-1:0] ai0 = acc_i[base[3:0]];
    wire signed [ACC_W-1:0] aq0 = acc_q[base[3:0]];
    wire signed [ACC_W-1:0] ai1 = acc_i[(base + 5'd1) & 5'd15];
    wire signed [ACC_W-1:0] aq1 = acc_q[(base + 5'd1) & 5'd15];
    wire signed [ACC_W-1:0] ai2 = acc_i[(base + 5'd2) & 5'd15];
    wire signed [ACC_W-1:0] aq2 = acc_q[(base + 5'd2) & 5'd15];

    // 6 个乘法器（3 路 × i²,q²）
    wire [2*ACC_W+1:0] pw0 = ai0 * ai0 + aq0 * aq0;
    wire [2*ACC_W+1:0] pw1 = ai1 * ai1 + aq1 * aq1;
    wire [2*ACC_W+1:0] pw2 = ai2 * ai2 + aq2 * aq2;

    reg [2*ACC_W+1:0] best_pw;
    reg [3:0]         best_idx;

    // 本拍 3 路串行合并（保序: 严格 > 保留"第一个最大"——与原版语义一致）
    reg [2*ACC_W+1:0] cand;
    reg [3:0]         cand_idx;
    always @(*) begin
        cand     = pw0;
        cand_idx = base[3:0];
        if (idx1_ok && (pw1 > cand)) begin
            cand     = pw1;
            cand_idx = (base + 5'd1) & 5'd15;
        end
        if (idx2_ok && (pw2 > cand)) begin
            cand     = pw2;
            cand_idx = (base + 5'd2) & 5'd15;
        end
    end

    always @(posedge clk) begin
        if (!rst_n) begin
            best_pw  <= {(2*ACC_W+2){1'b0}};
            best_idx <= 4'd0;
        end else if (chip_dv) begin
            best_pw  <= {(2*ACC_W+2){1'b0}};   // 新轮: best 复位（=原版初值语义）
            best_idx <= 4'd0;
        end else if (sched_active && idx0_ok) begin
            if (cand > best_pw) begin
                best_pw  <= cand;
                best_idx <= cand_idx;
            end
        end
    end

    // ---------------- 输出（sched=7 拍） ----------------
    always @(posedge clk) begin
        if (!rst_n) begin
            sym    <= 4'd0;
            sym_dv <= 1'b0;
        end else begin
            sym_dv <= (sched == 3'd7) && had_cdv;
            if ((sched == 3'd7) && had_cdv) sym <= best_idx;
        end
    end

    // ---------------- 片计数（与原版一致） ----------------
    always @(posedge clk) begin
        if (!rst_n) begin
            chip_cnt <= 6'd0;
        end else if (frame_start) begin
            if (chip_dv) chip_cnt <= 6'd1;
            else          chip_cnt <= 6'd0;
        end else if (chip_dv) begin
            chip_cnt <= (chip_cnt == 6'd31) ? 6'd0 : chip_cnt + 6'd1;
        end
    end

    // ---------------- 累加更新（与原版一致: 片到达拍） ----------------
    always @(posedge clk) begin
        if (!rst_n) begin
            // 无显式复位（与原版一致, 由帧流管理）
        end else if (frame_start) begin
            if (chip_dv) begin
                for (kk = 0; kk < 16; kk = kk + 1) begin
                    acc_i[kk] <= nxt_i[kk];
                    acc_q[kk] <= nxt_q[kk];
                end
            end
        end else if (chip_dv) begin
            for (kk = 0; kk < 16; kk = kk + 1) begin
                acc_i[kk] <= nxt_i[kk];
                acc_q[kk] <= nxt_q[kk];
            end
        end
    end

endmodule
