# 链路框图与 RTL 对照（v1.0）

> 更新：2026-09-25
> 定位：把「比特流 ↔ DAC/ADC 边界」的完整数字链路画成符号框图，逐块对照 RTL 文件、
> 黄金模型函数与验证状态。✅ = cocotb bit-true 回归通过（`tb/` 下各 `run.py`）。

## 0. 系统边界总览

```
 MAC 侧                   数字基带 (本项目主战场)                  模拟侧
┌───────┐   PSDU 字节  ┌───────────────────────────────┐  I/Q 12bit   ┌─────┐
│ MAC / │ ───────────► │  TX: tx_framer → oqpsk_mod   │ ───────────► │ DAC │──► 射频
│ 应用  │ ◄─────────── │  RX: rx_mf → sync → desp →   │ ◄─────────── │ ADC │◄── 前端
└───────┘   PSDU 字节  │      deframer                  │  I/Q 12bit   └─────┘
                       └───────────────────────────────┘   (16 Msps)
```

## 1. TX 链（比特流 → DAC 为止）

```
PSDU 字节流 (len + data_in + start)
   │
   ▼
┌────────────────────────────────────────────────────────┐
│ tx_framer.sv                    ✅ tb/tx_framer          │
│ 组帧(PHR=len + PSDU) → PN9 白化 → CRC-16 FCS            │
│ 黄金: phy.tx_symbols (bit-true)                         │
└──────────────────────────┬─────────────────────────────┘
                           ▼ sym[3:0] + sym_valid/ready (2 Msym/s 使能)
┌────────────────────────────────────────────────────────┐
│ oqpsk_modulator.sv              ✅ tb/oqpsk_modulator    │
│ chip_lut 扩频(4b→32 chip) → 奇偶分轨(半码片偏移)         │
│ → half_sine_fir ×2 成形                                  │
│ 黄金: phy.modulate_oqpsk_fixed (全采样 bit-true)         │
└──────────────────────────┬─────────────────────────────┘
                           ▼ i_out/q_out [11:0] + sample_dv @16 MHz
                    ═══════ DAC 边界 ═══════  (模拟域, 协作边界)
```

链级封装: `tb/tx_framer/tx_chain.sv`（tx_framer + oqpsk_modulator）✅

## 2. RX 链（ADC 采样 → 判决比特流）

```
                    ═══════ ADC 边界 ═══════  (模拟域, 协作边界)
                           ▼ i_in/q_in [11:0] + dv_in @16 Msps
                           │ (12bit 量化; 规格书假设 4bit, 待 RF 协作确认)
┌────────────────────────────────────────────────────────┐
│ rx_matched_filter.sv            ✅ tb/rx_matched_filter  │
│ half_sine_fir ×2 (X_W=12 → Y_W=21 全精度)               │
│ 黄金: phy.fixed_half_sine 整数卷积 (bit-true)            │
└──────────────────────────┬─────────────────────────────┘
                           ▼ i/q [20:0] + dv
┌────────────────────────────────────────────────────────┐
│ preamble_sync.sv #(W=21)        ✅ tb/preamble_sync      │
│ 8 相位扫描 + 块间自相关锁定 → 去交错码片流                │
│ → SFD 全并行窗相关 → frame_start (与首个 PHR 码片同拍)    │
│ 黄金: 含噪突发帧 bit-true + 功能断言                      │
└──────────────────────────┬─────────────────────────────┘
                           ▼ chip_i/q [20:0] + chip_dv + frame_start
   ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄
   cfo_corr 组 —— ✅ 已实现 (2026-09-27): cfo_est (前导联合估计) + cordic_atan2
   + cfo_rot (整帧消旋); 0~450 kHz 实测修后 BER 1e-9 量级 (`tb/cfo_corr/`)
   DC/IQ 校正 RTL 未实现 (黄金侧联合 LS 已闭环, model/rx_frontend.py)
   ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄
┌────────────────────────────────────────────────────────┐
│ despreader.sv #(W=12)           ✅ tb/despreader         │
│ 16 路码片相关 |·|² argmax → sym[3:0]                     │
│ (chip_i[18:7] 截断 = >>7; frame_start 锚定 32 片窗)      │
│ 黄金: phy.despread_chips (bit-true)                      │
└──────────────────────────┬─────────────────────────────┘
                           ▼ sym[3:0] + sym_dv
┌────────────────────────────────────────────────────────┐
│ rx_deframer.sv                  ✅ tb/rx_deframer        │
│ 去白化(pn9) + PHR 解析 + CRC-16 校验                     │
│ 黄金: phy.rx_deframe_symbols (单元级 + 链级)             │
└──────────────────────────┬─────────────────────────────┘
                           ▼ data_out + data_valid + psdu_len + fcs_ok + frame_done
                     PSDU 字节流 (MAC 侧)
```

链级封装:
- `tb/rx_deframer/rx_chain.sv` — preamble_sync → despreader → rx_deframer（MF 在激励侧）✅
- `tb/rx_chain_e2e/e2e_top.sv` — **全链含 MF + ADC 量化**: ADC(12bit) → MF → sync → despread → deframe ✅（2026-09-25 新增）

## 3. 模块 ↔ 黄金模型 ↔ 验证状态总表

| RTL 模块 | 黄金模型函数 | 验证点 (`tb/`) | 状态 |
|---|---|---|---|
| chip_lut | `symbols_to_chips` / `_CHIP_BITS` | chip_lut | ✅ 随机符号流逐码片比对 |
| half_sine_fir | `fixed_half_sine` 定点卷积 | half_sine_fir | ✅ 随机脉冲流整数卷积比对 |
| pn9_whiten | `pn9_whiten` | pn9 | ✅ 随机字节流比对 |
| crc16_fcs | `crc16_fcs` | crc16 | ✅ "123456789"→0x31C3 + 随机比对 |
| oqpsk_modulator | `modulate_oqpsk_fixed` | oqpsk_modulator | ✅ 随机符号 → 16Msps I/Q 全采样 |
| tx_framer | `tx_symbols` | tx_framer (tx_chain) | ✅ 组帧链级比对 |
| rx_matched_filter | `fixed_half_sine` 卷积 | rx_matched_filter | ✅ 12bit→21bit 全精度比对 |
| preamble_sync | （含噪突发帧构造 + 功能断言） | preamble_sync | ✅ 8 相位锁定 + SFD 定界 |
| despreader | `despread_chips` | despreader | ✅ 含旋转/噪声场景符号比对 |
| rx_deframer | `rx_deframe_symbols` | rx_deframer | ✅ 单元级 + 链级（含 FCS 失败用例） |
| （整链 RX） | `rx_deframe_symbols` 镜像 | rx_chain_e2e | ✅ ADC 量化 → PSDU 字节流 |

## 4. 当前缺口（RTL 侧，2026-09-29 更新）

1. ~~`cfo_corr.sv`~~ **已实现并验收**（2026-09-27/29）：`cfo_est` + `cordic_atan2` + `cfo_rot`；
   同步判据改共轭积 + 偶/奇 lag2 自相关轴判别（`docs/07` §4）后，**0–450 kHz 全频偏范围
   高 SNR 检出率 95–100%**（100 帧统计 99%、BER 1%）；bit-true 与全模块回归 PASS。
2. ~~顶层集成~~ **已实现**（2026-09-29）：`rtl/rx/rx_top.sv` —— ADC(12bit) → MF → cfo_rot
   → preamble_sync → despreader → rx_deframer 的可综合顶层；验证链 `tb/rx_chain_e2e/mc_top_tb.sv`
   （`run_mc.py --top-chain`）。与手工例化验证链性能一致（±2%），无集成损失。
   CFO 估计触发源仍需外部提供（`est_start`/`rot_load` 端口），真实触发机制见 `docs/08` I-6。
3. **DC / I/Q 校正**：据 `model/out/algo_study/report.md` — DC 对 O-QPSK/DSSS 判决与同步
   天然无害（码片表零均值），**数字基带可不做**；I/Q 失衡可修（盲矩估计）但新链实测收益有限
   （见 `docs/08` 讨论）。优先级降低。
4. **RTL BER 蒙特卡洛**：平台 ✅（`run_mc.py` 三链：基础/CFO/顶层集成，各自独立 csrc）。
   当前瓶颈（顶层集成 50 帧/点实测）：**低 SNR 同步判决**（+10 dB 74%、+5 dB 72%、0 dB 8%）；
   高 SNR 下 ~2% 随机误锁。前者是链路固有限制（基础链同样降），详见 `docs/08` I-2/I-3。
5. **ADC 位宽**：RTL 按 12bit 实现；规格书假设 4bit（待 RF 协作确认后再定）。

## 5. 可运行性问答（2026-09-25 实测）

- **TX 到 DAC 为止**：✅ 可跑。`tb/tx_framer/tx_chain.sv` 整链 bit-true，输出
  12bit I/Q @16 Msps + sample_dv，即 DAC 输入数字波形。
- **ADC 数据 → 接收机 → bit 码流**：✅ 可跑。`tb/rx_chain_e2e/`：TX 波形 ×8 + AWGN
  → clamp 12bit（模拟 ADC）→ MF → preamble_sync → despreader → rx_deframer →
  PSDU 字节流还原、fcs_ok=1（`python tb/rx_chain_e2e/run.py`）。
  前提：无 CFO/DC/IQ 损伤（缺口 1/2 未做）。
