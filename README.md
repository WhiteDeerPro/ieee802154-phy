# Wireless Communication SoC —— IEEE 802.15.4 (Zigbee) 2.4 GHz PHY 数字基带

数字 IC 练手项目。以「比特流 → 射频波形 → 比特流」完整链路为视角，**主导数字侧**（调制解调、同步、数字中频、数字接口）的设计与 RTL 实现；射频前端、ADC/DAC、天线作为协作边界。

- 目标制式：IEEE 802.15.4-2006 2.4 GHz PHY，O-QPSK + DSSS，250 kbps
- 载频 2405–2480 MHz（16 信道），码片率 2 Mchip/s，主时钟 16 MHz（每码片 8 采样）

## 目录结构

```
docs/    系统规格书、非理想效应、RTL 模块划分
model/   Python 浮点黄金模型 (golden model) —— RTL bit-true 比对基准
  ├─ baseband/        通用基带算法库 (制式无关)
  │   ├─ link.py      ★ 链路编排: Signal(观测点) / Stage(阶段) / Chain(tx·channel·rx)
  │   └─ ...          成形/匹配滤波、信道、损伤、同步、前端校正、均衡、扩频、编码
  ├─ phy_802154.py    802.15.4 制式原语 (CHIP 表 / DSSS / O-QPSK / PPDU / PN9 / CRC / 前导同步)
  ├─ algo/            ★ 算法层: 服务 RTL 的自适应/估计 (低复杂度换 BER)
  │   ├─ agc.py       增益/判决门限估计 (矩估计 / 决策导向)
  │   └─ dc_block.py  一阶 IIR 直流阻塞 + 盲 I/Q 失衡抑制
  ├─ chains.py        ★ 链路库: 把制式原语组装成可运行链路 (tx_stages / rx_stages / link / simulate)
  ├─ upper/           上层/主机侧组件 (非物理层: 参数记忆与模式匹配; 见 docs/12)
  │   ├─ patterns.py  接收模式库: 记忆/匹配/发现/淘汰 (演示"旋性可由上层管理"的参考实现)
  │   └─ observer.py  链路观测器: 跨帧证据累积 + 捕获状态机 + 服务调度 (见 docs/16)
  ├─ measure.py       测量层: BER/SER/EVM/SNR/星座软值/相关/频谱/眼图 (纯函数, 不画图)
  ├─ visualize.py     可视化层: 消费 measure 结果画图 (matplotlib 薄封装)
  ├─ filters.py        滤波器设计 (窗函数法) 与频响分析
  ├─ tests/           回归测试: test_chains (链路库) / test_migrated_scripts (实验脚本)
  └─ experiments/     实验脚本 (配置 + 扫参 + 出图) → 输出统一落到 model/out/
rtl/     Verilog/SystemVerilog (common / tx / rx)
tb/      cocotb 验证环境（每模块一个目录）
```

## 链路模型

链路被显式建模为**观测点 + 可插拔阶段**（`baseband.link`），换制式只需换配方：

```
载荷 ─┬─ 扰码 ─┬─ 扩频 ─┬─ 成形 ─┬═[信道]═┬─ 同步 ─┬─ 匹配滤波 ─┬─ 解扩 ─┬─ 解帧 ─┬─ 载荷
      │        │        │        │         │        │            │        │        │
   payload → bits →  symbols → chips →   tx   →   rx   →   rx_chips → rx_soft → rx_symbols → rx_payload
                                      └────── 观测点 (probe points) ──────┘
```

- **`Signal`** 一次链路运行的信号上下文，每个具名字段就是一个观测点；
  `measure` 全部从它取值（星座图读 `rx_soft`，眼图读 `rx`，BER 读 `rx_symbols`/`symbols`）。
- **`Stage`** 一个处理步骤 `fn(sig) -> None`。损伤注入、校正、同步都是普通阶段，
  可在链上任意位置插入；新增一种损伤 = 加一个阶段，不必改动编排层或既有脚本。
- **`Chain`** 阶段的有序集合，分 `tx` / `channel` / `rx` 三段，列表顺序即作用顺序。

典型用法（完整实验只需这几行）：

```python
from baseband import impairments as imp
import chains, measure

chain = chains.link(
    [imp.multipath_stage([1, .5], [0, .5]),   # 多径
     imp.cfo_stage(50e3),                     # 载波频偏（星座图旋转）
     imp.iq_stage(2.0, 10.0),                 # I/Q 不平衡（镜像）
     imp.dc_stage(0.35, 0.30),                # 零偏
     chains.awgn(-1.0, rng)],                 # AWGN
    cfo="two_stage", dc_iq=True, deframe=True)

sig = chain.run(payload=psdu)
print(measure.format_report(measure.link_report(sig)))
```

阶段参数可以是常量，也可以是 `lambda sig: ...` —— 后者在运行到该阶段时求值，
用于「参数依赖前序阶段结果」的场景（如按估计出的 CFO 做逆 SFO 重采样）。

**换制式**：在 `chains.py` 另写一组同形状的配方（例如 QAM：跳过 `stage_spread`、
把 `stage_despread` 换成星座判决、加 `stage_fec`），`baseband.link` / `measure` /
`visualize` 三层均无需改动。

## 快速开始（model）

实验脚本在 `model/experiments/`（配置 + 扫参 + 出图），输出统一落到 `model/out/`。
从仓库根直接跑：

```bash
python model/experiments/run_study.py              # ★ 自由组合实验: 调制 × 成形 × 非理想效应 → out/study/ (12 个实例)
python model/experiments/run_filter_study.py       # 窗函数与 FIR 设计: 窗对比 / 阻带-过渡带权衡 / Kaiser β → out/filter_study/
python model/experiments/run_algo_study.py         # 算法层收益: DC / I-Q 失衡下的 BER 对比 (raw vs 前导LS vs 盲估计) → out/algo_study/
python model/experiments/run_link_report.py        # 单个实例示范: 一条链路的完整观测报告 → out/link_report/
python model/experiments/run_ber.py                # BER vs 码片 SNR 曲线，验证扩频增益 ≈ 9 dB
python model/experiments/run_impairments.py        # CFO / 定时 / 多径 损伤曲线
python model/experiments/run_quant.py              # ADC 量化位宽扫描 (4bit 假设验证) → out/ber/
python model/experiments/run_sfo.py                # 采样率偏差 (SFO) 扫描: 帧长 × ppm → out/impairments/
python model/experiments/run_sfo_fix.py            # SFO 修复方案对比: 只消旋 / 逆重采样 / Farrow 插值 → out/impairments/
python model/experiments/run_joint_sfo_cfo.py      # 同源 ε 联合验证: CFO 消旋 + 逆 SFO 重采样 (127B 长帧, 40 ppm)
python model/experiments/run_cfo_study.py          # CFO 分解: 判决环 vs 同步环 (谁先死) → out/cfo_study/
python model/experiments/run_cfo_visual.py         # CFO 可视化专题: 成因/频谱/眼图/2ω 振荡 → out/cfo_visual/
python model/experiments/run_cfo_fix.py            # CFO 修复闭环: 前导联合估计(对齐点+频偏) + 整帧消旋 → out/cfo_fix/
python model/experiments/run_cfo_fft.py            # 单帧 256 点 FFT 的 CFO 估计 (模式发现的核心工具) → out/cfo_fft/
python model/experiments/run_cfo_eye.py            # CFO 修正后的眼图 (浮点参考侧, 对照 tb/cfo_corr 的 RTL 版) → out/ref/
python model/experiments/run_preamble_detect.py    # 前导/突发检测器四方法对比 (能量/双窗/自相关/匹配滤波) → out/preamble_detect/
python model/experiments/run_preamble_mag.py       # 前导检测判据对比: 实部 vs 复相关模 → out/preamble_mag/
python model/experiments/run_mc_model.py "-6,-4,-2,0,2" 2000
                                                   # 浮点模型蒙特卡洛 (RTL 侧见 tb/rx_chain_e2e/run_mc.py)
python model/experiments/analyze_mc.py             # RTL vs 模型 BER 对比报告 + 图 → out/rtl_ber/
python model/experiments/run_rtl_lab.py            # RTL 联合实验: oqpsk_modulator 定点点位 I/Q 导出 → out/rtl_lab/
python model/experiments/run_frontend.py           # CFO / DC / I/Q 前端校正闭环（BER 恢复 + CFO 残差）
python model/experiments/run_multipath_eq.py       # 多径均衡收益: 基线 vs 前导LS+MMSE均衡 → out/impairments/
python model/experiments/run_visual.py             # 全套可视化 → out/vis/（波形/频谱/星座/眼图/加扰/变频…）
python model/experiments/run_eye_modulations.py    # 多调制眼图对比: BPSK/QPSK/4-ASK/16-QAM/O-QPSK × {理想, +AWGN, +AWGN+CFO} → out/vis/
python model/experiments/run_oqpsk_comprehensive.py # OQPSK 完整可视化: 基带波形/调制信号/PSD/星座/EVM → out/oqpsk_visual/
python model/experiments/run_e2e_file.py           # 全流程演练: README 文本 → 比特流 → 波形 → 损伤 → 还原 → out/vis/
python model/experiments/run_pattern_lib.py        # 模式库验证: 多设备帧流的匹配/发现/淘汰 → out/pattern_lib/
python model/experiments/run_pattern_link.py       # 模式库接入链路: two_stage vs pattern (跨帧记忆) → out/pattern_link/
python model/experiments/run_observer_replay.py    # 观测器回放: RTL 估计序列 → 捕获/热启动对比 → out/observer/
python model/experiments/run_observer_multidev.py  # 多设备场景 + 服务调度 → out/observer/
python model/experiments/run_rtl_residual.py       # RTL 侧残余 CFO 容限(外部参数通道错配扫描) → out/rtl_residual/
python model/experiments/run_residual_viz.py       # 残余 CFO 表征: 眼图 + 星座 → out/residual_viz/
python model/experiments/bandpass_sampling_demo.py # 带通采样 + DDC 数学性质 → out/bandpass/
```

也可以 `cd model/experiments` 后直接 `python run_ber.py`（脚本自身负责把 `model/` 加入
`sys.path`，两种调用方式等价）。

图件按主题分目录：`out/vis/`（可视化）、`out/ber/`（误码率）、`out/impairments/`（损伤）、`out/bandpass/`（带通采样）、`out/ref/`（外部参考图）。

链路回归测试（改动链路库或实验脚本后跑一次，确认数值未变）：

```bash
python model/tests/test_chains.py            # 链路库 vs 迁移前逻辑逐比特比对
python model/tests/test_migrated_scripts.py  # 迁移后的实验脚本 vs 原逻辑参照
```

## 实验实例（`out/<实例名>/`）

每个通信实验是一个**实例**: 产出到 `out/<实例名>/`, 内含该实验的全套观测 —— 读者不必重跑就能
看懂「这条链路发生了什么」。组织与落盘由 `instance.py` 统一负责, 未调用的产出不会出现:

```
out/<实例名>/
  ├─ report.md          分析结论与关键数值 (close() 时自动汇总)
  ├─ bits.txt           基带码片段 (过长自动截断并标注)
  ├─ symbols.csv        符号序列
  ├─ waveform.png       时域: 发射 / 信道 / 接收 各阶段波形
  ├─ spectrum.png       频谱 (幅度)
  ├─ energy.png         能量谱 / PSD (dB/Hz)
  ├─ filters.png        滤波器: 成形脉冲的幅频与系数
  ├─ noise.png          加噪对照: 全带白噪声 vs 带限(带内)噪声
  ├─ constellation.png  星座图
  └─ eye.png            眼图 (可选)
```

示范见 `experiments/run_study.py`（12 个组合：调制 × 成形 × 效应）与
`experiments/run_link_report.py`（单实例，改 `SPEC` 换调制/成形/信噪比）。
眼图产出会同时给出**量化**：眼高（最内层眼的最小电平间距）与判决点 SNR —— 按
行业惯例（MathWorks/Tektronix 口径）。眼宽/定时余量需扫 jitter 才能给出。

**成形脉冲**三种可选，均归一到单位能量以便横向比较：

- `mod.raised_cosine(beta, sps, span)` —— 升余弦，跨符号重叠、频带受控
- `mod.sine_burst(cycles, sps_per_cycle)` —— 「乘法器输出」式：一个符号含整数个正弦周期，
  完全落在符号内（无 ISI 尾巴）、频谱旁瓣高；`sps_per_cycle` 即每周期采样点数（≥10 时波形上能看清正弦）
- `mod.half_sine(sps)` —— 半正弦，与 802.15.4 同族

## 黄金模型覆盖

- **TX 全链**：组帧 → PN9 白化 → CRC-16 → DSSS 扩频（4 bit → 32 码片）→ O-QPSK 半正弦成形
- **RX 全链**：匹配滤波 → 前导同步 → 解扩（非相干 |·| 检测）→ 去白化 → 解帧
- **损伤模型**：AWGN（全带 / 带限）/ CFO / 采样定时（静态 + SFO 采样率偏差 + 孔径抖动）/ 多径 / DC 偏移 / I/Q 不平衡 / 镜像干扰 / ADC 量化（位宽扫描）
- **前端校正**：CFO 估计（FFT 谱峰粗估 + 前导相位差分精估，无模糊鲁棒）+ 数字混频纠正；DC/IQ 前导码联合 LS 校正
- **多径均衡**：前导 LS 信道估计 + 频域 MMSE 均衡（`run_multipath_eq` 量化增益边界）

## 通用基带库（`model/baseband/`）

与制式解耦的基带算法组件，供 `phy_802154` 复用，将来接入其它 O-QPSK/PSK 制式可直接调用。
分层参考 CommPy（`channels/filters/modulation/impairments` 平铺 + 制式实例）与
MATLAB Communications Toolbox（调制器/信道/同步器/均衡器对象）：

- `modulation`    成形脉冲（半正弦 / 升余弦 / sine_burst）+ 匹配滤波 + 星座映射与成形
- `filters`       窗函数法 FIR 设计 + 频响 / 阻带 / 占用带宽分析
- `channels`      AWGN / 多径 / Rayleigh 信道
- `impairments`   CFO/定时/DC/IQ/镜像干扰/量化/SFO 损伤注入 (纯函数 + `*_stage` 阶段工厂)
- `sync`          消旋 `correct_cfo`（载波同步）
- `frontend`      DC/IQ 联合 LS 前端校正
- `equalization`  信道估计 LS + 频域 MMSE 均衡
- `coding`        通用 CRC + LFSR 扰码
- `spreading`     通用 DSSS 扩频/解扩
- `link`          链路编排 `Signal` / `Stage` / `Chain`（见上节「链路模型」）

## 链路预算（定性）

| 项 | 值 | 备注 |
|---|---|---|
| 热噪声底 kTB（2 MHz） | −111 dBm | 室温 |
| 接收机 NF（假设） | 8 dB | 待 RF 协作确认 |
| 噪声底 | −103 dBm | |
| 灵敏度目标 | ≤ −85 dBm（假设） | 商用 CC2530 级 ≈ −97 dBm |
| 可用 SNR @ −85 dBm | ≈ 18 dB | |
| 扩频增益（32 码片/符号） | 9.03 dB | |
| 解调需求（BER=1e-3） | 码片 SNR ≈ −1 dB（实测 run_ber） | Eb/N0 ≈ 8 dB |

结论：−85 dBm 目标下可用码片 SNR ≈ 18 dB，远高于解调需求 ≈ −1 dB（BER=1e-3），**余量 ≈ 19 dB**——说明 −85 dBm 是保守目标，实际可下探至更低灵敏度（受限于同步/CFO 而非解扩）。

## 当前状态

- **Phase 0 完成**：黄金模型 + 全套可视化 + BER / 损伤 / 前端闭环验证（CFO 残差 0.34 kHz ≪ 7 kHz 需求）
- **RTL TX 链闭环**：`tx_framer` → `oqpsk_modulator` 通过 cocotb bit-true 回归
- **RTL RX**：`rx_matched_filter` / `despreader` / `preamble_sync` / `rx_deframer` 单元级 + RX 链级闭环 `test_rx_chain` + 全链端到端 `test_e2e`（ADC 12bit 量化 → MF → 同步 → 解扩 → 解帧，含噪整链还原 PSDU + FCS 校验）全部通过 cocotb bit-true 回归（`tb/` 下各 `run.py`，共 14 个验证点；其中 `tb/rtl_lab` 额外把
`oqpsk_modulator` 的 12bit 定点 I/Q 导出给模型侧做联合实验，见 `model/experiments/run_rtl_lab.py`）
- **RTL CFO 修复闭环**（`tb/cfo_corr`）：`cfo_est`（8 相位候选联合搜索，对齐点+频偏一次定；不依赖上游同步）+
`cordic_atan2`（16 级向量模式，带象限预处理）+ `cfo_rot`（24bit 相位累加器 + 256 点 LUT + 复乘）。
实测：0~450 kHz 内估计误差 **< 11 Hz**（无噪）/ 0.03~0.05 kHz（有噪），消旋 **bit-true**；
BER 从 0.33~0.79 全部回到零错误，前导相关峰（同步环）恢复 5.5 倍。出图见 `model/out/rtl_cfo/`，
波形 `tb/cfo_corr/sim_build/cfo_corr.fst`（74 KB，信号单 `gtkwave_signals.tcl`）
- **RX 顶层集成**（`rtl/rx/rx_top.sv`，2026-09-29）：`ADC(12bit) → rx_matched_filter → cfo_rot → preamble_sync → despreader → rx_deframer` 的可综合顶层；`preamble_detect`（短窗归一化延迟自相关，对 CFO 免疫）已接入，当前作观测输出。验证链 `tb/rx_chain_e2e/mc_top_tb.sv`
- **上层旋性管理**（`docs/12` 架构决策 + `model/upper/patterns.py` 参考实现）：单元只做消旋，旋性的发现 / 记忆 / 匹配 / 淘汰放上层或转发出去；待办 issue 见 `docs/14`（I-8…I-16）
- **链路观测器**（`model/upper/observer.py` + `docs/16`）：观测多帧 → 估计链路状态 → 控制矫正。
Python ref（多状态共存 + 证据累积 + SEARCH/VERIFY/LOCK + RRM 风格服务调度，`test_observer` 18/18）
+ C 实现（`observer_dpi.c`）经 DPI-C 在仿真中实时运行；外部参数通道 `rx_top.ext_inc_*` 已打通
（决策层解耦）。实测：回放对比 0.9 kHz vs 单帧硬判决 78.5 kHz；共享分辨单元（1 状态服务 8 台）；
残余容限 RTL 侧 δ≤2 kHz 无损、10 kHz 仍可用 65%
- **遗留**：自主触发（`preamble_detect` → `cfo_est`）尚未收敛（方案 C 后 25/50），当前交付为外部触发 ——
专题总览见 `docs/15`，探索记录见 `docs/08` I-6，重写计划见 I-17
- **下一步**：I-13 前导码片缓冲（观测上行）+ 异常帧上报（`docs/16` §9），随后 I-15 接口闭环联调

## 参考（README/文档级调研）

- [ucb-bar/baseband-modem](https://github.com/ucb-bar/baseband-modem) — SoC 外设式基带调制解调器（文档体系范本）
- [bastibl/gr-ieee802-15-4](https://github.com/bastibl/gr-ieee802-15-4) — O-QPSK 软件收发（非相干相关接收参照）
- [nexuslrf/gr-oqpsk_dsss](https://github.com/nexuslrf/gr-oqpsk_dsss) — O-QPSK + DSSS 教学实现（PN 序列表来源）
