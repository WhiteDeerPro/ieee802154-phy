# 文献与开源调研：802.15.4 接收链的 CFO 处理

日期: 2026-09-29 · 网络可达: DuckDuckGo/Bing/arXiv/GitHub/TI/PyPI/OpenAlex/Unpaywall 通, Google & StackOverflow & Semantic Scholar(429) 不通
方法: 搜索引擎反爬严重（Bing 忽略查询、DDG 返回空页），实际有效路径是 **GitHub raw + OpenAlex API**。
以下每条都来自实际抓取/读取，无转述性推测。

---

## 1. 开源参考实现: `bastibl/gr-ieee802-15-4`（309★，GNU Radio ZigBee 收发器）

**最重要的一条：整个 `lib/` 里没有 CFO 估计模块，也没有载波相位恢复模块。**

它靠三件事绕开 CFO：

1. **`lib/preamble_tagger_cc_impl.cc` —— 符号间差分相位判据**
   ```cpp
   for (int i = 1; i < 2 * d_len_preamble - 1; i++) {
       if (std::arg(in[i] / in[i - 1]) < M_PI / 4) ctr++;
       else ctr = 1;
       if (ctr >= d_len_preamble &&
           std::abs(std::arg(in[i + 1] / in[i])) > M_PI / 4)   // SFD 处相位跳变
           add_item_tag(0, ..., "SOF", ...);
   }
   ```
   判据是**相邻符号的相位差**（差分），不是与已知前导的相干积分。前导 8 个符号相同 ⇒ 相位差只
   累积**一个符号周期**（32 码片 = 16 µs）的 CFO 旋转，而不是整个前导（128 µs）的。
   注意 `std::arg(...) < M_PI/4` **没有取绝对值** —— 对负角度恒真，判据是不对称的（可靠性来自
   "连续 8 个符号都满足"这一条件，而非单次比较）。

2. **`lib/packet_sink.cc` —— 硬判决码片 + 汉明距离**
   ```cpp
   if (gr::blocks::count_bits32(
           (d_shift_reg & 0x7FFFFFFE) ^ (CHIP_MAPPING[0] & 0x7FFFFFFE)) < d_threshold)
       d_preamble_cnt += 1;                 // 找到前导符号 0
   ```
   输入是**硬判决后的码片比特流**，用 32 位异或 + popcount 与 `CHIP_MAPPING[0]` 比汉明距离；
   之后每 32 码片比一次，再依次匹配 `CHIP_MAPPING[7]`、`CHIP_MAPPING[10]`（SFD = 0x7A）
   进入 `STATE_HAVE_SYNC`。**全程只用比特，不用幅相 ⇒ 对 CFO 完全免疫**，代价是无乘法也无线性度。

3. **`digital_clock_recovery_mm_xx`（Mueller & Müller）+ `dqpsk_soft_demapper`（差分解调）**
   （见 `examples/ieee802_15_4_OQPSK_PHY.grc` 的 block 清单）

**架构哲学：在差分/硬判决域做同步与解调，从而不需要估计 CFO。** 差分检测相对相干有约 3 dB 损失，
换来的是没有载波同步环路、没有冷启动死锁。

## 2. 学术侧（OpenAlex 检索，摘要原文引用）

### 2.1 [2008] A low power ZigBee baseband processor（`10.1109/socdc.2008.4815569`，非 OA）

> "To estimate and compensate carrier phase error at baseband, the receiver allows **full digital solution for
> carrier phase synchronization**. An existing **packet detection algorithm for spread spectrum communication
> system is used to estimate large carrier frequency offset**. This paper also presents a new
> **decision-feedback algorithm for residue phase error tracking**."

三段式：**包检测/大 CFO 粗估 → 解调 → 判决反馈残余相位跟踪**。实测 TSMC 0.18 µm、78 k 门、
1.633 mm²、接收模式 1.7 mW，PER = 0.01 @ SNR < 5 dB。
**注意"包检测算法同时用于估大 CFO"** —— 检测与粗估频偏由同一个模块承担，正是我们讨论的"触发源"问题。

### 2.2 [2020] Symbol-by-Symbol Detection for IEEE 802.15.4 O-QPSK Receivers（`10.1109/access.2020.3020183`，OA）

> "the **residual carrier frequency offset (CFO) of the chip sample is estimated and compensated with the aid
> of the preamble**; then, the standard **noncoherent detection** scheme with perfectly known CFO is directly
> configured. … only **4 preamble symbols is sufficient** for accurate CFO estimation. Compared with the
> conventional noncoherent detector, the average running time per data packet of our enhanced detector is only
> **0.17 times** of the former."

两点可直接对照我们：**(a)** 前导长度 4 个符号（我们用了 8 个 = 256 码片，余量充足）；
**(b)** 估计出来只是"残余 CFO"，粗偏由别处处理 —— 单靠前导能纠的范围有限。

### 2.3 [2016] Reconfigurable dual mode 802.15.4 baseband receiver（`10.1109/wf-iot.2016.7845488`）

> "The standard specifies O-QPSK PHY with half-sine pulse shaping which can be either categorized under the
> class of M-ary PSK signals (QPSK signal with offset) or as **Minimum Shift Keying (MSK)**. M-ary PSK
> demodulation requires **perfect carrier synchronization** … MSK signals … can be demodulated
> **non-coherently but error performance is not as good**."

给出 O-QPSK 的"双面性"：按 PSK 处理要载波同步，按 MSK（CPFSK）处理可非相干。双模接收机按 SNR 切换。

### 2.4 [2023] Understanding Concurrent Transmissions: CFO and RF Interference（`10.1145/3604430`，OA）

> "the impact of errors induced by **relative carrier frequency offsets** on the performance of CT **highly
> depends on the choice of the underlying physical layer**."

并发传输（CT/Flooding）场景下相对 CFO 的影响随 PHY 选择而剧烈变化；说明 CFO 在 802.15.4 里不是可以
一概忽略的量，其重要性取决于具体的用例与 PHY 配置。

---

## 3. 对照我们的实现

| 环节 | 文献/开源做法 | 我们现状 | 判断 |
|---|---|---|---|
| 解扩 | 非相干（`abs(corr)`）或差分 | `despreader` 非相干软值 | ✅ 已一致 |
| CFO 估计 | 前导辅助（2008/2020） | `cfo_est` 前导联合估计（对齐点 + 频偏） | ✅ 同类，且我们连对齐点一起估 |
| **包/前导检测** | **硬判决汉明距离**（gr-ieee802-15-4）/ **差分相位**（preamble_tagger）/ 扩频包检测算法（2008） | `preamble_sync` **相干积分 + 门限** | ❌ **这是死锁根源** |
| 残余相位跟踪 | 判决反馈环（2008） | 无 | ⚠️ 缺失 |
| 时钟恢复 | M&M 定时环（gr-ieee802-15-4） | 由 `preamble_sync` 的同步过程隐含完成 | ⚠️ 未单独建模 |

**结论**：我们的数据段（非相干解扩）本来就与文献一致地"免疫 CFO"；问题精确地集中在
**前导检测那一步用了相干积分**。实测数据（`model/out/cfo_trigger/report.md`）显示：
未消旋条件下 100 kHz 时窗内 DET 仅 1/50 —— 与"相干积分在前导 2048 采样上转 12.8 圈"完全吻合。

文献给出的三条修法对应我们此前的候选方案：

- **方案 C（改 `preamble_sync` 判据为非相干）** ⇒ 对应 gr-ieee802-15-4 的 `packet_sink`（硬判决汉明距离）
  与 `preamble_tagger`（差分相位）。**有直接先例，且实现是 popcount / 角度比较级别，无乘法。**
- **方案 A（用 `preamble_detect` 触发）** ⇒ 其"延迟自相关"同样是差分思想；`preamble_detect.sv` 已实现，
  模型侧测过"CFO 免疫 + Pd = 1.00"。需解决 460±10 抖动 > 对齐窗 7 的问题。
- **补一个判决反馈残余相位跟踪** ⇒ 2008 论文的第三段，目前我们完全没有。数据段非相干解扩不强制需要它，
  但若要往相干解扩/更长包走则迟早要有。

## 4. 未解决 / 未验证

- 2008、2020 两篇都**没有拿到全文**（IEEE 直链被拦、Unpaywall 无 OA 镜像），结论仅基于摘要原文；
  其中"包检测算法"与"4 个前导符号"的具体算法细节未知。
- gr-ieee802-15-4 的 `packet_sink` 工作在**硬判决码片域**，其上游的定时/判决环节（图里是 M&M +
  dqpsk soft demapper）对 CFO 的敏感度未量化 —— 不能据此断言"整链 CFO 免疫"。
- 未检索到专门讨论"**前导检测在未消旋下的冷启动**"的文献；我们这一轮的实测（检出率随 CFO 塌陷、
  且非单调）暂无外部对标。
