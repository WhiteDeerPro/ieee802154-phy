# gtkwave_signals.tcl —— 启动时加入 TX 链的**关键观测点**（不是全量）
# ---------------------------------------------------------------------------
# 用法:
#   gtkwave -S tb/rtl_lab/gtkwave_signals.tcl tb/rtl_lab/sim_build/rtl_lab.vcd
#
# 按**数据流顺序**排列: 符号输入 → 码片/LUT → I/Q 注入 → FIR 成形 → 定点输出
#
# 想看别的时候, 在 GTKWave 左侧 Signal Search 里搜名字, 拖进来即可。

set want {
    rtl_lab_top.clk
    rtl_lab_top.ce_2m

    rtl_lab_top.sym
    rtl_lab_top.sym_valid
    rtl_lab_top.sym_ready

    rtl_lab_top.u_dut.chip
    rtl_lab_top.u_dut.chip_ce
    rtl_lab_top.u_dut.chip_cnt
    rtl_lab_top.u_dut.even_chip

    rtl_lab_top.u_dut.i_x
    rtl_lab_top.u_dut.q_x
    rtl_lab_top.u_dut.i_dv
    rtl_lab_top.u_dut.q_dv
    rtl_lab_top.u_dut.q_dly

    rtl_lab_top.i_out
    rtl_lab_top.q_out
    rtl_lab_top.sample_dv
}

gtkwave::addSignalsFromList $want
puts "GTKWave: 已加入 [llength $want] 个关键观测点 (数据流顺序: 符号 → 码片 → I/Q 注入 → 成形 → 定点输出)"
