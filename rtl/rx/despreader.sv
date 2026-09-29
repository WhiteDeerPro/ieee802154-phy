// despreader.sv —— 32 复数码片软值 → 16 路并行相关 + |·|² 幅值检测 argmax → 4bit 符号
// 非相干解扩 (IEEE 802.15.4 对数字实现最友好的特性: 无需载波相位恢复)。
// 相关: s_k = Σ_m c_m · PN_k[m] (k=0..15), power_k = |s_k|², sym = argmax
// 与 model/phy_802154.py despread_chips 算法一致 (符号级 bit-true, 同序整数累加)。
//
// 时序: 流式累加 —— 每片到达当拍完成 16 路加减, symbol 末片 (chip_cnt==31)
// 当拍组合前馈出 argmax (nxt 通路), 无 COLLECT/COMPUTE 分时, 片间隔 ≥1 拍即不丢片。
// 码片速率 2 Mchip/s @16MHz = 8 拍/片, 16 路并行加法是 trivial 预算。
// frame_start: 帧边界同步 (单拍, 与首个 PHR 码片同拍)。RX 链由 preamble_sync 的
// frame_start 清零片计数, 使 32 片累加窗锚定符号边界 (TX 侧/单元测试 tie 1'b0,
// 分组天然对齐)。片计数归零当拍的累加基值强制为 0 (免显式清 acc)。
`timescale 1ns/1ps
module despreader #(
    parameter W = 12,              // 码片软值位宽
    parameter ACC_W = W + 5        // 相关累加位宽 (32×|x|)
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

    // 组合前馈: 当前片并入后的累加值。
    //   frame_start 拍 或 chip_cnt==0 拍 = 符号首片: 基值 = ±chip, 系数 PN[0] (C0 先传);
    //   其余片: 累加 = acc ± chip, 系数 PN[chip_cnt]。
    // 帧外自由滚动与帧内同语义: chip_cnt 模 32 分组天然对齐, frame_start 只重置相位。
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

    // 前馈功率与 argmax (组合)
    reg [2*ACC_W:0] pwr_n [0:15];
    reg [3:0] best_n;
    reg [2*ACC_W:0] bestp_n;
    always @(*) begin
        for (kk = 0; kk < 16; kk = kk + 1)
            pwr_n[kk] = nxt_i[kk]*nxt_i[kk] + nxt_q[kk]*nxt_q[kk];
        best_n  = 4'd0;
        bestp_n = pwr_n[0];
        for (kk = 1; kk < 16; kk = kk + 1)
            if (pwr_n[kk] > bestp_n) begin
                bestp_n = pwr_n[kk];
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
