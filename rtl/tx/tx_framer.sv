// tx_framer.sv —— 组帧 + 白化 + FCS: PSDU 字节流 → 符号流 (低半字节先)
// 帧结构: Preamble(4×0x00) + SFD(0xA7) + 白化(PHR + PSDU + FCS)
// ⚠ 约定同 model/phy_802154.py tx_frame_bytes: SHR 不白化, FCS 低字节先传 (待确认)
// 与 modulator 握手: sym_valid 保持, 等 sym_ready 的 ce 拍完成交易
`timescale 1ns/1ps
module tx_framer (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [7:0]  len,          // PSDU 长度 (1..127), start 时锁存
    input  wire        start,        // 启动组帧 (单拍脉冲, IDLE 时有效)
    input  wire        ce_2m,        // 2 MHz 使能: 符号交易只在 ce 拍完成 (与 modulator 一致)
    input  wire [7:0]  data_in,      // PSDU 字节流
    input  wire        data_valid,
    output reg         data_ready,   // PAYLOAD 段为 1
    output reg  [3:0]  sym,
    output reg         sym_valid,
    input  wire        sym_ready,
    output reg         busy,
    output reg         done          // 末符号交易完成单拍脉冲
);
    localparam S_IDLE   = 4'd0,
               S_PREP   = 4'd1,   // 取字节, 送白化器/CRC
               S_WHITE  = 4'd2,   // 等白化输出
               S_NIB_LO = 4'd3,
               S_NIB_HI = 4'd4,
               S_NEXT   = 4'd5,
               S_DONE   = 4'd6;

    localparam SEC_PRE  = 2'd0,   // Preamble
               SEC_SFD  = 2'd1,   // SFD
               SEC_PHR  = 2'd2,   // PHR(长度)
               SEC_PSDU = 2'd3;   // PSDU + FCS

    reg [3:0] state;
    reg [1:0] sec;
    reg [7:0] len_latched;
    reg [2:0] pre_cnt;        // 0..3
    reg [7:0] psdu_cnt;       // 已取 PSDU 字节
    reg [7:0] cur_byte;       // 白化后输出字节
    reg [15:0] fcs_latched;
    reg [1:0] fcs_phase;      // 0=未到 1=低字节 2=高字节
    reg       crc_capture;    // 本字节消化后锁存 CRC

    wire whiten_en = (sec != SEC_PRE) && (sec != SEC_SFD);
    wire crc_en    = (sec == SEC_PSDU) && (fcs_phase == 2'd0);

    // ---- 白化器与 CRC (字节级, 内部自动串行) ----
    wire [7:0] wout;
    wire       wvalid;
    reg        win_valid;
    reg  [7:0] win_byte;

    pn9_whiten u_pn9 (
        .clk(clk), .rst_n(rst_n),
        .seed_load(1'b0),
        .data_in(win_byte), .in_valid(win_valid),
        .data_out(wout), .out_valid(wvalid), .busy(wbusy)
    );
    wire wbusy;

    wire [15:0] crc_o;
    reg          crc_in_valid;
    reg          crc_init;
    crc16_fcs u_crc (
        .clk(clk), .rst_n(rst_n),
        .data_in(win_byte), .in_valid(crc_in_valid),
        .init(crc_init), .crc_o(crc_o), .busy(cbusy)
    );
    wire cbusy;

    // 源字节选择 (组合)
    reg [7:0] src_byte;
    always @(*) begin
        case (sec)
            SEC_PRE : src_byte = 8'h00;
            SEC_SFD : src_byte = 8'hA7;
            SEC_PHR : src_byte = len_latched;
            default : src_byte = data_in;   // PSDU / FCS 由时序区分
        endcase
    end

    // FCS 字节 (组合, 由 fcs_phase 选择)
    wire [7:0] fcs_byte = (fcs_phase == 2'd1) ? fcs_latched[7:0] : fcs_latched[15:8];

    wire [7:0] payload_byte = (fcs_phase == 2'd0) ? data_in : fcs_byte;

    always @(posedge clk) begin
        if (!rst_n) begin
            state       <= S_IDLE;
            sec         <= SEC_PRE;
            pre_cnt     <= 3'd0;
            psdu_cnt    <= 8'd0;
            fcs_phase   <= 2'd0;
            crc_capture <= 1'b0;
            cur_byte    <= 8'd0;
            fcs_latched <= 16'd0;
            sym         <= 4'd0;
            sym_valid   <= 1'b0;
            data_ready  <= 1'b0;
            win_valid   <= 1'b0;
            crc_in_valid<= 1'b0;
            crc_init    <= 1'b0;
            busy        <= 1'b0;
            done        <= 1'b0;
            len_latched <= 8'd0;
        end else begin
            // 单拍脉冲默认
            win_valid    <= 1'b0;
            crc_in_valid <= 1'b0;
            crc_init     <= 1'b0;
            done         <= 1'b0;

            case (state)
                S_IDLE: begin
                    if (start) begin
                        len_latched <= len;
                        pre_cnt     <= 3'd0;
                        psdu_cnt    <= 8'd0;
                        fcs_phase   <= 2'd0;
                        sec         <= SEC_PRE;
                        busy        <= 1'b1;
                        crc_init    <= 1'b1;    // 清 CRC
                        state       <= S_PREP;
                    end
                end

                S_PREP: begin
                    // 取当前字节送白化器 (和 CRC), SHR 段跳过白化
                    if (whiten_en) begin
                        win_byte  <= (sec == SEC_PSDU) ? payload_byte : src_byte;
                        win_valid <= 1'b1;
                        state     <= S_WHITE;
                    end else begin
                        cur_byte <= src_byte;
                        state    <= S_NIB_LO;
                    end
                    if (crc_en) begin
                        crc_in_valid <= 1'b1;
                        if (psdu_cnt == len_latched - 8'd1)
                            crc_capture <= 1'b1;   // 末 PSDU 字节消化后锁存
                    end
                    data_ready <= 1'b0;
                end

                S_WHITE: begin
                    if (wvalid) begin
                        cur_byte <= wout;
                        state    <= S_NIB_LO;
                    end
                end

                S_NIB_LO: begin
                    sym       <= cur_byte[3:0];
                    sym_valid <= 1'b1;
                    if (sym_valid && sym_ready && ce_2m) begin
                        sym_valid <= 1'b0;
                        state     <= S_NIB_HI;
                    end
                end

                S_NIB_HI: begin
                    sym       <= cur_byte[7:4];
                    sym_valid <= 1'b1;
                    if (sym_valid && sym_ready && ce_2m) begin
                        sym_valid <= 1'b0;
                        state     <= S_NEXT;
                    end
                end

                S_NEXT: begin
                    if (crc_capture) begin
                        fcs_latched <= crc_o;      // 末 PSDU 字节已消化, 锁存 FCS
                        crc_capture <= 1'b0;
                    end
                    // 推进段/字节计数
                    case (sec)
                        SEC_PRE: begin
                            if (pre_cnt == 3'd3) sec <= SEC_SFD;
                            pre_cnt <= pre_cnt + 3'd1;
                            state   <= S_PREP;
                        end
                        SEC_SFD: begin
                            sec   <= SEC_PHR;
                            state <= S_PREP;
                        end
                        SEC_PHR: begin
                            sec        <= SEC_PSDU;
                            data_ready <= 1'b1;      // 准备收 PSDU
                            state      <= S_PREP;
                        end
                        default: begin  // SEC_PSDU
                            if (fcs_phase == 2'd0) begin
                                if (psdu_cnt == len_latched - 8'd1) begin
                                    fcs_phase <= 2'd1;   // 下一字节发 FCS 低
                                end else begin
                                    psdu_cnt <= psdu_cnt + 8'd1;
                                end
                                data_ready <= 1'b1;
                                state      <= S_PREP;
                            end else if (fcs_phase == 2'd1) begin
                                fcs_phase <= 2'd2;
                                state     <= S_PREP;
                            end else begin
                                state <= S_DONE;
                            end
                        end
                    endcase
                end

                S_DONE: begin
                    busy  <= 1'b0;
                    done  <= 1'b1;
                    state <= S_IDLE;
                end

                default: state <= S_IDLE;
            endcase
        end
    end
endmodule
