// rtl/filelist.f —— 现行主链路（rx_dual 链）源码清单（2026-10-02 建）
// 用法: 编译/综合/回归统一从这里读，避免各 tb 脚本重复维护漂移。
//   VCS 冒烟示例（cwd=仓库根）: vcs -sverilog +incdir+rtl/rx -o /tmp/simv_smoke -f rtl/filelist.f
// 参考: tb/rx_dual_mc/run_dual_mc.py SOURCES 与 tb/rx_dual/run.py 原清单取并集。
//
// [现行主链路] —— rx_dual（共享前端 + 双通道执行段），定点参考配置 W=16
//   同步器现役 = Csq（I-18 转正, v1.2）；A16 保留为对照变体（见下）
//   despreader 现役 = variants/despreader_oct8_tdm.sv（构建宏 +DESP_OCT8_TDM +DESP_QUAD；
//   2026-10-04 基线切换, 见 docs/23）；无宏时例化本文件中的 backend/despreader.sv（旧基线, 逐位等价）
rtl/common/chip_lut.sv
rtl/common/half_sine_fir.sv
rtl/common/pn9_whiten.sv
rtl/common/crc16_fcs.sv
rtl/rx/frontend/rx_matched_filter.sv
rtl/rx/frontend/preamble_sync_csq.sv
rtl/rx/frontend/preamble_detect.sv
rtl/rx/frontend/preamble_buf.sv
rtl/rx/frontend/deinterleave.sv
rtl/rx/backend/cfo_rot.sv
rtl/rx/frontend/rx_frontend.sv
rtl/rx/backend/sfd_detect.sv
rtl/rx/backend/despreader.sv                         // 旧基线（无宏时例化）
rtl/rx/variants/despreader_oct8_tdm.sv               // 现役（DESP_OCT8_TDM 宏时例化; +DESP_QUAD=四边形档）
rtl/rx/backend/rx_deframer.sv
rtl/rx/backend/rx_chip_backend.sv
rtl/rx/top/rx_dual.sv
// 对照变体（A16 历史基线, 转正前现役；simv_ps_a16 / 影子对比用）：
// rtl/rx/variants/preamble_sync.sv
//
// 候选（未启用, 供共享延迟线替换实验用；启用时把 preamble_sync_csq.sv 换成它）：
// rtl/rx/variants/preamble_sync_ref.sv
//
// 候选（未启用）: rtl/rx/variants/despreader_oct8.sv —— 8 边形幅度检测（0 乘法; 门级 -74%;
//   整链零传导; MODE=1 为"零代价"档——去 DESP_QUAD 的 TDM 构建即用此度量）
// 候选（未启用）: rtl/rx/variants/despreader_oct8_pipe.sv —— 四级流水判决树（组合路径 -65%）
// 候选（未启用）: rtl/rx/variants/despreader_tdm.sv —— 平方/argmax 时分复用（旧版, $mul 32→6）
//
// [历史/参考, 勿删] —— 被 tb/cfo_corr 等历史测试引用:
// rtl/rx/legacy/rx_top.sv
// rtl/rx/legacy/rx_backend.sv
// rtl/rx/legacy/preamble_lock.sv
// rtl/rx/legacy/cfo_est.sv
// rtl/rx/legacy/cordic_atan2.sv
