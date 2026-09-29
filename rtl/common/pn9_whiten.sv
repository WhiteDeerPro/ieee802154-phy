// pn9_whiten.sv —— PN9 白化/去白化 (自逆), x^9+x^5+1, 逐字节 LSB-first
// ⚠ 约定「待标准原文确认」(CC2530 风格), 与 model/phy_802154.py pn9_bytes 逐位一致。
// 白化/去白化共用: y = x ^ PN9, 对合运算。
// seed_load: 单拍脉冲, 重载 LFSR seed = 9'h1FF (RX 每帧开始去白化前使用;
//            上电/复位后无需, 因复位即载 seed)。TX 侧 tie 1'b0。
`timescale 1ns/1ps
module pn9_whiten (
    input  wire       clk,
    input  wire       rst_n,
    input  wire       seed_load,   // 重载 seed (单拍脉冲, 字节空闲时有效)
    input  wire [7:0] data_in,
    input  wire       in_valid,
    output reg  [7:0] data_out,
    output reg        out_valid,
    output reg        busy
);
    reg [8:0]  lfsr;      // seed = 9'h1FF
    reg [7:0]  shreg;
    reg [3:0]  bitcnt;    // 0..7
    reg        running;

    wire fb = lfsr[8] ^ lfsr[4];   // 抽头 x^9, x^5

    always @(posedge clk) begin
        if (!rst_n) begin
            lfsr      <= 9'h1FF;
            shreg     <= 8'd0;
            bitcnt    <= 4'd0;
            running   <= 1'b0;
            busy      <= 1'b0;
            out_valid <= 1'b0;
        end else begin
            out_valid <= 1'b0;
            if (seed_load)
                lfsr <= 9'h1FF;
            if (!running) begin
                if (in_valid) begin
                    running <= 1'b1;
                    busy    <= 1'b1;
                    shreg   <= data_in;
                    bitcnt  <= 4'd0;
                end
            end else begin
                // LSB-first: shreg[0] 当前位
                shreg[6:0] <= shreg[7:1];   // 右移: 下一位移到 bit0 (LSB-first)
                lfsr <= {lfsr[7:0], fb};
                // 输出位 = data_bit ^ 移出位(lfsr[8] 旧值)
                if (bitcnt == 4'd7) begin
                    data_out[bitcnt] <= shreg[0] ^ lfsr[8];
                    running   <= 1'b0;
                    busy      <= 1'b0;
                    out_valid <= 1'b1;
                end else begin
                    data_out[bitcnt] <= shreg[0] ^ lfsr[8];
                    bitcnt <= bitcnt + 4'd1;
                end
            end
        end
    end
endmodule
