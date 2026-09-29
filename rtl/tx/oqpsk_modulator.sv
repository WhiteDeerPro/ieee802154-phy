// oqpsk_modulator.sv —— TX 组合层: 符号流 → DSSS 扩频 → I/Q 分轨(半码片偏移)
// → 半正弦成形 → 16 Msps 定点 I/Q 采样流
// 与 model/phy_802154.py modulate_oqpsk_fixed 采样级 bit-true。
//
// 采样网格 (clk=16MHz, 码片窗=8 采样):
//   偶数码片 2k : I 路脉冲注入在采样 16k        (窗起点)
//   奇数码片 2k+1: Q 路脉冲注入在采样 16k+12   (奇数码片窗 [16k+8,16k+16) 内偏移 4)
`timescale 1ns/1ps
module oqpsk_modulator (
    input  wire               clk,
    input  wire               rst_n,
    input  wire               ce_2m,      // 2 MHz 符号/码片速率使能
    input  wire [3:0]         sym,        // 4bit 符号
    input  wire               sym_valid,
    output wire               sym_ready,  // 可接收新符号 (上一符号 32 码片已发完)
    output wire signed [11:0] i_out,
    output wire signed [11:0] q_out,
    output wire               sample_dv   // 16MHz 采样有效 (FIR 流水线填充后恒 1)
);
    // ---- 码片产生 ----
    wire chip, chip_ce;
    reg  [4:0] chip_cnt;    // 已输出码片数 (模 32)
    reg        parity;      // 当前输出码片的奇偶 (0=偶→I 路)
    wire       new_sym = ce_2m & sym_valid & sym_ready;

    // load 必须组合驱动: 与 ce 同拍到达 lut, C0 在 load 拍输出
    wire lut_load = new_sym;

    assign sym_ready = (chip_cnt == 5'd0);

    // chip_cnt 按 ce tick 计数 (与码片 1:1, 同拍对齐, 避免采样 chip_ce 的 +1 拍滞后
    // 导致计数提前回卷、sym_ready 在最后一个码片期间虚假置 1)
    always @(posedge clk) begin
        if (!rst_n) begin
            chip_cnt <= 5'd0;
            parity   <= 1'b0;
        end else begin
            if (new_sym) begin
                chip_cnt <= 5'd1;
                parity   <= 1'b0;          // C0 为偶数码片
            end else if (ce_2m && chip_cnt != 5'd0) begin
                chip_cnt <= chip_cnt + 5'd1;   // 5'd31+1 回卷为 0
                parity   <= ~parity;
            end
        end
    end

    chip_lut u_lut (
        .clk(clk), .rst_n(rst_n), .ce(ce_2m),
        .load(lut_load), .sym(sym),
        .chip(chip), .chip_ce(chip_ce)
    );

    // ---- 奇偶分轨 + 半码片偏移 ----
    wire even_chip = (parity == 1'b0);   // 当前输出码片为偶数 → I 路
    reg signed [11:0] i_x, q_x;
    reg               i_dv, q_dv;
    reg [2:0]         q_dly;                  // Q 注入延迟计数 (0..4)
    reg signed [11:0] q_hold;
    reg               q_pending;

    always @(posedge clk) begin
        if (!rst_n) begin
            i_x <= 12'sd0; i_dv <= 1'b0;
            q_x <= 12'sd0; q_dv <= 1'b0;
            q_dly <= 3'd0; q_hold <= 12'sd0; q_pending <= 1'b0;
        end else begin
            i_dv <= 1'b0;
            q_dv <= 1'b0;
            // I: 偶数码片, 窗起点注入
            if (chip_ce && even_chip) begin
                i_x  <= chip ? 12'sd1 : -12'sd1;
                i_dv <= 1'b1;
            end
            // Q: 奇数码片, 4 采样后注入
            if (chip_ce && !even_chip) begin
                q_hold    <= chip ? 12'sd1 : -12'sd1;
                q_pending <= 1'b1;
                q_dly     <= 3'd3;      // T+1..T+3 递减, T+4 注入
            end else if (q_pending) begin
                if (q_dly == 3'd0) begin
                    q_x       <= q_hold;
                    q_dv      <= 1'b1;
                    q_pending <= 1'b0;
                end else begin
                    q_dly <= q_dly - 3'd1;
                end
            end
        end
    end

    // ---- 成形滤波 ----
    wire signed [11:0] i_y, q_y;
    wire i_dvo, q_dvo;

    half_sine_fir u_fir_i (.clk(clk), .rst_n(rst_n), .x(i_x), .dv_in(i_dv), .y(i_y), .dv_out(i_dvo));
    half_sine_fir u_fir_q (.clk(clk), .rst_n(rst_n), .x(q_x), .dv_in(q_dv), .y(q_y), .dv_out(q_dvo));

    assign i_out     = i_y;
    assign q_out     = q_y;

    // ---- sample_dv: 流水线填满后每拍输出均有效 ----
    // 原来写成 `i_dvo & q_dvo` 是错的: half_sine_fir 的 dv_out 是**单拍**脉冲
    // (dv_out <= dv_in), 而 I/Q 的注入在 8 拍周期内错开 4 拍 -> 二者永不重叠,
    // sample_dv 恒为 0, 下游根本无法拿它采样 (与端口注释“填充后恒 1”矛盾)。
    // 修正为流水线填充计数: 9 级 (8 pipe + 输出寄存) 填满后恒 1。
    reg [3:0] fill_cnt;
    always @(posedge clk) begin
        if (!rst_n)                fill_cnt <= 4'd0;
        else if (fill_cnt != 4'hF) fill_cnt <= fill_cnt + 4'd1;
    end
    assign sample_dv = (fill_cnt == 4'hF);
endmodule
