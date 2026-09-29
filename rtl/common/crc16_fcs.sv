// crc16_fcs.sv —— CRC-16 FCS, poly 0x1021, init 0x0000, 无输出异或, MSB-first
// 已知向量: "123456789" -> 0x31C3 (CRC-16/AUG-CCITT)
// 与 model/phy_802154.py crc16_fcs 逐位一致。
// crc_o 实时反映当前余数: TX 送完 PSDU 后读出即为 FCS; RX 送完全部后 crc_o==0 判通过。
`timescale 1ns/1ps
module crc16_fcs (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [7:0]  data_in,
    input  wire        in_valid,
    input  wire        init,      // 高电平期间异步清零 crc (与 in_valid 互斥使用)
    output reg  [15:0] crc_o,
    output reg         busy
);
    reg [15:0] crc;
    reg [7:0]  shreg;
    reg [3:0]  bitcnt;
    reg        running;

    wire fb = crc[15] ^ shreg[7];

    always @(posedge clk) begin
        if (!rst_n) begin
            crc     <= 16'd0;
            shreg   <= 8'd0;
            bitcnt  <= 4'd0;
            running <= 1'b0;
            busy    <= 1'b0;
        end else if (init) begin
            crc     <= 16'd0;
            running <= 1'b0;
            busy    <= 1'b0;
        end else begin
            if (!running) begin
                if (in_valid) begin
                    running <= 1'b1;
                    busy    <= 1'b1;
                    shreg   <= data_in;
                    bitcnt  <= 4'd0;
                end
            end else begin
                shreg[7:1] <= shreg[6:0];
                crc <= fb ? ((crc << 1) ^ 16'h1021) : (crc << 1);
                if (bitcnt == 4'd7) begin
                    running <= 1'b0;
                    busy    <= 1'b0;
                end else begin
                    bitcnt <= bitcnt + 4'd1;
                end
            end
        end
    end

    always @(*) crc_o = crc;
endmodule
