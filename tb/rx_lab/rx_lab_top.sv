// rx_lab_top.sv —— RX 全链的纯透传包装顶层, 只为**波形观测**存在
// ---------------------------------------------------------------------------
// 与 tb/rtl_lab/rtl_lab_top.sv 对称: 端口与 rx_chain_e2e 一一同名同宽, 内部只有
// 例化 + $dumpfile/$dumpvars, 不含任何逻辑。
//
// $dumpvars(0, ...) 会连 rx_chain_e2e **内部的中间 wire** 一起 dump —— 那些正是
// 看 RX 链最想看的东西:
//     mf_i/mf_q      匹配滤波输出 (21bit 全精度)
//     chip_i/chip_q  同步后去交错得到的码片流
//     sym            解扩判决出的符号 (4bit)
//     frame_start    帧起点脉冲
//
// bit-true 验证仍由 tb/rx_chain_e2e 负责, 本层不参与。
`timescale 1ns/1ps
module rx_lab_top (
    input  wire                   clk,
    input  wire                   rst_n,
    input  wire signed [11:0]     i_in,          // ADC 12bit 复基带采样 @16Msps
    input  wire signed [11:0]     q_in,
    input  wire                   dv_in,
    input  wire [47:0]            ph_thresh,
    input  wire [47:0]            sfd_thresh,
    output wire                   detect,
    output wire [3:0]             locked_phase,
    output wire [7:0]             data_out,
    output wire                   data_valid,
    output wire [7:0]             psdu_len,
    output wire                   fcs_ok,
    output wire                   frame_done
);

    rx_chain_e2e u_dut (
        .clk         (clk),
        .rst_n       (rst_n),
        .i_in        (i_in),
        .q_in        (q_in),
        .dv_in       (dv_in),
        .ph_thresh   (ph_thresh),
        .sfd_thresh  (sfd_thresh),
        .detect      (detect),
        .locked_phase(locked_phase),
        .data_out    (data_out),
        .data_valid  (data_valid),
        .psdu_len    (psdu_len),
        .fcs_ok      (fcs_ok),
        .frame_done  (frame_done)
    );

    initial begin
        $dumpfile("rx_lab.vcd");
        // **不要用 $dumpvars(0, ...)**: RX 链有 179 个信号 x 7 万拍, 会把 VCD 撑到
        // 391 MB (VCD 是 ASCII, 每拍都要重写变了的信号, 开销极大), GTKWave 打不开。
        // 改成只 dump 关键层次: level=1 = 该模块内的信号, 不往下钻子模块内部。
        $dumpvars(1, rx_lab_top);                // 顶层端口 (ADC 输入 / PSDU 输出)
        $dumpvars(1, rx_lab_top.u_dut);          // e2e 内部 wire: mf_*, chip_*, sym, frame_start
        $dumpvars(1, rx_lab_top.u_dut.u_sync);   // 同步器内部状态: 扫描/锁定过程
    end

endmodule
