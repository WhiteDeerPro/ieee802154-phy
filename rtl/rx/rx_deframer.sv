// rx_deframer.sv —— RX 字节定界: 去白化 (PN9) + PHR 解析 + FCS 校验
// 输入为 despreader 输出的符号流 (自 PHR 符号起, 低半字节先);
// frame_start (单拍, 与首个 PHR 码片同拍) 触发本帧 seed 重载与 CRC 清零。
// 输出 PSDU 字节流 + psdu_len + fcs_ok; frame_done 单拍标记帧结束。
//
// ⚠ 约定「待标准原文确认」(CC2530 风格): 白化范围 = PHR+PSDU+FCS, FCS 低字节先传,
//   FCS 覆盖范围 = 仅 PSDU (与 tx_framer 发送侧一致)。
// 与 model/phy_802154.py rx_deframe_symbols 功能镜像。
//
// 时序: symbol 间隔 ~65 拍 (32 片 × 2 采样) 远大于 pn9/crc 每字节 8 拍, 无反压。
//   S_IDLE  : 等 frame_start, 同拍 pulse seed_load + crc init
//   S_SYM_LO: 收低半字节
//   S_SYM_HI: 收高半字节拼字节 → pulse pn9 in_valid
//   S_WAIT_W: 等去白化字节 (pn9 8 拍)
//   S_BYTE  : 按字节序号分发: 0=PHR(锁 len, len>127 判坏帧) 1..L=PSDU(输出+喂 CRC)
//             L+1/L+2=FCS(不输出不喂 CRC), L+2 且 CRC 空时判 crc_o==0 → fcs_ok
`timescale 1ns/1ps
module rx_deframer (
    input  wire       clk,
    input  wire       rst_n,
    input  wire [3:0] sym,          // despreader 符号输出
    input  wire       sym_dv,
    input  wire       frame_start,  // 单拍, 帧起点 (首个 PHR 码片到达时)
    output reg  [7:0] data_out,     // PSDU 字节流
    output reg        data_valid,   // 单拍
    output reg  [7:0] psdu_len,     // 本帧 PHR 长度 (frame_done 时有效)
    output reg        fcs_ok,       // frame_done 时有效, 保持到下一帧
    output reg        frame_done,   // 单拍
    output reg        busy          // frame_start 至 frame_done
);
    localparam S_IDLE   = 3'd0,
               S_SYM_LO = 3'd1,
               S_SYM_HI = 3'd2,
               S_WAIT_W = 3'd3,
               S_BYTE   = 3'd4;

    reg [2:0] state;
    reg [3:0] lo_nib;       // 低半字节缓存
    reg [7:0] byte_idx;     // 去白化字节序号 (0=PHR, 1..L=PSDU, L+1/L+2=FCS)
    reg [15:0] fcs_reg;     // FCS 低字节锁存 (高字节在 S_BYTE 当前拍直接比对)
    reg [7:0] wbyte;        // 去白化后字节

    wire [7:0] pn_out;
    wire       pn_ov;
    wire [15:0] crc_o;
    wire       crc_busy;
    reg        crc_feed;    // S_BYTE 内 PSDU 字节的 CRC 喂入单拍
    // 字节拼装组合直通 pn9: 避免 raw_byte 寄存器与 pn9 采样同拍竞争
    wire [7:0] asm_byte = {sym, lo_nib};

    pn9_whiten u_pn9 (
        .clk(clk), .rst_n(rst_n),
        .seed_load(frame_start & (state == S_IDLE)),
        .data_in(asm_byte), .in_valid(state == S_SYM_HI && sym_dv),
        .data_out(pn_out), .out_valid(pn_ov), .busy()
    );

    crc16_fcs u_crc (
        .clk(clk), .rst_n(rst_n),
        .data_in(wbyte),
        .in_valid(crc_feed),
        .init(frame_start & (state == S_IDLE)),
        .crc_o(crc_o), .busy(crc_busy)
    );

    always @(posedge clk) begin
        if (!rst_n) begin
            state      <= S_IDLE;
            lo_nib     <= 4'd0;
            byte_idx   <= 8'd0;
            fcs_reg    <= 16'd0;
            wbyte      <= 8'd0;
            data_out   <= 8'd0;
            data_valid <= 1'b0;
            psdu_len   <= 8'd0;
            fcs_ok     <= 1'b0;
            frame_done <= 1'b0;
            busy       <= 1'b0;
        end else begin
            data_valid <= 1'b0;
            frame_done <= 1'b0;
            crc_feed   <= 1'b0;

            case (state)
                S_IDLE: begin
                    if (frame_start) begin
                        // seed_load / crc init 由子模块输入条件同拍触发
                        byte_idx <= 8'd0;
                        busy     <= 1'b1;
                        if (sym_dv) begin
                            lo_nib <= sym;      // frame_start 与首个符号同拍: 直接收低半字节
                            state  <= S_SYM_HI;
                        end else begin
                            state  <= S_SYM_LO; // 链级场景: frame_start 早到, 符号随后
                        end
                    end
                end

                S_SYM_LO: begin
                    if (sym_dv) begin
                        lo_nib <= sym;
                        state  <= S_SYM_HI;
                    end
                end

                S_SYM_HI: begin
                    if (sym_dv) begin
                        state <= S_WAIT_W;   // 字节经 asm_byte 组合通路已进 pn9
                    end
                end

                S_WAIT_W: begin
                    if (pn_ov) begin
                        wbyte  <= pn_out;
                        state  <= S_BYTE;
                    end
                end

                S_BYTE: begin
                    if (byte_idx == 8'd0) begin
                        // PHR: 长度字节
                        if (wbyte > 8'd127) begin
                            // 坏帧: 直接收尾
                            fcs_ok     <= 1'b0;
                            frame_done <= 1'b1;
                            busy       <= 1'b0;
                            state      <= S_IDLE;
                        end else begin
                            psdu_len <= wbyte;
                            byte_idx <= 8'd1;
                            state    <= S_SYM_LO;
                        end
                    end else if (byte_idx <= psdu_len) begin
                        // PSDU: 输出 + 喂 CRC
                        data_out   <= wbyte;
                        data_valid <= 1'b1;
                        crc_feed   <= 1'b1;
                        byte_idx   <= byte_idx + 8'd1;
                        state      <= S_SYM_LO;
                    end else if (byte_idx == psdu_len + 8'd1) begin
                        // FCS 低字节: 只锁存
                        fcs_reg  <= {8'd0, wbyte};
                        byte_idx <= byte_idx + 8'd1;
                        state    <= S_SYM_LO;
                    end else begin
                        // FCS 高字节: CRC 空时与累计值比对 (低字节先传)
                        if (!crc_busy) begin
                            fcs_ok     <= (crc_o == {wbyte, fcs_reg[7:0]});
                            frame_done <= 1'b1;
                            busy       <= 1'b0;
                            state      <= S_IDLE;
                        end
                    end
                end

                default: state <= S_IDLE;
            endcase
        end
    end
endmodule
