# verdi_signals.tcl —— Verdi/nWave 自动加载 TX 链关键观测点（与 gtkwave_signals.tcl 同清单）
# ---------------------------------------------------------------------------
# 用法（**从项目根启动**；本机 Verdi 2018 SP2 的 -ssf CLI 参数实测不生效，
#       所以本脚本自行用 wvOpenFile 打开 fsdb，不需要 -ssf）:
#
#   cd <项目根>
#   verdi -play tb/rtl_lab/verdi_signals.tcl -nologo
#
# （可用环境变量 CW_FSDB 覆盖 fsdb 路径；脚本在启动 3 秒后加载，避开 GUI 初始化竞态）

if {![info exists _nWave1]} { set _nWave1 [wvCreateWindow] }

proc _cw_load {} {
  global _nWave1

  # ① 打开 fsdb（默认相对路径 = 从项目根启动；CW_FSDB 可覆盖）
  set fsdb "tb/rtl_lab/sim_build/rtl_lab.fsdb"
  if {[info exists ::env(CW_FSDB)] && $::env(CW_FSDB) ne ""} { set fsdb $::env(CW_FSDB) }
  if {[catch {wvOpenFile $fsdb} err]} {
    puts "verdi_signals: wvOpenFile('$fsdb') 失败: $err  (cwd=[pwd], 请从项目根启动或用 CW_FSDB)"
    return
  }
  puts "verdi_signals: 已打开 $fsdb"

  # ② 按数据流顺序加信号（符号 → 码片/LUT → I/Q 注入 → FIR → 定点输出）
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

  wvZoomAll -win $_nWave1
  wvScrollUp -win $_nWave1 1000
}

after 3000 _cw_load
