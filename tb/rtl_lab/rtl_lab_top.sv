// rtl_lab_top.sv —— 纯透传包装顶层, 只为**波形观测**存在
// ---------------------------------------------------------------------------
// 为什么需要它: cocotb 的 testbench 是 Python, 没有地方写 $dumpfile/$dumpvars;
// 而 VCS 的自动 dump 选项 (+vcs+dumpvars) 在本环境未生效、PLI 方式又需要
// Verilog TB。所以在最外层套一个**只做透传 + dump** 的模块:
//
//   · 端口与 oqpsk_modulator 一一同名同宽 —— 测试代码里 dut.xxx 无需改动;
//   · 内部只有例化 + initial dump, 不含任何逻辑, 不影响功能;
//   · bit-true 验证仍由 tb/oqpsk_modulator 负责, 本层不参与。
//
// 产出的 VCD 可直接用 Verdi 打开 (verdi -ssf rtl_lab.vcd) 手动看波形。
`timescale 1ns/1ps
module rtl_lab_top (
    input  wire               clk,
    input  wire               rst_n,
    input  wire               ce_2m,
    input  wire [3:0]         sym,
    input  wire               sym_valid,
    output wire               sym_ready,
    output wire signed [11:0] i_out,
    output wire signed [11:0] q_out,
    output wire               sample_dv
);

    oqpsk_modulator u_dut (
        .clk       (clk),
        .rst_n     (rst_n),
        .ce_2m     (ce_2m),
        .sym       (sym),
        .sym_valid (sym_valid),
        .sym_ready (sym_ready),
        .i_out     (i_out),
        .q_out     (q_out),
        .sample_dv (sample_dv)
    );

    // 波形 dump: 全层次, 0 = 含所有子模块信号 (能同时看到 FIR 的 pipe/acc 内部量)
    initial begin
        $dumpfile("rtl_lab.vcd");
        $dumpvars(0, rtl_lab_top);
    end

endmodule
