// rtl/filelist.f —— 现行主链路（rx_dual 链）源码清单（2026-10-02 建）
// 用法: 编译/综合/回归统一从这里读，避免各 tb 脚本重复维护漂移。
// 参考: tb/rx_dual_mc/run_dual_mc.py SOURCES 与 tb/rx_dual/run.py 原清单取并集。
//
// [现行主链路] —— rx_dual（共享前端 + 双通道执行段），定点参考配置 W=16
rtl/common/chip_lut.sv
rtl/common/half_sine_fir.sv
rtl/common/pn9_whiten.sv
rtl/common/crc16_fcs.sv
rtl/rx/frontend/rx_matched_filter.sv
rtl/rx/frontend/preamble_sync.sv
rtl/rx/frontend/preamble_detect.sv
rtl/rx/frontend/preamble_buf.sv
rtl/rx/frontend/deinterleave.sv
rtl/rx/backend/cfo_rot.sv
rtl/rx/frontend/rx_frontend.sv
rtl/rx/backend/sfd_detect.sv
rtl/rx/backend/despreader.sv
rtl/rx/backend/rx_deframer.sv
rtl/rx/backend/rx_chip_backend.sv
rtl/rx/top/rx_dual.sv
//
// 候选（未启用, 供共享延迟线替换实验用；启用时把 preamble_sync.sv 换成它）：
// rtl/rx/frontend/preamble_sync_ref.sv
//
// [历史/参考, 勿删] —— 被 tb/cfo_corr 等历史测试引用:
// rtl/rx/legacy/rx_top.sv
// rtl/rx/legacy/rx_backend.sv
// rtl/rx/legacy/preamble_lock.sv
// rtl/rx/legacy/cfo_est.sv
// rtl/rx/legacy/cordic_atan2.sv
