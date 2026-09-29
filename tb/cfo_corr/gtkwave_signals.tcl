# GTKWave 信号单 (精选 13 个, 按数据流顺序) —— 全量加载会很乱, 故只列关键端口
# 用法: gtkwave cfo_corr.vcd tb/cfo_corr/gtkwave_signals.tcl
gtkwave::loadFile "cfo_corr.fst"   # FST 比 VCD 小 10 倍 (785KB -> 74KB)
set sigs [list \
    cfo_corr_top.clk \
    cfo_corr_top.rst_n \
    cfo_corr_top.est_start \
    cfo_corr_top.dv_in \
    cfo_corr_top.i_in \
    cfo_corr_top.q_in \
    cfo_corr_top.est_done \
    cfo_corr_top.p_hat \
    cfo_corr_top.phase_inc \
    cfo_corr_top.rot_load \
    cfo_corr_top.i_out \
    cfo_corr_top.q_out \
    cfo_corr_top.dv_out ]
gtkwave::addSignalsFromList $sigs
gtkwave::/Time/Zoom/Zoom_Full
