// zigbee_top.sv —— Zigbee (IEEE 802.15.4 2.4GHz PHY) 数字收发单元顶层。
// ---------------------------------------------------------------------------
// 组装: TX (tx_framer → oqpsk_modulator) ── 数字回环 ── RX (rx_dual 双通道)
//   用途: 单元级端到端验证 —— 主机发一帧 PSDU, 期望 RX 解码 (fcs_ok + 载荷一致)。
//   回环 = 理想信道 (无噪/无 CFO); 单时钟域 16 MHz; ce_2m = 8 分频 (内部产生)。
//   配置: 阈值取 rx_dual_mc 参考配置 (ph_th=2e11, sfd_th=3e13, norm_th=90);
//         消旋参数 0 (理想信道); W=16 / VSHIFT=8 (rx_dual 现役默认)。
// ---------------------------------------------------------------------------
`timescale 1ns/1ps
module zigbee_top #(
    parameter integer LOOP_SHIFT = 3,  // 回环标度: adc = tx << LOOP_SHIFT（对齐场景 mem 的 ×8）
    parameter integer MIX_F_HZ   = 2_000_000,  // 数字"中频"（变频链中间频率）
    parameter integer CFO_HZ     = 100_000     // 上/下变频频差（= 晶振频差/CFO; A 通道假设 +100k）
) (
    input  wire        clk,            // 16 MHz
    input  wire        rst_n,
    // ---- 主机侧: TX ----
    input  wire [7:0]  tx_len,         // PSDU 长度 (1..127)
    input  wire        tx_start,       // 启动组帧 (单拍脉冲)
    input  wire [7:0]  tx_data,        // PSDU 字节流 (data_ready 拍提供)
    input  wire        tx_data_valid,
    output wire        tx_data_ready,
    output wire        tx_busy,
    output wire        tx_done,
    // ---- 主机侧: RX (A/B 双通道) ----
    output wire [7:0]  rx_data_a,
    output wire        rx_data_valid_a,
    output wire [7:0]  rx_psdu_len_a,
    output wire        rx_fcs_ok_a,
    output wire        rx_frame_done_a,
    output wire [7:0]  rx_data_b,
    output wire        rx_data_valid_b,
    output wire [7:0]  rx_psdu_len_b,
    output wire        rx_fcs_ok_b,
    output wire        rx_frame_done_b,
    output wire        rx_any_fcs_ok,
    // ---- 观测 ----
    output wire [3:0]  rx_phase_out,
    output wire        rx_detect
);
    // ---- 2 MHz 使能（8 分频自由跑; RX 扫描自动补偿相位）----
    reg [2:0] div_cnt;
    always @(posedge clk) begin
        if (!rst_n) div_cnt <= 3'd0;
        else        div_cnt <= div_cnt + 3'd1;
    end
    wire ce_2m = (div_cnt == 3'd0);

    // ---- TX: 组帧 ----
    wire [3:0] t_sym;
    wire       t_sym_valid, t_sym_ready;
    tx_framer u_tx_framer (
        .clk(clk), .rst_n(rst_n),
        .len(tx_len), .start(tx_start),
        .ce_2m(ce_2m),
        .data_in(tx_data), .data_valid(tx_data_valid), .data_ready(tx_data_ready),
        .sym(t_sym), .sym_valid(t_sym_valid), .sym_ready(t_sym_ready),
        .busy(tx_busy), .done(tx_done)
    );

    // ---- TX: 调制（符号 → 16 MHz 采样流）----
    wire signed [11:0] tx_i, tx_q;
    wire               tx_smp_dv;
    oqpsk_modulator u_mod (
        .clk(clk), .rst_n(rst_n),
        .ce_2m(ce_2m),
        .sym(t_sym), .sym_valid(t_sym_valid), .sym_ready(t_sym_ready),
        .i_out(tx_i), .q_out(tx_q), .sample_dv(tx_smp_dv)
    );

    // ---- 变频链（"不能直接回环": TX/RX 之间必须有上下变频环节）----
    //   上变频: +MIX_F_HZ; 下变频: -MIX_F_HZ + CFO_HZ（净 = +CFO_HZ 频差, 模拟晶振差）。
    //   cfo_rot 输出 = in·exp(-j·n·inc)（消旋约定）→ 正频移 = 负 inc。
    localparam integer INC_MIX = (1 << 24) * MIX_F_HZ / 16_000_000;
    localparam integer INC_CFO = (1 << 24) * CFO_HZ / 16_000_000;
    localparam signed [23:0] INC_UP = -INC_MIX;          // 上变频（正频移 = 负 inc）
    localparam signed [23:0] INC_DN = INC_MIX - INC_CFO;  // 下变频（净 +CFO）

    wire signed [11:0] up_i, up_q;
    wire               up_dv;
    cfo_rot #(.W(12)) u_mix_up (          // 上变频（正频移: inc = -INC_MIX）
        .clk(clk), .rst_n(rst_n),
        .i_in(tx_i), .q_in(tx_q), .dv_in(tx_smp_dv),
        .load(1'b0),
        .phase_inc(INC_UP),
        .phase_off(24'd0),
        .i_out(up_i), .q_out(up_q), .dv_out(up_dv)
    );

    // ---- 信道: 注入噪声底（关键! "纯 0" 是数字直连的假象——真实链路总有噪声,
    //      否则检测器的"相关/能量"判据在 0 流上退化误触发, 帧沿消失）----
    reg [15:0] lfsr;
    always @(posedge clk) begin
        if (!rst_n) lfsr <= 16'hACE1;
        else        lfsr <= {lfsr[14:0], lfsr[15] ^ lfsr[13] ^ lfsr[12] ^ lfsr[10]};
    end
    wire signed [11:0] ch_n_i = {{5{lfsr[6]}}, lfsr[6:0]};      // 粗量化噪声 ±64
    wire signed [11:0] ch_n_q = {{5{lfsr[14]}}, lfsr[14:8]};
    wire signed [12:0] ch_i13 = {up_i[11], up_i} + {ch_n_i[11], ch_n_i};
    wire signed [12:0] ch_q13 = {up_q[11], up_q} + {ch_n_q[11], ch_n_q};
    wire signed [11:0] ch_i = ch_i13[11:0];
    wire signed [11:0] ch_q = ch_q13[11:0];

    wire signed [11:0] dn_i, dn_q;
    wire               dn_dv;
    cfo_rot #(.W(12)) u_mix_dn (          // 下变频（净 +CFO: inc = +MIX - CFO）
        .clk(clk), .rst_n(rst_n),
        .i_in(ch_i), .q_in(ch_q), .dv_in(up_dv),
        .load(1'b0),
        .phase_inc(INC_DN),
        .phase_off(24'd0),
        .i_out(dn_i), .q_out(dn_q), .dv_out(dn_dv)
    );

    // ---- 回环标度（对齐场景 mem 量纲；纯连线: 左移 + 取低 12 位域）----
    wire signed [11:0] loop_i = 12'(dn_i <<< LOOP_SHIFT);
    wire signed [11:0] loop_q = 12'(dn_q <<< LOOP_SHIFT);

    // ---- RX: 双通道接收（经"变频"链的回环; RST_EN=1 = 参考配置:
    //      帧到达→扫描重启, 相位 latch 在前导段确认后——非旧路径的"detect 即锁"）----
    rx_dual #(.RST_EN(1'b1)) u_rx (
        .clk(clk), .rst_n(rst_n),
        .adc_i(loop_i), .adc_q(loop_q), .adc_dv(dn_dv),
        .ph_thresh(48'd200_000_000_000),
        .sfd_thresh(48'd30_000_000_000_000),
        .sfd_norm_th(16'd90),
        .wake_dly(17'd0),
        .wake_clr(1'b0),
        .pbuf_addr(12'd0), .pbuf_clr(1'b0),
        .pbuf_i(), .pbuf_q(), .pbuf_done(),
        .ext_lock_en(1'b0), .ext_lock_phase(4'd0),
        .rot_load(1'b0),
        .phase_inc_chip_a(INC_CFO),           // A 通道: 假设 +CFO（与变频链净频差一致）
        .phase_off_a(24'd0),
        .phase_inc_chip_b(-INC_CFO),          // B 通道: 假设 −CFO
        .phase_off_b(24'd0),
        .data_a(rx_data_a), .data_valid_a(rx_data_valid_a),
        .psdu_len_a(rx_psdu_len_a), .fcs_ok_a(rx_fcs_ok_a), .frame_done_a(rx_frame_done_a),
        .data_b(rx_data_b), .data_valid_b(rx_data_valid_b),
        .psdu_len_b(rx_psdu_len_b), .fcs_ok_b(rx_fcs_ok_b), .frame_done_b(rx_frame_done_b),
        .phase_out(rx_phase_out), .detect(rx_detect),
        .rot_a_i(), .rot_a_q(), .rot_a_dv(),
        .rot_b_i(), .rot_b_q(), .rot_b_dv(),
        .any_fcs_ok(rx_any_fcs_ok)
    );
endmodule
