// rtl/filelist.f —— 现行主链路（rx_dual 链）源码清单（2026-10-02 建）
// 用法: 编译/综合/回归统一从这里读，避免各 tb 脚本重复维护漂移。
// 参考: tb/rx_dual_mc/run_dual_mc.py SOURCES 与 tb/rx_dual/run.py 原清单取并集。
//
// [现行主链路] —— rx_dual（共享前端 + 双通道执行段），定点参考配置 W=16
rtl/common/chip_lut.sv
rtl/common/half_sine_fir.sv
rtl/common/pn9_whiten.sv
rtl/common/crc16_fcs.sv
rtl/rx/rx_matched_filter.sv
rtl/rx/preamble_sync.sv
rtl/rx/preamble_detect.sv
rtl/rx/preamble_buf.sv
rtl/rx/deinterleave.sv
rtl/rx/cfo_rot.sv
rtl/rx/rx_frontend.sv
rtl/rx/sfd_detect.sv
rtl/rx/despreader.sv
rtl/rx/rx_deframer.sv
rtl/rx/rx_chip_backend.sv
rtl/rx/rx_dual.sv
//
// 候选（未启用, 供共享延迟线替换实验用；启用时把 preamble_sync.sv 换成它）：
// rtl/rx/preamble_sync_ref.sv
//
// [历史/参考, 勿删] —— 被 tb/cfo_corr 等历史测试引用:
// rtl/rx/rx_top.sv
// rtl/rx/rx_backend.sv
// rtl/rx/preamble_lock.sv
// rtl/rx/cfo_est.sv
// rtl/rx/cordic_atan2.sv
