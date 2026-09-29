# gtkwave_signals.tcl —— 启动时加入 RX 链的**关键观测点**（不是全量）
# ---------------------------------------------------------------------------
# 用法:
#   gtkwave -S tb/rx_lab/gtkwave_signals.tcl tb/rx_lab/sim_build/rx_lab.fst
#
# 为什么不用"全部加入": 这条链有 91 个信号, 全加进波形窗口既长又难找重点 ——
# 调试验证时真正要看的只有下面这 20 个, 按**数据流顺序**排列:
#
#   ADC 输入 → 匹配滤波 → 同步 → 解扩 → 解帧
#
# 想看别的时候, 在 GTKWave 左侧 Signal Search 里搜名字, 拖进来即可。

set want {
    rx_lab_top.clk

    rx_lab_top.dv_in
    rx_lab_top.i_in
    rx_lab_top.q_in

    rx_lab_top.u_dut.mf_i
    rx_lab_top.u_dut.mf_q
    rx_lab_top.u_dut.mf_dv

    rx_lab_top.detect
    rx_lab_top.locked_phase
    rx_lab_top.u_dut.frame_start

    rx_lab_top.u_dut.chip_i
    rx_lab_top.u_dut.chip_q
    rx_lab_top.u_dut.chip_dv

    rx_lab_top.u_dut.sym
    rx_lab_top.u_dut.sym_dv

    rx_lab_top.data_out
    rx_lab_top.data_valid
    rx_lab_top.psdu_len
    rx_lab_top.fcs_ok
    rx_lab_top.frame_done
}

gtkwave::addSignalsFromList $want
puts "GTKWave: 已加入 [llength $want] 个关键观测点 (数据流顺序: ADC → MF → 同步 → 解扩 → 解帧)"
