// despreader_oct8.sv —— 32 复数码片软值 → 16 路并行相关 → **8 边形幅度检测** argmax → 4bit 符号
// ---------------------------------------------------------------------------
// "0 乘法"变体: 用 alpha-max-beta-min 八边形幅度近似替代精确 |s|² = I² + Q²:
//     |s| ≈ α·max(|I|,|Q|) + β·min(|I|,|Q|)
//
// 系数用"稀疏移位项"实现 —— 同 ip/FPU_PROJECT/rtl/common/fpu_recip_seed_lut.sv 的
// signed-digit 斜率构造风格 (系数 = 少量带符号 2^-k 之和; 应用 = 移位 + 加/减, 无乘法):
//     MODE=1 (默认): α = 15/16 = 1 − 2^-4,  β = 15/32 = 2^-1 − 2^-5  (幅度误差 6.25%)
//     MODE=0:        α = 1,                β = 1/2                     (幅度误差 11.8%)
//
// 判决只需"序"不需"值": argmax v_k ≡ argmax |s_k| (近似; 对判决语义单调等价)。
// 全链操作 = 移位 + 加减 + 比较, **无乘法器**（写为 +/− 即退化了的相关运算）。
//
// 时序与原版 despreader 完全一致: 组合前馈; 末片 (chip_cnt==31) 当拍出 argmax,
// 下一拍寄存 sym/sym_dv; frame_start 拍基值分支; 帧外自由滚动同语义。
// 对照依据: notes §66 多边形化(模型级); tb/despreader_oct8 与原版并排。
// ---------------------------------------------------------------------------
`timescale 1ns/1ps
module despreader_oct8 #(
    parameter W = 12,              // 码片软值位宽
    parameter ACC_W = W + 5,       // 相关累加位宽 (32×|x|)
    parameter MODE = 1             // 系数档: 0=(1,1/2); 1=(15/16,15/32)
) (
    input  wire                      clk,
    input  wire                      rst_n,
    input  wire signed [W-1:0]       chip_i,
    input  wire signed [W-1:0]       chip_q,
    input  wire                      chip_dv,
    input  wire                      frame_start,  // 片计数相位重置 (锚定符号边界)
    output reg  [3:0]                sym,
    output reg                       sym_dv
);
    localparam ABW = ACC_W + 1;    // 18: |·| 与度量的无符号位宽

    reg [5:0] chip_cnt;   // 符号内片序号 0..31

    // 16 路相关累加
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

    // 组合前馈: 当前片并入后的累加值 (与原版 despreader 相同)
    //   frame_start 拍 或 chip_cnt==0 拍 = 符号首片: 基值 = ±chip, 系数 PN[0] (C0 先传);
    //   其余片: 累加 = acc ± chip, 系数 PN[chip_cnt]。
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

    // ---------------- 8 边形度量 (0 乘法, 组合前馈) ----------------
    //   |s| ≈ α·max(|I|,|Q|) + β·min(|I|,|Q|)
    //   系数档 MODE=1: α = 1 − 2^-4 (一次移位一次减); β = 2^-1 − 2^-5 (两次移位一次减)
    wire signed [ABW-1:0] sx_i [0:15];
    wire signed [ABW-1:0] sx_q [0:15];
    wire [ABW-1:0]        ai   [0:15];
    wire [ABW-1:0]        aq   [0:15];
    wire [ABW-1:0]        amax [0:15];
    wire [ABW-1:0]        amin [0:15];
    wire [ABW-1:0]        met  [0:15];
    genvar gi;
    generate
        for (gi = 0; gi < 16; gi = gi + 1) begin : g_metric
            assign sx_i[gi] = {{(ABW-ACC_W){nxt_i[gi][ACC_W-1]}}, nxt_i[gi]};
            assign sx_q[gi] = {{(ABW-ACC_W){nxt_q[gi][ACC_W-1]}}, nxt_q[gi]};
            assign ai[gi]   = sx_i[gi][ABW-1] ? (~sx_i[gi] + 1'b1) : sx_i[gi];
            assign aq[gi]   = sx_q[gi][ABW-1] ? (~sx_q[gi] + 1'b1) : sx_q[gi];
            assign amax[gi] = (ai[gi] >= aq[gi]) ? ai[gi] : aq[gi];
            assign amin[gi] = (ai[gi] >= aq[gi]) ? aq[gi] : ai[gi];
            if (MODE == 0) begin : g_m0
                assign met[gi] = amax[gi] + (amin[gi] >> 1);
            end else begin : g_m1
                // 15/16·a + 15/32·b  =  a − a/16 + b/2 − b/32
                assign met[gi] = amax[gi] - (amax[gi] >> 4)
                               + (amin[gi] >> 1) - (amin[gi] >> 5);
            end
        end
    endgenerate

    // ---------------- argmax (组合前馈, 同原版语义) ----------------
    reg [3:0]        best_n;
    reg [ABW-1:0]    bestv_n;
    always @(*) begin
        best_n  = 4'd0;
        bestv_n = met[0];
        for (kk = 1; kk < 16; kk = kk + 1)
            if (met[kk] > bestv_n) begin
                bestv_n = met[kk];
                best_n  = kk[3:0];
            end
    end

    always @(posedge clk) begin
        if (!rst_n) begin
            chip_cnt <= 6'd0;
            sym      <= 4'd0;
            sym_dv   <= 1'b0;
        end else begin
            sym_dv <= 1'b0;
            if (frame_start) begin
                if (chip_dv) begin
                    // 帧首片同拍: 当拍累加基值 (组合由 frame_start 强制 PN[0]), 下一片序号 1
                    for (kk = 0; kk < 16; kk = kk + 1) begin
                        acc_i[kk] <= nxt_i[kk];
                        acc_q[kk] <= nxt_q[kk];
                    end
                    chip_cnt <= 6'd1;
                end else begin
                    // frame_start 早到 (首片随后): 清零, 首片拍 chip_cnt==0 走基值分支
                    chip_cnt <= 6'd0;
                end
            end else if (chip_dv) begin
                for (kk = 0; kk < 16; kk = kk + 1) begin
                    acc_i[kk] <= nxt_i[kk];
                    acc_q[kk] <= nxt_q[kk];
                end
                if (chip_cnt == 6'd31) begin
                    chip_cnt <= 6'd0;
                    sym      <= best_n;
                    sym_dv   <= 1'b1;
                end else begin
                    chip_cnt <= chip_cnt + 6'd1;
                end
            end
        end
    end
endmodule
