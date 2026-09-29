# verdi_signals.tcl —— Verdi/nWave 自动加载 RX 链关键观测点（与 gtkwave_signals.tcl 同清单）
# ---------------------------------------------------------------------------
# 用法（**从项目根启动**；本机 Verdi 2018 SP2 的 -ssf CLI 参数实测不生效，
#       所以本脚本自行用 wvOpenFile 打开 fsdb，不需要 -ssf）:
#
#   cd <项目根>
#   verdi -play tb/rx_lab/verdi_signals.tcl -nologo
#
# （可用环境变量 CW_FSDB 覆盖 fsdb 路径；脚本在启动 3 秒后加载，避开 GUI 初始化竞态）

if {![info exists _nWave1]} { set _nWave1 [wvCreateWindow] }

proc _cw_load {} {
  global _nWave1

  # ① 打开 fsdb（默认相对路径 = 从项目根启动；CW_FSDB 可覆盖）
  set fsdb "tb/rx_lab/sim_build/rx_lab.fsdb"
  if {[info exists ::env(CW_FSDB)] && $::env(CW_FSDB) ne ""} { set fsdb $::env(CW_FSDB) }
  if {[catch {wvOpenFile $fsdb} err]} {
    puts "verdi_signals: wvOpenFile('$fsdb') 失败: $err  (cwd=[pwd], 请从项目根启动或用 CW_FSDB)"
    return
  }
  puts "verdi_signals: 已打开 $fsdb"

  # ② 按数据流顺序加信号（ADC → 匹配滤波 → 同步 → 解扩 → 解帧）
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
}

after 3000 _cw_load
