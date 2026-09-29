# verdi_signals.tcl —— Verdi/nWave 自动加载 RX 链关键观测点（与 gtkwave_signals.tcl 同清单）
# ---------------------------------------------------------------------------
# 用法:
#   verdi -ssf tb/rx_lab/sim_build/rx_lab.fsdb \
#         -play tb/rx_lab/verdi_signals.tcl -nologo
#
# 为什么需要它: Verdi 打开 fsdb 后 nWave 窗口默认是**空的**（fsdb 里明明有信号）；
# 这个脚本按数据流顺序（ADC → 匹配滤波 → 同步 → 解扩 → 解帧）自动加好关键观测点。
# fsdb 是全层次 dump —— 想看别的模块, 在 Verdi 左侧层次树里搜名字拖进来即可。

if {![info exists _nWave1]} {
  set _nWave1 [wvCreateWindow]
}
wvResizeWindow -win $_nWave1 1280 800
wvAddSignal -win $_nWave1 -clear

wvAddSignal -win $_nWave1 -group {"clk" \
  {/rx_lab_top/clk} \
}
wvAddSignal -win $_nWave1 -group {"ADC 输入" \
  {/rx_lab_top/dv_in} \
  {/rx_lab_top/i_in[11:0]} \
  {/rx_lab_top/q_in[11:0]} \
}
wvAddSignal -win $_nWave1 -group {"匹配滤波" \
  {/rx_lab_top/u_dut/mf_i} \
  {/rx_lab_top/u_dut/mf_q} \
  {/rx_lab_top/u_dut/mf_dv} \
}
wvAddSignal -win $_nWave1 -group {"同步" \
  {/rx_lab_top/detect} \
  {/rx_lab_top/locked_phase[3:0]} \
  {/rx_lab_top/u_dut/frame_start} \
}
wvAddSignal -win $_nWave1 -group {"解扩" \
  {/rx_lab_top/u_dut/chip_i} \
  {/rx_lab_top/u_dut/chip_q} \
  {/rx_lab_top/u_dut/chip_dv} \
  {/rx_lab_top/u_dut/sym} \
  {/rx_lab_top/u_dut/sym_dv} \
}
wvAddSignal -win $_nWave1 -group {"解帧 输出" \
  {/rx_lab_top/data_out[7:0]} \
  {/rx_lab_top/data_valid} \
  {/rx_lab_top/psdu_len[7:0]} \
  {/rx_lab_top/fcs_ok} \
  {/rx_lab_top/frame_done} \
}

wvZoomAll -win $_nWave1
wvScrollUp -win $_nWave1 1000
