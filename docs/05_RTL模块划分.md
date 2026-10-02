# RTL 模块划分（v1.0）

> 2026-09-25 ｜ 粒度原则：**模块 = 黄金模型函数级**——每个模块对应 `model/phy_802154.py`
> 中一个函数（或自然分组），可单独用 cocotb 与 Python 黄金模型 bit-true 比对。
> 叶子原语在 TX/RX 两侧复用。
>
> **状态（2026-10-01）：TX 链闭环 + RX 全链闭环 + CFO 修复闭环 + 顶层集成（`rx_top.sv`）；
> 全部 cocotb 回归通过；RTL BER 蒙特卡洛平台已建成**
> （`tb/rx_chain_e2e/mc_gen.py` / `mc_tb.sv` / `run_mc.py`，文件向量驱动）。
> 2026-09-28 修复两个同步器设计缺陷（详见时序说明与 `model/out/rtl_ber/report.md`）：
> ① `preamble_sync` ST_LOCK 无帧尾退出路径 → 连续帧不可接；
> ② 扫描器相位覆盖仅 6/16（仅覆盖偶片峰∈[0,8) 的帧到达相位）→ 扩为 16 候选全相位覆盖。
> 待修：固定门限同步在低 SNR 失效（误锁 → 崩溃区，报告 §3 已列修复方向）。
> TX 链: PSDU 字节流 →（组帧+白化+FCS+扩频+O-QPSK+成形）→ 16 Msps I/Q，
> 与黄金模型 `modulate_oqpsk_fixed(tx_symbols(psdu))` 全采样一致。
> RX 链已闭环到帧同步: `rx_matched_filter`(21bit 全精度) / `despreader`(16 路相关 |·|² argmax)
> / `preamble_sync`（含噪突发帧下 bit-true + 解扩功能断言）
> / `rx_deframer`（单元级: 符号流还原 PSDU + FCS 失败用例，2/2 PASS）。
> 链级闭环 `test_rx_chain`（preamble_sync → despreader → rx_deframer 含噪整链还原 PSDU + FCS 校验）通过。
> 全链端到端 `test_e2e`（`tb/rx_chain_e2e/`：ADC 12bit 量化 → MF → sync → despread → deframe）通过。
> CFO 修复闭环（`tb/cfo_corr/`，2026-09-27/29）：`cfo_est`（8 相位候选联合搜索，不依赖上游同步）
> + `cordic_atan2` + `cfo_rot`；0~450 kHz 估计误差 < 11 Hz（无噪）/ 0.03~0.05 kHz（有噪），消旋 bit-true。
> 顶层集成（`rtl/rx/legacy/rx_top.sv`，2026-09-29）：ADC → MF → cfo_rot → preamble_sync → despreader → rx_deframer；
> `preamble_detect`（短窗延迟自相关，CFO 免疫）并联接入，当前作观测输出。
> 下一步: 自主触发收敛（重写 `cfo_est` COLLECT 段，见 `docs/08` I-6）、DC/IQ 校正 RTL、低 SNR 同步判据修复。

## 目录结构

```
rtl/
  common/            # 两侧复用的叶子原语
    chip_lut.sv        # 符号(4b) → 32 码片序列（16×32 ROM，串行输出，C0 先传）
    half_sine_fir.sv   # 8 抽头半正弦成形 FIR（12bit 输出；TX 成形 = RX 匹配滤波器，h 对称）
    pn9_whiten.sv      # PN9 白化/去白化（x^9+x^5+1，逐字节，双向复用）
    crc16_fcs.sv       # CRC-16 FCS 生成/校验（poly 0x1021, init 0x0000，双向复用）
  tx/
    oqpsk_modulator.sv # TX 组合层：符号流 → 扩频 → I/Q 分轨(半码片偏移) → 成形 → 16Msps I/Q
    tx_framer.sv       # [Phase 2] 组帧 + 白化 + FCS：字节流 → 符号流（喂 modulator）
  rx/                  # [Phase 2/3]
    rx_matched_filter.sv # 复用 half_sine_fir ×2（I/Q 两路）
    preamble_sync.sv   # [done] 16 相位扫描(块间自相关+I/Q 轴判别) → argmax 锁定
                       #        → 去交错码片流 → SFD 全并行窗相关 → frame_start
                       #        (K_BLOCKS=4, ph_thresh/sfd_thresh 参数化, 无 PLL 前馈)
                       #        [2026-09-28] 16 候选全相位覆盖 + frame_done 帧尾闭环
                       #        (连续帧可收) + STUCK_TIMEOUT 假前导兑底
    cfo_est.sv         # [done] CFO 联合估计: 8 相位候选搜对齐点 + 相位差分估频偏
                       #        (不依赖上游同步; CHIP_OFF/NSMP_P/SKIP_T3 可调)
    cordic_atan2.sv    # [done] 16 级向量模式 CORDIC (带象限预处理)
    cfo_rot.sv         # [done] 整帧消旋: 24bit 相位累加器 + 256 点 LUT + 复乘; load/phase_inc/phase_off 接口
    preamble_detect.sv # [done 2026-09-29] 短窗归一化延迟自相关前导检测器
                       #        (DEC=8 降采样, 65 样本延迟线, |acc|>γ·pwr, 连续 2 次确认)
    despreader.sv      # 16 路码片相关 + |·| 幅值检测 argmax → 4bit 符号
    rx_deframer.sv     # [done] 去白化 + FCS 校验 + PHR 解析 → 字节流（单元级 + 链级 PASS）
    rx_top.sv          # [done 2026-09-29] 可综合顶层: ADC(12bit) → MF → cfo_rot → preamble_sync
                       #        → despreader → rx_deframer; CFO 触发选择(外部/内部检测器)
    rx_chain_e2e/      # [tb] 全链端到端: ADC(12bit) → MF → sync → despread → deframe
    rx_chain.sv        # [tb] RX 链级封装（preamble_sync + despreader + rx_deframer，test_rx_chain 用）
tb/                    # 每个模块一个 cocotb 验证目录（照 tb/smoke 模板）
```

## 模块清单与黄金模型对应

| 模块 | 黄金模型对应 | 复用方 | 验证方式 |
|---|---|---|---|
| chip_lut | `symbols_to_chips` / `_CHIP_BITS` | TX 扩频、RX 相关器（内容共享） | 随机符号流逐码片比对 |
| half_sine_fir | `half_sine`（定点化后） | TX 成形、RX 匹配滤波 | 随机 ±1 脉冲流整数卷积比对 |
| pn9_whiten | `pn9_whiten`（本批新增） | TX 白化、RX 去白化 | 随机字节流比对 |
| crc16_fcs | `crc16_fcs`（本批新增） | TX 追加 FCS、RX 校验 | "123456789"→0x31C3 已知向量 + 随机比对 |
| oqpsk_modulator | `modulate_oqpsk` | TX 独有 | 随机符号 → 16Msps I/Q 全采样比对 |
| rx_matched_filter | `baseband.modulation.matched_filter` | RX 独有 | `tb/rx_matched_filter` + 链级 |
| preamble_sync | `phy_802154.preamble_sync_mirror` | RX 独有 | `tb/preamble_sync`（含噪突发帧 bit-true） |
| despreader | `baseband.spreading.despread` | RX 独有 | `tb/despreader`（16 路 argmax） |
| cfo_est | `run_cfo_fix.estimate_cfo`（定点/流式版） | RX 独有 | `tb/cfo_corr`（0~450 kHz 扫频） |
| cordic_atan2 | —（定点 atan2 原语） | cfo_est 子模块 | 经 `tb/cfo_corr` 间接验证 |
| cfo_rot | `baseband.sync.correct_cfo` | RX 独有 | `tb/cfo_corr`（消旋 bit-true） |
| preamble_detect | `run_preamble_detect`（归一化延迟自相关） | RX（当前作观测输出） | `tb/rx_chain_e2e` 顶层链（三 CFO 点 20/20） |
| rx_top | —（顶层编排） | — | `tb/rx_chain_e2e/mc_top_tb.sv` |
| rx_deframer | `rx_deframe_symbols`（去白化 → PHR 解析 → CRC 校验） | RX 独有 | 符号流注入：PSDU 字节流 / psdu_len / fcs_ok / frame_done 时序 + FCS 失败用例 |

## oqpsk_modulator 数据通路（Phase 1 交付）

```
sym[3:0] + sym_valid (2Msym/s 使能)
   │
chip_lut ──► chip 流(±1, 2Mchip/s 使能, C0 先传)          ┌── I 路: 偶数码片 → 码片窗起点注入脉冲
   │                                                      ├── Q 路: 奇数码片 → 码片窗 +4 采样注入（= 模型 16k+12）
chip 计数 0..31 ──► 奇偶分轨 ──► half_sine_fir ×2 ──► i_out[11:0], q_out[11:0] @16MHz + dv
```

与黄金模型 `modulate_oqpsk` 的采样级对应（16 MHz 时钟，码片窗 = 8 采样）：
- 偶数码片 2k：I 路脉冲注入在采样 16k（窗起点）
- 奇数码片 2k+1：Q 路脉冲注入在采样 16k+12（奇数码片窗 [16k+8,16k+16) 内偏移 4）
- 两路 FIR 系数同一组（h 对称 ⇒ 成形 = 匹配）

## 接口约定

- 时钟：单时钟域 `clk` = 16 MHz；慢速使能：`ce_2m`（码片/符号速率 tick），由 3bit 分频计数器产生。
- 数据握手：模块间用 `valid` + `ready`（字节/符号级）；采样级输出用 `dv`（data valid）。
- 定点：系数 Q2.6（8bit），FIR 输出 12bit 有符号；与 Python 定点化函数（`phy_802154.fixed_half_sine`）同法舍入，保证 bit-true。
- 复位：同步低有效 `rst_n`（`always_ff @(posedge clk)`），Phase 1 不做异步复位树。

## preamble_sync 设计要点与踩坑记录（2026-09-25 定稿）

**采样结构（实测确认）**：MF 输出中偶数码片峰在 `(k=0, ph=F)`、奇数码片峰在
`(k=0, ph=(F+4)%8)`——半码片偏移体现在 8 相位空间的 +4，**两个峰都在
`cnt[3]=0` 的半周期**。奇数码片采样必须在 k=0 拍做 -j 旋转（I'=Q, Q'=-I），
在 k=1 拍采样会采到码片间中点（幅值只有 ~1/6 且受邻片污染）。

**三个连环坑**（前两个导致首版测试始终不过）：
1. **k 位错位**：把半码片偏移当成"另一半周期"，奇片在 k=1 采样 → 流全错。
2. **模板 32 相位模糊**：前导是 CHIP[0] 周期重复，32 片块与 T0 相关只有在块
   恰对齐符号边界时才相干；扫描从 smp=0 起算，块边界与符号边界的关系随帧
   到达时刻任意 → 改用**块间延迟自相关 + I/Q 轴判别**
   `r = Σ(I_m·I_{m-1} − Q_m·Q_{m-1})`：模板无关，真相位 ≈ +32·0.9·P²，
   偶/奇轴互换候选 ≈ −32·0.9·P²（负！正门限天然排除），纯噪低 4 个数量级。
3. **SFD 滑窗模板锚定**：窗每滑 1 片，窗内所有片的模板系数都要移位——
   "进/出片同系数"的廉价滑窗只对**绝对序号锚定**有效，而 SFD 在码片流里的
   起点随锁定时刻任意（mod 64 ≠ 0）→ 必须**全并行窗锚定相关**
   `E_n = |Σ_{j=0}^{63} TS[j]·chip[n−63+j]|²`（TS ±1，只是 128 个加减）。
   `frame_start` 在判决片后一拍与首个 PHR 码片同拍输出。

**锁定策略**：不用即时锁定（±1 采样偏移候选 r 达真相位 ~87%，先锁会锁错）；
每候选跟踪历史最大 r，所有候选完成 K_BLOCKS=4 块后取 argmax 过正门限锁定。
前导 256 片 = 2048 采样，K=4 在帧内 ~600 采样处收尾，SFD 区有余量。

**标定值**（NOISE=60, SCALE=8 测试构造）：ph_thresh=2e11（前导 r≈5e11），
sfd_thresh=3e13（SFD 峰 7.2e13，前导区最大 1.3e13——注意前导块与 TS 存在
循环移位相关，不可压得太低；PHR 首符号若为 7 会与 TS 前半相干 ~1.7e13，
但出现在真 SFD 之后，靠"首过门限锁存"保证正确）。

## 刻意不做（本阶段）

- AXI-Stream/APB 封装（规格书 §6 的接口层）——Phase 2 包壳，先用 valid/ready 跑通链路。
- DC/IQ 校正 RTL、低 SNR 同步判据修复、自主触发收敛——见 `docs/08`（I-2/I-3/I-6）。
- 异步复位树、时钟门控——Phase 3 低功耗课题。

---

## 现状对照（v1.1 增补，2026-10-02）

> 本文档主体为 **v1.0（2026-09-25）**，描述的是**单通道 `rx_top` 链**。
> 此后架构演进为 **`rx_dual` 双通道链**（共享前端 + 每通道执行段），本节的"对照表"
> 用于消除文档与代码的代差；文件级状态标注已写在各模块头部。

**现行主链路（`rx_dual` 链，16 文件，tb/rx_dual_mc 与 tb/rx_dual 的 SOURCES）**：

```
ADC ─► rx_frontend（共享一份）
        ├─ rx_matched_filter（half_sine_fir ×2, Y_W=W, LSB_SHIFT 定点重定标）
        ├─ 相位恢复: 扫描（preamble_sync + preamble_detect + g_scan latch 门控）或 ext 直锁
        │   + 段确认/对齐门（ALIGN_GATE：latch 延迟到抽取网格 0/4，消扫描缺口 36→49/50）
        ├─ deinterleave（自由计数器 + phase_fix, 每 16 采样 2 抽取点）
        └─ preamble_buf（I-13: 前导快照 + 上层读口）
     ─┬─ rx_chip_backend ×2（每通道一份）
        ├─ cfo_rot（码片级消旋, 24bit pacc; 每有效片累加 phase_inc）
        ├─ sfd_detect（64 片相干能量定界）
        ├─ despreader（32 片解扩）
        └─ rx_deframer（PN9 去白化 + CRC16 FCS）
```

**文件状态标注（2026-10-02）**：

| 类别 | 文件 | 说明 |
|---|---|---|
| 现行主链路 | rx_dual / rx_frontend / rx_chip_backend / deinterleave / cfo_rot / sfd_detect / despreader / rx_deframer / rx_matched_filter / preamble_sync / preamble_detect / preamble_buf（+ common/ 四原语） | 与 tb SOURCES 一致；定点参考配置 W=16（notes §8/§11） |
| **历史/参考（勿删）** | **rx_top / rx_backend / preamble_lock / cfo_est / cordic_atan2** | 单通道旧链；被 `tb/cfo_corr` 等历史测试引用；文件头已加 `[状态]` 标注 |
| 边界说明 | `rx_top` 仍可作为"单通道最小集成"参考；`cfo_est` 的算法论证见 docs/11 | — |

**已知与 v1.0 的差异**：① 单通道 → 双通道（共享前端）；② 扫描器由 `preamble_sync` 直出改为
"latch 门控 + 对齐门"；③ 新增 preamble_buf（I-13）/轮换替换（SWAPTAB, I-10 雏形）；
④ 位宽口径：MF 满精度 21bit → 参考配置 16bit（实测无损）；⑤ CFO 估计移出主链（上层/慢环）。
