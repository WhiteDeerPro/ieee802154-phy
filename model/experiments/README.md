# model/experiments —— 实验脚本索引

> 运行：`python model/experiments/<name>.py`（各脚本自带 `model/` 引导；部分需 VCS 仿真环境）。
> 公共样板：`_common.py`（Agg 后端 + 中文字体 + `out_dir`）；"实验实例"规范：`model/instance.py`。
> 设计与实验全日志：`model/out/dual_mc/rtl_area_notes.md`（§1–40）。
> I-21 样板统一状态：**41/51 使用 `_common`，其余 10 个经核无需**（无手写样板可迁）。

## A. 制式链与系统实验

| 脚本 | 说明 | 产出 |
|---|---|---|
| `run_study` | 自由组合：调制 × 成形 × 非理想效应（12 实例） | `out/study/` |
| `run_link_report` | 一条链路的完整观测报告（实例规范示范） | `out/link_report/` |
| `run_impairments` | 非理想性定量（CFO/定时/多径） | `out/impairments/` |
| `run_frontend` | 前端损伤闭环（CFO/DC/IQ 校正 BER 恢复） | 终端 |
| `run_multipath_eq` | 多径均衡收益（前导 LS + MMSE） | `out/impairments/` |
| `run_joint_sfo_cfo` | 同源 ε：消旋 + 逆 SFO 重采样联合验证 | 终端 |
| `run_visual` | 机制型可视化包（波形/频谱/星座/眼图…） | `out/vis/` |
| `run_eye_modulations` | 多调制眼图对比 | `out/vis/` |
| `run_oqpsk_comprehensive` | O-QPSK 完整可视化（PSD/星座/EVM） | `out/oqpsk_visual/` |
| `run_e2e_file` | 全流程演练：文本 → 比特流 → 波形 → 还原 | `out/vis/` |
| `bandpass_sampling_demo` | 带通采样 + DDC 数学性质 | `out/bandpass/` |
| `run_spread_approx` | 扩频近似误差分析 | `out/spread_approx/` |
| `run_quant` | ADC/DAC 量化位宽扫描（4/6/8/12 bit） | `out/ber/` |
| `run_ber` | BER vs 码片 SNR（验证扩频增益 ≈ 9 dB） | `out/ber/` |
| `run_algo_study` | 算法层收益（DC/IQ 下 raw vs LS vs 盲估计） | `out/algo_study/` |
| `run_filter_study` | 窗函数与 FIR 设计（窗对比/Kaiser β） | `out/filter_study/` |

## B. CFO / SFO 专题

| 脚本 | 说明 | 产出 |
|---|---|---|
| `run_cfo_study` | CFO 分解：判决环 vs 同步环（谁先死） | `out/cfo_study/` |
| `run_cfo_visual` | CFO 成因/影响/修正可视化专题 | `out/cfo_visual/` |
| `run_cfo_fix` | CFO 修复闭环（前导联合估计 + 整帧消旋） | `out/cfo_fix/` |
| `run_cfo_eye` | CFO 修正后眼图（浮点参考侧） | `out/ref/` |
| `run_cfo_fft` | 单帧 256 点 FFT 的 CFO 估计（模式发现核心工具） | `out/cfo_fft/` |
| `run_residual_cfo` | 残余 CFO 容限细扫（分辨单元宽度） | `out/cfo_residual/` |
| `run_residual_viz` | 残余 CFO 表征（眼图 + 星座） | `out/residual_viz/` |
| `run_win_cfo` | "多少点够用"：窗长 vs CFO 估计精度 | `out/dual_mc/` |
| `run_rtl_residual` | RTL 侧残余容限（外部参数通道错配扫描） | `out/rtl_residual/` |
| `run_sfo` | SFO 扫描：BER vs ppm × 帧长 | `out/impairments/` |
| `run_sfo_fix` | SFO 修复对比（消旋/逆重采样/Farrow） | `out/impairments/` |

## C. 前导检测 / 同步专题

| 脚本 | 说明 | 产出 |
|---|---|---|
| `run_preamble_detect` | 四类检测器对比（能量/双窗/自相关/匹配滤波） | `out/preamble_detect/` |
| `run_preamble_mag` | 判据对比：实部 vs 复相关模（方案 C） | `out/cfo_trigger/` |
| `run_pd_matrix` | 检测参数矩阵（γ × 确认拍 × 判据源） | `out/dual_mc/` |
| `run_gamma_scan` | γ 灵敏度/虚警折衷（6dB vs 20dB） | `out/dual_mc/snr6/` |
| `run_noise_fa` | 纯噪声虚警率直接测量 | `out/dual_mc/noise_only/` |
| `run_snr_est` | "能否 estSNR"：能量法 SNR 估计验证 | `out/dual_mc/` |
| `run_ps_arch` | 同步器架构级替代方案对比（model 侧） | `out/ps_arch/` |
| `run_ps_e2e` | 相位对齐偏移 δ 容限 + 实读成功率 | `out/ps_arch/` |
| `run_edge_scan` | 边界扫描：SNR 网格 × pd 抖动 × 两方案成败 | `out/dual_mc/edge/` |

## D. model ↔ RTL 对照 / 蒙特卡洛

| 脚本 | 说明 | 产出 |
|---|---|---|
| `run_mc_model` | 浮点模型 MC（与 RTL 同 SNR 口径） | `out/rtl_ber/data/` |
| `analyze_mc` | MC 汇总：RTL vs 模型 BER 对比图 | `out/rtl_ber/` |
| `run_rtl_lab` | RTL 定点 I/Q 导出的联合分析 | `out/rtl_lab/` |
| `run_chain_report` | 链路综合报告（多设备 × SNR × 通道替换） | `out/dual_mc/chain_report*/` |
| `run_tx_wave` | 发射波形检查（数字域版） | `out/tx_wave/` |

## E. upper：模式库 / 观测器 / 多设备

| 脚本 | 说明 | 产出 |
|---|---|---|
| `run_pattern_lib` | 模式库：匹配/发现/淘汰 | `out/pattern_lib/` |
| `run_pattern_link` | 模式库接入链路（two_stage vs pattern） | `out/pattern_link/` |
| `run_observer_replay` | RTL 估计序列回放观测器（捕获/热启动） | `out/cfo_trigger/` |
| `run_observer_multidev` | 多设备场景 + 服务调度 | `out/observer/` |
| `run_multidev_scene` | 三设备 × 三模式帧通信场景 | `out/multidev_scene/` |
| `run_multichannel` | 多通道并行接收（每通道自带消旋器） | `out/multichannel/` |

## F. 工具 / 专题（v1.x 新增）

| 脚本 | 说明 | 产出 |
|---|---|---|
| `run_bfp_blk` | 块浮点块长 vs 空间效率（"直接浮点"对比） | 终端 |
| `run_codebook` | 码本对比：PN vs Walsh-Hadamard | `out/codebook/` |
| `run_dapping` | 重叠波形：两设备信号叠加双通道接收 | `out/dapping/` |
| `run_channel_budget` | 通道数 sweet point：边际覆盖 vs 成本 | `out/channel_budget/` |

## 约定（新脚本指南）

1. 引导：`sys.path.insert(0, str(Path(__file__).resolve().parent.parent))`（库在 `model/`）；
2. 画图/输出目录：`import _common; plt = _common.init(); OUT = _common.out_dir("<name>")`；
3. 需要"标准实验实例"（report.md + 全套图）时用 `model/instance.py` 的 `Instance`；
4. 用 VCS 仿真的脚本：引导 `tb/rx_dual_mc` + `tb/rx_chain_e2e`，复用 `run_dual_mc` 的
   `build()/VCS_ENV/inc_from_cfo`；场景缺失时先跑 `tb/rx_dual_mc/regen_scenes.py`。
