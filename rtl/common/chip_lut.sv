// chip_lut.sv —— 4bit 符号 → 32 码片序列 (IEEE 802.15.4 Table 24/73)
// C0 最先传输。综合后为 16×32 LUT + 移位寄存器。
// 与 model/phy_802154.py _CHIP_BITS 逐位一致。
`timescale 1ns/1ps
module chip_lut (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        ce,        // 2 MHz 码片使能
    input  wire        load,      // 载入符号（与首个码片同拍）
    input  wire [3:0]  sym,
    output reg         chip,      // 码片值: 1=+1, 0=-1
    output reg         chip_ce    // 码片有效
);
    reg [31:0] shreg;
    reg [5:0]  cnt;      // 0..31, 32 = 空闲
    reg [31:0] word;

    // C0 在 bit31, 左移输出 => C0 先传
    always @(*) begin
        case (sym)
            4'd0 : word = 32'b11011001110000110101001000101110;
            4'd1 : word = 32'b11101101100111000011010100100010;
            4'd2 : word = 32'b00101110110110011100001101010010;
            4'd3 : word = 32'b00100010111011011001110000110101;
            4'd4 : word = 32'b01010010001011101101100111000011;
            4'd5 : word = 32'b00110101001000101110110110011100;
            4'd6 : word = 32'b11000011010100100010111011011001;
            4'd7 : word = 32'b10011100001101010010001011101101;
            4'd8 : word = 32'b10001100100101100000011101111011;
            4'd9 : word = 32'b10111000110010010110000001110111;
            4'd10: word = 32'b01111011100011001001011000000111;
            4'd11: word = 32'b01110111101110001100100101100000;
            4'd12: word = 32'b00000111011110111000110010010110;
            4'd13: word = 32'b01100000011101111011100011001001;
            4'd14: word = 32'b10010110000001110111101110001100;
            4'd15: word = 32'b11001001011000000111011110111000;
            default: word = 32'b0;
        endcase
    end

    always @(posedge clk) begin
        if (!rst_n) begin
            shreg   <= 32'b0;
            cnt     <= 6'd32;
            chip    <= 1'b0;
            chip_ce <= 1'b0;
        end else begin
            chip_ce <= 1'b0;
            if (load) begin
                shreg <= {word[30:0], 1'b0};
                cnt   <= 6'd1;               // bit31 本拍直接输出
                if (ce) begin
                    chip    <= word[31];
                    chip_ce <= 1'b1;
                end
            end else if (ce && cnt < 6'd32) begin
                chip    <= shreg[31];
                chip_ce <= 1'b1;
                shreg   <= {shreg[30:0], 1'b0};
                cnt     <= cnt + 6'd1;
            end
        end
    end
endmodule
