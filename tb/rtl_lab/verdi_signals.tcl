# verdi_signals.tcl —— Verdi/nWave 启动时自动加入 TX 链关键观测点（不是全量）
# ---------------------------------------------------------------------------
# 用法:
#   verdi -ssf tb/rtl_lab/sim_build/rtl_lab.fsdb \
#         -play tb/rtl_lab/verdi_signals.tcl -nologo
#
# 为什么需要它: Verdi 打开 fsdb 后 nWave 窗口默认是**空的**（fsdb 里明明有信号），
# 手动拖信号很烦 —— 这个脚本把关键观测点按数据流顺序自动加好，并给 I/Q 设模拟显示。
# 信号清单与 `gtkwave_signals.tcl` 保持一致。
# 想看别的时候, 在 Verdi 左侧层次树里搜名字拖进来即可。

if {![info exists _nWave1]} {
  set _nWave1 [wvCreateWindow]
}
wvResizeWindow -win $_nWave1 1280 800
wvAddSignal -win $_nWave1 -clear

wvAddSignal -win $_nWave1 -group {"clk_ctrl" \
  {/rtl_lab_top/clk} \
  {/rtl_lab_top/rst_n} \
  {/rtl_lab_top/ce_2m} \
}
wvAddSignal -win $_nWave1 -group {"sym_in" \
  {/rtl_lab_top/sym[3:0]} \
  {/rtl_lab_top/sym_valid} \
  {/rtl_lab_top/sym_ready} \
}
wvAddSignal -win $_nWave1 -group {"chip_lut" \
  {/rtl_lab_top/u_dut/chip} \
  {/rtl_lab_top/u_dut/chip_ce} \
  {/rtl_lab_top/u_dut/chip_cnt} \
  {/rtl_lab_top/u_dut/even_chip} \
}
wvAddSignal -win $_nWave1 -group {"iq_inject" \
  {/rtl_lab_top/u_dut/i_x} \
  {/rtl_lab_top/u_dut/q_x} \
  {/rtl_lab_top/u_dut/i_dv} \
  {/rtl_lab_top/u_dut/q_dv} \
  {/rtl_lab_top/u_dut/q_dly} \
}
wvAddSignal -win $_nWave1 -group {"iq_out" \
  {/rtl_lab_top/i_out[11:0]} \
  {/rtl_lab_top/q_out[11:0]} \
  {/rtl_lab_top/sample_dv} \
}

# I/Q 想做模拟显示: 在 nWave 里选中信号 → 右键 → Waveform Type → Analog。
# （wvBusWaveform 在本 Verdi 版本 -play 早期阶段会报错, 暂不自动设置。）

wvZoomAll -win $_nWave1
wvScrollUp -win $_nWave1 1000
