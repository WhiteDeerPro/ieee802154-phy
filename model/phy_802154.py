# -*- coding: utf-8 -*-
"""
phy_802154.py —— IEEE 802.15.4 2.4 GHz PHY 浮点黄金模型 (golden model)
====================================================================

用途:
  1. Phase 0 算法验证 (BER 曲线, 扩频增益)
  2. 后续 RTL 的 bit-true 比对基准 (定点化时在此库上逐模块替换)

已实现 (v0.1):
  - 4 bit 符号 <-> 32 码片 DSSS 扩频 (IEEE 802.15.4 Table 24 / Table 73)
  - O-QPSK 半正弦成形调制/非相干相关解调 (码片级, 无需载波相位恢复)
  - PPDU 组帧 (Preamble + SFD + PHR + PSDU)
  - 前导码模板相关同步 (粗定时, 容忍噪声; CFO 未建模 — 见 TODO)
  - AWGN 信道, 按"匹配滤波后码片 SNR"标定
  - PN9 白化 / CRC-16 FCS (v0.2, 供 RTL bit-true; 白化位置与 PN9 方向 待标准原文最终确认)

v0.2 (RTL Phase 1):
  - 定点化半正弦系数 (Q2.6), 与 rtl/common/half_sine_fir.sv 同法舍入
  - pn9_whiten / crc16_fcs —— 与 RTL 逐位镜像; crc16 经 "123456789"→0x31C3
    已知向量验证 (AUG-CCITT, poly 0x1021 init 0x0000); pn9 约定见函数 docstring「待确认」

数据出处: 码片序列表以 IEEE 802.15.4 标准原文转录 (经 Virginia Tech 学位论文
Table 2.3 与 math.stackexchange 引用交叉核对; TI CC2420/CC2520 数据手册
一致; 符号 14 以标准原文为准, 数据手册 OCR 有歧义)。

TODO:
  - CFO / 采样定时偏差 / 多径损伤注入 (见规格书 §7) — 部分已在 impairments.py
  - PN9 白化范围 (PHR+PSDU, 不含 SHR) 与序列方向待标准原文最终确认
"""

import numpy as np

from baseband import modulation, channels, coding, spreading
from baseband.modulation import half_sine
from baseband.sync import correct_cfo
from baseband.frontend import estimate_dc_iq, apply_dc_iq_correction
from baseband.equalization import estimate_channel_ls, equalize_mmse

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 符号 -> 32 码片序列 (C0 最先传输), IEEE 802.15.4-2003 Table 24 / -2006 Table 73
_CHIP_BITS = [
    "11011001110000110101001000101110",  #  0
    "11101101100111000011010100100010",  #  1
    "00101110110110011100001101010010",  #  2
    "00100010111011011001110000110101",  #  3
    "01010010001011101101100111000011",  #  4
    "00110101001000101110110110011100",  #  5
    "11000011010100100010111011011001",  #  6
    "10011100001101010010001011101101",  #  7
    "10001100100101100000011101111011",  #  8
    "10111000110010010110000001110111",  #  9
    "01111011100011001001011000000111",  # 10
    "01110111101110001100100101100000",  # 11
    "00000111011110111000110010010110",  # 12
    "01100000011101111011100011001001",  # 13
    "10010110000001110111101110001100",  # 14
    "11001001011000000111011110111000",  # 15
]
CHIP = np.array([[1.0 if c == "1" else -1.0 for c in s] for s in _CHIP_BITS])  # (16, 32)

SFD_BYTE = 0xA7          # SFD, 低半字节(7)先传
PREAMBLE_SYMS = 8        # 32 bit 全 0 前导 = 8 个符号 0
CHIP_RATE = 2e6          # Hz
SPS = 8                  # 每码片采样数 (16 MHz / 2 MHz)
SPREADING_GAIN_DB = 10 * np.log10(32 / 4)  # = 9.03 dB, SF=8


# ---------------------------------------------------------------------------
# 比特/符号/码片 变换
# ---------------------------------------------------------------------------

def bytes_to_symbols(data: bytes) -> np.ndarray:
    """每字节 -> 2 个符号, 低半字节在前 (标准 LSB-first)"""
    a = np.frombuffer(data, dtype=np.uint8)
    return np.stack([a & 0x0F, a >> 4], axis=1).ravel()


def symbols_to_bytes(symbols) -> bytes:
    s = np.asarray(symbols, dtype=np.uint8).reshape(-1, 2)
    return bytes(((s[:, 1] << 4) | s[:, 0]).tolist())


def symbols_to_chips(symbols) -> np.ndarray:
    """符号 -> 32 码片 DSSS 扩频; 码片表映射委托 baseband.spreading"""
    return spreading.spread(symbols, CHIP)  # (32*len,)


def despread_chips(chips: np.ndarray) -> np.ndarray:
    """复数码片软值 -> 符号; 与 16 个 PN 序列做复相关, 取 |·| 最大 (非相干幅值检测)

    输入为复数: 偶数码片取 MF 采样值, 奇数码片已乘 -j 归位 (见 rx_despread)。
    |CHIP @ soft| 的 ±1 反相假设由幅值检测天然涵盖。委托 baseband.spreading。
    """
    return spreading.despread(chips, CHIP, coherent=False)


# ---------------------------------------------------------------------------
# PPDU 组帧 (不含白化/CRC, 见 TODO)
# ---------------------------------------------------------------------------

def build_ppdu(psdu: bytes) -> bytes:
    assert len(psdu) <= 127
    return b"\x00" * 4 + bytes([SFD_BYTE, len(psdu)]) + psdu


def ppdu_symbols(psdu: bytes) -> np.ndarray:
    return bytes_to_symbols(build_ppdu(psdu))


# ---------------------------------------------------------------------------
# O-QPSK 调制 / 解调 (半正弦成形)
# ---------------------------------------------------------------------------

def modulate_oqpsk(chips: np.ndarray, sps: int = SPS) -> np.ndarray:
    """码片(±1) -> 复基带: 偶片->I, 奇片->Q 延迟半片 (O-QPSK); 委托 baseband.modulation"""
    return modulation.oqpsk_modulate(chips, half_sine(sps))


def matched_filter(r: np.ndarray, sps: int = SPS) -> np.ndarray:
    """与半正弦脉冲匹配 (h 对称, 卷积即匹配滤波); 通用实现见 baseband.modulation"""
    return modulation.matched_filter(r, half_sine(sps))


def chip_peak_index(template_align: int, m: int, sps: int = SPS) -> int:
    """前导模板对齐点 template_align + 码片 m 在匹配滤波输出中的峰值采样序号"""
    return template_align + m * sps + (sps // 2 if m % 2 else 0) + (sps - 1)


# ---------- 模块级缓存 ----------
_PREAMBLE_TMPL_CACHE = {}

def _get_preamble_template(pilot_syms, sps):
    """构建 (并缓存) 前导匹配模板, 只依赖 (pilot_syms, sps)"""
    key = (pilot_syms, sps)
    if key in _PREAMBLE_TMPL_CACHE:
        return _PREAMBLE_TMPL_CACHE[key]

    h = half_sine(sps)
    g = np.convolve(h, h)
    pilot_chips = np.tile(CHIP[0], pilot_syms)
    m_idx = np.arange(len(pilot_chips))
    off = m_idx * sps + np.where(m_idx % 2, sps // 2, 0)
    L = int(off[-1] + len(g) + 1)
    tmpl = np.zeros(L, dtype=complex)
    np.add.at(tmpl, off[:, None] + np.arange(len(g))[None, :],
              pilot_chips[:, None] * g[None, :])

    # 关键: 缓存 NFFT 与模板频谱
    nfft_hint = 1 << 16                       # 足够大, 覆盖典型帧长
    tmpl_spec = np.conj(np.fft.fft(tmpl, nfft_hint))
    _PREAMBLE_TMPL_CACHE[key] = (tmpl, L, nfft_hint, tmpl_spec)
    return _PREAMBLE_TMPL_CACHE[key]


def correlate_preamble(mf, pilot_syms=PREAMBLE_SYMS, sps=SPS, return_corr=False):
    """前导码模板相关 (FFT), 模板频谱已缓存"""
    tmpl, L, nfft_hint, tmpl_spec = _get_preamble_template(pilot_syms, sps)

    if len(mf) < L:
        return (0, 0.0, np.zeros(0)) if return_corr else (0, 0.0)

    nfft = 1 << (len(mf) + L - 1).bit_length()
    if nfft <= nfft_hint:
        # 信号与模板必须用同一 NFFT 做 FFT, 不能截断缓存频谱 (否则时域混叠, 相关峰错位)
        spec = np.fft.fft(mf, nfft_hint) * tmpl_spec
    else:
        spec = np.fft.fft(mf, nfft) * np.conj(np.fft.fft(tmpl, nfft))

    full = np.fft.ifft(spec)
    c = np.abs(full[:len(mf) - L + 1])
    k = int(np.argmax(c))
    if return_corr:
        return k, float(c[k]), c
    return k, float(c[k])


def rx_despread(mf: np.ndarray, template_align: int, n_symbols: int, sps: int = SPS) -> np.ndarray:
    """按定时采样码片并解扩为符号 (矢量化, 复数域)

    偶数码片取 MF 采样值 (I 路), 奇数码片乘 -j 将 Q 路归位到实轴,
    使软值 = chip·exp(jθ) —— CFO 表现为符号内公共相位旋转, 由 |·| 检测容忍
    """
    n_chips = n_symbols * 32
    m = np.arange(n_chips)
    idx = template_align + m * sps + np.where(m % 2, sps // 2, 0) + (sps - 1)
    v = mf[idx].astype(complex)
    chips = np.where(m % 2 == 0, v, v * (-1j))
    return despread_chips(chips)


# ---------------------------------------------------------------------------
# 信道: 复 AWGN, 按匹配滤波后码片 SNR 标定
# ---------------------------------------------------------------------------

def add_awgn(x: np.ndarray, chip_snr_db: float, sps: int = SPS,
             rng: np.random.Generator = None) -> np.ndarray:
    """加复高斯白噪声, 使匹配滤波器输出端码片 SNR = chip_snr_db; 委托 baseband.channels"""
    es_h = float(np.sum(half_sine(sps) ** 2))        # = sps/2
    return channels.awgn(x, chip_snr_db, es_h, rng)


# ---------------------------------------------------------------------------
# 定点化 (与 RTL bit-true 比对用)
# ---------------------------------------------------------------------------

def fixed_half_sine(sps: int = SPS, q: int = 6) -> np.ndarray:
    """半正弦系数的 Q2.q 定点 (四舍五入), 与 rtl/common/half_sine_fir.sv 一致

    sps=8, q=6 → [12,36,53,63,63,53,36,12]
    """
    return np.rint(half_sine(sps) * (1 << q)).astype(int)


def modulate_oqpsk_fixed(symbols, sps: int = SPS) -> tuple:
    """定点版调制: 返回 (i_seq, q_seq) 整数序列, 与 oqpsk_modulator.sv 采样级一致

    i_seq 长度 n*8, q_seq 长度 n*8 + sps//2; 脉冲注入位置与浮点版 modulate_oqpsk 相同
    """
    chips = symbols_to_chips(np.asarray(symbols, dtype=int))
    n = len(chips)
    h = fixed_half_sine(sps)
    i_up = np.zeros(n * sps, dtype=int)
    i_up[np.arange(n // 2) * 2 * sps] = chips[0::2].astype(int)
    q_up = np.zeros(n * sps + sps // 2, dtype=int)
    q_up[np.arange(n - n // 2) * 2 * sps + sps + sps // 2] = chips[1::2].astype(int)
    sI = np.convolve(i_up, h)[:n * sps]
    sQ = np.convolve(q_up, h)[:n * sps + sps // 2]
    return sI.astype(int).tolist(), sQ.astype(int).tolist()


# ---------------------------------------------------------------------------
# 白化 (PN9) 与 FCS (CRC-16) —— v0.2, 与 RTL 逐位镜像
# ---------------------------------------------------------------------------

def pn9_bytes(data: bytes, seed: int = 0x1FF) -> bytes:
    """PN9 白化, 多项式 x^9+x^5+1, Fibonacci 左移, 逐字节 LSB-first

    ⚠ 约定「待标准原文确认」: 白化范围(PHR+PSDU)、seed、位序均按业界通行
    描述实现 (CC2530 风格); 与 rtl/common/pn9_whiten.sv 逐位一致。
    委托 baseband.coding.lfsr_scramble。
    """
    return coding.lfsr_scramble(data, 9, (8, 4), seed, lsb_first=True)


def crc16_fcs(data: bytes, init: int = 0x0000) -> int:
    """CRC-16 FCS, poly 0x1021, init 0x0000, 无输出异或, 每字节 MSB-first

    已知向量: crc16_fcs(b"123456789") == 0x31C3 (CRC-16/AUG-CCITT)。
    与 rtl/common/crc16_fcs.sv 逐位一致; 委托 baseband.coding.crc。
    """
    return coding.crc(data, 0x1021, 16, init, msb_first=True)


def tx_frame_bytes(psdu: bytes, whiten: bool = True) -> bytes:
    """完整 TX PPDU 字节流: SHR(4×0x00 + SFD) + 白化(PHR + PSDU + FCS)

    ⚠ 约定「待标准原文确认」: 白化范围 = PHR+PSDU+FCS (SHR 不白化);
    FCS 低字节先传。fcs = crc16_fcs(psdu)。
    """
    assert len(psdu) <= 127
    fcs = crc16_fcs(psdu).to_bytes(2, "little")
    tail = pn9_bytes(bytes([len(psdu)]) + psdu + fcs) if whiten else (bytes([len(psdu)]) + psdu + fcs)
    return b"\x00" * 4 + bytes([SFD_BYTE]) + tail


def tx_symbols(psdu: bytes) -> np.ndarray:
    """TX 符号流 (低半字节先), 喂 oqpsk_modulator 的黄金输入"""
    return bytes_to_symbols(tx_frame_bytes(psdu))


def rx_deframe_symbols(symbols) -> tuple[bytes, bool, int]:
    """RX 解扩符号流 (自 PHR 符号起, 低半字节先) → (psdu, fcs_ok, phr_len)

    与 rtl/rx/rx_deframer.sv 功能镜像: 去白化 (PN9 自逆) → PHR 长度 →
    PSDU 字节流 + FCS 校验 (crc16_fcs(psdu) == FCS 低字节先)。
    长度越界或字节不足时 fcs_ok=False。
    """
    tail = pn9_bytes(symbols_to_bytes(np.asarray(symbols, dtype=np.uint8).reshape(-1)))
    L = tail[0] if len(tail) >= 1 else 0xFF
    if L > 127 or len(tail) < 3 + L:
        return b"", False, L
    psdu = tail[1:1 + L]
    fcs = int.from_bytes(tail[1 + L:3 + L], "little")
    return psdu, (crc16_fcs(psdu) == fcs), L


def preamble_sync_mirror(i_seq, q_seq, ph_thresh, sfd_thresh, k_blocks=4,
                         frame_done_at=None, sfd_wait=None, frame_drain=None,
                         norm_num=11, norm_shift=5, mag_mode=False):
    """preamble_sync.sv 的黄金镜像: 逐采样复刻同一整数算法 (去交错双相位结构)。

    采样结构 (实测): 每 16 采样有 2 个码片峰 (I 路偶片 + Q 路奇片), 峰间距 12/4
    交替; 偶片峰位置 pe 与奇片峰位置 po=(pe+12)%16 中恰有一个落在任一 8 拍半周期。
    **帧到达相位任意 ⇒ pe 可为 0..15**, 故候选覆盖全部 16 个采样位置:
    候选 c = 偶片峰位置假设; 服务 = 位置 c 采偶片 (I,Q 原值),
            位置 (c+12)%16 采奇片 (-j 旋转: I'=Q, Q'=-I)。
    等价地, 位置 q 的采样同时服务偶链候选 c=q 与奇链候选 c=(q+4)%16;
    每候选每 16 采样收 2 片 → 块 = 32 片 = 256 采样。
    (旧版只服务 smp[3]=0 的 8 拍, 隐含 pe∈[0,8), 实测只覆盖 6/16 相位。)

    扫描统计量 (模板无关, 避开 32 相位符号边界模糊): 前导 = CHIP[0] 周期重复,
    块相关对 T0 只有在块对齐符号边界时才相干, 否则掉到自相关底 (~0)。
    改用块间延迟自相关 + I/Q 轴判别:
        r = Σ_k ( I_m[k]·I_{m-1}[k] − Q_m[k]·Q_{m-1}[k] )
    前导块 m 与 m-1 逐片相同 → r = Σ(I²−Q²) ≈ +32·0.9·P² (真相位,
    码片能量集中在实轴); "偶/奇轴互换"候选 (如 F=7 时的相位3) 得 −32·0.9·P²;
    纯噪 |r| ~ √32·σ², 低 4 个数量级。锁定取 r 最大且过正门限的候选。
    锁定后按同一去交错规则输出码片流; SFD (CHIP[7]++CHIP[10]) 64 码片滑窗
    互相关, sfd_E(窗 [n-64..n-1]) 过 sfd_thresh 时 frame_start_idx = n。

    连续帧支持 (与 RTL 同步): frame_done_at = 收到帧尾事件的采样索引集合,
    在该采样上退出锁定态、清扫描器状态 (bcnt/bestR/…) 重新扫描下一帧。
    两阶段保持超时 (镜像 RTL 的 SFD_WAIT / FRAME_DRAIN):
      sfd_wait   锁定后未报 frame_start 时的超时 (采样数) —— 误锁时快速退锁;
      frame_drain 已报 frame_start 后的兑底超时 (采样数) —— 防解帧阶段卡死。
    无上述参数时行为与旧版逐位一致 (单帧, 锁定后不再退出)。

    锁定判据为**双判据 AND** (镜像 RTL):
      绝对门限 bestR ≥ ph_thresh  (高 SNR 主导, 保持原行为)
      归一化  r ≥ (norm_num/2^norm_shift)·E_prev 且连续 2 块合格 (低 SNR 主导,
              消除绝对门限散布 ∝ σ² 导致的噪声误锁; 详见 docs/07 §3.5)

    返回 dict: detect (最终电平), locked_phase (最后一次锁定相位),
    chips (锁后码片流 [(i,q)...]), frame_start_idx (第一个 frame_start,
    未检出为 None), 以及连续帧场景的 lock_events [(smp, p)] /
    frame_start_idxs (全部 frame_start)。
    """
    n = len(i_seq)
    TS = np.concatenate([CHIP[7], CHIP[10]]).astype(int)   # SFD 模板 64
    accR = [0] * 16                   # 块间自相关累加 (有符号)
    accI = [0] * 16                   # 块间自相关虚部累加 (mag_mode 用)
    mcnt = [0] * 16
    bestR = [0] * 16                  # 各候选历史最大 r (有符号)
    bcnt = [0] * 16
    accE = [0] * 16                   # 当前块能量累加 Σ(I²+Q²)
    prevE = [0] * 16                  # 上一块能量 (归一化分母)
    pending = [False] * 16            # 上一块归一化合格
    ready = [False] * 16              # 连续 2 块合格 → 锁定资格
    axc = [0] * 16                    # 轴判别: Σ(偶·conj(偶_{-2}) − 奇·conj(奇_{-2}))
    axis_ok = [False] * 16            # 上一块末: 正确候选>0, 轴互换<0
    ecnt = [0] * 16                   # 偶服务计数 (每 16 次评估一次)
    eh1_i = [0] * 16                  # 上次偶服务值
    eh1_q = [0] * 16
    eh2_i = [0] * 16                  # 上上次偶服务值
    eh2_q = [0] * 16
    oh1_i = [0] * 16                  # 上次奇服务值
    oh1_q = [0] * 16
    oh2_i = [0] * 16                  # 上上次奇服务值
    oh2_q = [0] * 16
    prevI = [[0] * 32 for _ in range(16)]   # 上一块码片 (去交错后)
    prevQ = [[0] * 32 for _ in range(16)]
    curI = [[0] * 32 for _ in range(16)]    # 当前块码片
    curQ = [[0] * 32 for _ in range(16)]
    fd_set = set(frame_done_at or ())
    smp = 0
    state_scan = True
    chips, fs_idx, detect = [], None, False
    fs_idxs = []
    lock_events = []
    fs_reported = False
    stuck = 0

    win = []                                 # SFD 全窗 (去交错后码片, 左端零填充)
    sfd_n = 0
    lphase = 0

    while smp < n:
        a, b = int(i_seq[smp]), int(q_seq[smp])
        locked_now = False

        # 帧尾闭环 / 两阶段保持超时: 退出锁定态回扫描 (镜像 RTL 的 ST_LOCK 退出路径)
        if not state_scan:
            if not fs_reported:
                expired = (sfd_wait is not None) and (stuck >= sfd_wait - 1)
            else:
                expired = (frame_drain is not None) and (stuck >= frame_drain - 1)
            stuck += 1
            if smp in fd_set or expired:
                state_scan = True
                detect = False
                accR = [0] * 16
                accI = [0] * 16
                bestR = [0] * 16
                mcnt = [0] * 16
                bcnt = [0] * 16
                accE = [0] * 16
                prevE = [0] * 16
                pending = [False] * 16
                ready = [False] * 16
                axc = [0] * 16
                axis_ok = [False] * 16
                ecnt = [0] * 16
                eh1_i = [0] * 16
                eh1_q = [0] * 16
                eh2_i = [0] * 16
                eh2_q = [0] * 16
                oh1_i = [0] * 16
                oh1_q = [0] * 16
                oh2_i = [0] * 16
                oh2_q = [0] * 16
                win = []
                sfd_n = 0
                fs_reported = False
        else:
            stuck = 0

        q = smp & 15                             # 采样位置 (16 相位)
        if state_scan:                           # 每拍服务两候选: 偶链 q / 奇链 (q+4)%16
            # 先判定后服务: RTL 用寄存器值(本采样更新前)组合判定, 镜像同拍对齐
            if min(bcnt) >= k_blocks:            # 所有候选都完成 K 块后才允许锁定
                cands = [p for p in range(16) if ready[p] and axis_ok[p]]   # 双判据+轴判别
                if cands:
                    pmax = max(cands, key=lambda p: bestR[p])
                    if bestR[pmax] >= ph_thresh:
                        state_scan = False
                        lphase = pmax
                        detect = True
                        locked_now = True            # 锁定采样本身不发射 (与 RTL 时序一致)
                        lock_events.append((smp, pmax))
                        win = []
                        sfd_n = 0
                        fs_reported = False
                        stuck = 0
            if state_scan:                       # 未锁定则正常服务两候选
                for cand, vi, vq in ((q, a, b), ((q + 4) % 16, b, -a)):
                    k = mcnt[cand]
                    is_even = (cand == q)
                    curI[cand][k] = vi           # 存当前块 (块末成为 prev)
                    curQ[cand][k] = vq
                    accR[cand] += vi * prevI[cand][k] + vq * prevQ[cand][k]   # 共轭积 Re(v·conj(v'))
                    accI[cand] += vq * prevI[cand][k] - vi * prevQ[cand][k]   # 共轭积 Im(v·conj(v'))
                    accE[cand] += vi * vi + vq * vq
                    if is_even:
                        # 轴判别 (相位无关): 偶侧 lag=2 自相关 (正确 +4 / 轴互换 -4)
                        axc[cand] += vi * eh2_i[cand] + vq * eh2_q[cand]
                        eh2_i[cand] = eh1_i[cand]; eh2_q[cand] = eh1_q[cand]
                        eh1_i[cand] = vi;          eh1_q[cand] = vq
                        ecnt[cand] += 1
                        if ecnt[cand] == 16:
                            axis_ok[cand] = axc[cand] > 0
                            axc[cand] = 0
                            ecnt[cand] = 0
                    else:
                        # 奇侧同法但取负 (正确 -4 → 相减后同向累加, 信号翻倍)
                        axc[cand] -= vi * oh2_i[cand] + vq * oh2_q[cand]
                        oh2_i[cand] = oh1_i[cand]; oh2_q[cand] = oh1_q[cand]
                        oh1_i[cand] = vi;          oh1_q[cand] = vq
                    if k == 31:
                        # mag_mode: 用复相关的**模** (L1) 代替实部 —— 实部带 cos(ω·256) 因子,
                        # CFO=100 kHz 时该因子为负 ⇒ 永不过正门限 (检测死锁)。模对 CFO 免疫。
                        r_blk = (abs(accR[cand]) + abs(accI[cand])) if mag_mode else accR[cand]
                        if r_blk > bestR[cand]:
                            bestR[cand] = r_blk
                        # 归一化判据: r ≥ γ·E_prev (定点乘法形式), 连续 2 块才置 ready
                        ratio_ok = (prevE[cand] > 0 and
                                    r_blk * (1 << norm_shift) >= norm_num * prevE[cand])
                        prevE[cand] = accE[cand]
                        accE[cand] = 0
                        ready[cand] = ready[cand] or (ratio_ok and pending[cand])
                        pending[cand] = ratio_ok
                        accR[cand] = 0
                        accI[cand] = 0
                        prevI[cand] = curI[cand][:]  # 本块成为下一块的参考
                        prevQ[cand] = curQ[cand][:]
                        mcnt[cand] = 0
                        bcnt[cand] += 1
                    else:
                        mcnt[cand] += 1
        if (not state_scan) and (not locked_now):
            emit_even = (q == lphase)             # 偶片峰位置 = lphase
            emit_odd = (((q + 4) % 16) == lphase)  # 奇片峰位置 = (lphase+12)%16
            if emit_even or emit_odd:
                d_i, d_q = (a, b) if emit_even else (b, -a)
                chips.append((d_i, d_q))
                # SFD 窗锚定全窗相关: 窗 [n-63..n] (片序号 <0 零填充), 模板 TS[0..63]
                win.append((d_i, d_q))
                if len(win) > 64:
                    win.pop(0)
                pad = 64 - len(win)
                aI = sum(int(TS[j]) * win[j - pad][0] for j in range(pad, 64))
                aQ = sum(int(TS[j]) * win[j - pad][1] for j in range(pad, 64))
                E_now = aI * aI + aQ * aQ
                if sfd_n >= 63 and not fs_reported and E_now >= sfd_thresh:
                    fs_reported = True
                    if fs_idx is None:
                        fs_idx = sfd_n + 1        # frame_start 与下一片 (首个 PHR 片) 同拍
                    fs_idxs.append(sfd_n + 1)
                    stuck = 0                     # 阶段切换: drain 计时从 SFD 起算
                sfd_n += 1
        smp += 1
    return {"detect": detect, "locked_phase": lphase,
            "chips": chips, "frame_start_idx": fs_idx,
            "lock_events": lock_events, "frame_start_idxs": fs_idxs}


# ---------------------------------------------------------------------------
# CFO 估计与前端校正 (基于 802.15.4 前导; 消旋/DC-IQ 通用算法在 baseband)
# ---------------------------------------------------------------------------

FS = 16e6                        # 复基带采样率 (Hz)
SYM_DUR = 32 / CHIP_RATE         # 符号周期 = 16 µs


def estimate_cfo_periodogram(y, fs=FS, sps=SPS, n_fft=1 << 14):
    """去调制 + FFT 谱峰 CFO 粗估 (鲁棒, 无模糊, 范围 ±fs/2)。

    前导已知 → z[n] = y[n]·conj(前导波形[n]) 去除调制, 得到纯复正弦
    exp(j2πΔf n/fs); FFT 找峰值 bin 得 Δf。相比单延迟差分 + 解模糊复核,
    不会出现「选错模糊支路」的离谱错估 (如 -199 kHz)。

    精度 = fs/n_fft (n_fft=2^14 → ≈977 Hz), 足够粗纠到 estimate_cfo_fine 工作。
    ref 侧约定: 前导位于 y 开头 (帧起点=0)。
    """
    N_pre = PREAMBLE_SYMS * 32 * sps          # 前导采样数 (2048)
    if len(y) < N_pre:
        return 0.0
    ref = modulate_oqpsk(symbols_to_chips(np.zeros(PREAMBLE_SYMS, dtype=int)))
    z = y[:N_pre] * np.conj(ref[:N_pre])       # 去调制 → 纯正弦
    spec = np.fft.fft(z, n_fft)
    freqs = np.fft.fftfreq(n_fft, 1 / fs)
    return float(freqs[np.argmax(np.abs(spec))])


def estimate_cfo_coarse(mf, search_hz=(-300e3, 300e3), step_hz=10e3, fs=FS, sps=SPS):
    """网格粗搜 CFO (频域搬移版): 一次 FFT + 每候选一次 IFFT, 循环移位近似误差 << 步长

    ⚠ 已知脆弱性 (2026-09-25 实测): 真值不落在网格上时 (如 5 kHz), 真值假设的
    相关峰被 sinc 衰减 (128µs 模板窗), 长帧数据区的巧合相关可压过它 → 错估
    (实测 5 kHz → 错估 -120 kHz)。大频偏网格对齐场景 (50 kHz) 仍可靠。
    建议: 新代码用 estimate_cfo_preamble_diff (块间差分, 无此脆弱性)。
    """
    tmpl, L, nfft_hint, tmpl_spec = _get_preamble_template(PREAMBLE_SYMS, sps)
    if len(mf) < L:
        return 0.0

    need = len(mf) + L - 1
    if need <= nfft_hint:
        nfft = nfft_hint
        tspec = tmpl_spec
    else:
        nfft = 1 << need.bit_length()
        tspec = np.conj(np.fft.fft(tmpl, nfft))

    F = np.fft.fft(mf, nfft)                        # 一次 FFT (所有候选复用)
    n_valid = len(mf) - L + 1

    grid = np.arange(search_hz[0], search_hz[1] + step_hz, step_hz)
    shifts = np.round(grid * nfft / fs).astype(int)  # 频域整数 bin 循环移位

    best_f, best_peak = 0.0, -1.0
    for f, sh in zip(grid, shifts):
        F_shift = np.roll(F, -int(sh))              # 时域乘 exp(-j2πf n/fs) ≡ 频域搬移
        corr = np.abs(np.fft.ifft(F_shift * tspec)[:n_valid])
        pk = float(corr.max())
        if pk > best_peak:
            best_peak, best_f = pk, float(f)
    return best_f


def estimate_cfo_preamble_diff(y, fs=FS):
    """前导块间延迟自相关频偏估计 (与 RTL preamble_sync 同构, 模板无关)

    前导 = chip0 周期重复 -> 发送波形周期 256 采样 (32 片). 波形域直接相关:
        r = Σ y[n+256]·conj(y[n]),  angle(r) = 2πΔf·256/fs
    无模糊范围 ±31.25 kHz < 先验界 ±220 kHz (标准 ±40ppm/端 最坏 ±192 kHz) ->
    解模糊: 候选 f+k·62.5k 逐个消旋后做码片域短延迟差分复核 ——
    正确候选消旋后残余≈0 → 16片(8µs)差分相位≈0; 错误候选残余=±62.5k → 相位≈±π
    (半圈) → score = |r|·cos(φ) 判别力强, 免模板相关峰比较 (低 SNR 下脆弱)。
    ref 侧约定: 前导位于 y 开头 (帧起点=0); RTL 侧块间自相关在 preamble_sync
    8 相位扫描中完成, 无需此约定。
    """
    D = 32 * SPS                        # 256 采样 = 32 片
    N_pre = PREAMBLE_SYMS * 32 * SPS
    seg = y[:N_pre]
    r = np.sum(seg[D:] * np.conj(seg[:N_pre - D]))
    f_base = float(np.angle(r)) / (2 * np.pi * D / fs)
    # 复核只在局部: 前导区截取 + 小网格时域相关 (免全帧 FFT/卷积)
    tmpl, L, _, _ = _get_preamble_template(PREAMBLE_SYMS, SPS)
    h = half_sine(SPS)
    j_idx = np.arange(L)[None, :] + np.arange(16)[:, None]   # 定位网格 0..15 (p 物理 0..7)
    n_pre = np.arange(N_pre + 24)
    best_f, best_score = f_base, 1e18
    for k in range(-4, 5):
        f = f_base + k * (fs / D)
        if abs(f) > 220e3:
            continue
        yc = y[:N_pre + 24] * np.exp(-2j * np.pi * f * n_pre / fs)
        mfc = np.convolve(yc, h)                     # 前导区 MF
        corr_local = np.abs(mfc[j_idx] @ np.conj(tmpl))
        p = int(np.argmax(corr_local))
        v = extract_preamble_chips(mfc, p, derail=True)
        z = v * known_preamble_chips(derail=True)   # 去调制 → 纯正弦
        r4 = np.sum(z[4:] * np.conj(z[:-4]))        # 4 片 = 2 µs, 无模糊 ±250 kHz
        r16 = np.sum(z[16:] * np.conj(z[:-16]))     # 16 片 = 8 µs, 无模糊 ±62.5 kHz
        # 双延迟相位误差 + 相关峰幅度门控 (错误候选的模板相关峰被残留 CFO
        # 衰减 ~25×, 即使相位 err 碰巧小也被幅度因子打压)
        corr_peak = float(corr_local.max())
        err = (abs(np.angle(r4)) + abs(np.angle(r16))) / (corr_peak + 1e-6)
        if err < best_score:
            best_score, best_f = err, f
    return best_f


def estimate_cfo_fine(y, f_coarse, fs=FS, sps=SPS):
    """精估: 粗纠后逐符号复相关, 相邻峰相位差平均

    返回 (f_fine, 方差指示)。要求粗纠后残差足够小 (相关相位可读)。
    """
    n = np.arange(len(y))
    yc = y * np.exp(-2j * np.pi * f_coarse * n / fs)
    mf = matched_filter(yc)
    t = CHIP[0] * np.where(np.arange(32) % 2 == 0, 1.0, 1j)   # 符号0复模板
    # 逐符号: 码片采样 -> 复相关 (粗纠后假设 p=0 可由相关峰定位)
    p, _, corr = correlate_preamble(mf, return_corr=True)
    m = np.arange(32)
    idx0 = m * sps + np.where(m % 2, sps // 2, 0) + (sps - 1)
    cs = []
    for k in range(PREAMBLE_SYMS):
        v = mf[p + idx0 + k * 32 * sps]
        soft = np.where(m % 2 == 0, v, v * (-1j))
        cs.append(t @ soft)
    cs = np.array(cs)
    dphi = np.angle(cs[1:] * np.conj(cs[:-1]))          # 相邻符号相位差
    # 解卷绕: 相邻相位差应在 ±π 内 (粗搜步进 10kHz -> 粗纠后残差 <5kHz,
    # 16us 内相位差 < 0.5 rad, 无卷绕风险)
    f_res = float(np.mean(dphi)) / (2 * np.pi * SYM_DUR)
    return f_coarse + f_res, float(np.std(dphi) / (2 * np.pi * SYM_DUR))


def extract_preamble_chips(mf, p, pilot_syms=PREAMBLE_SYMS, sps=SPS,
                           derail=False):
    """取前导码期码片软值 (原始复数; derail=True 时奇数码片乘 -j 归位)"""
    m = np.arange(pilot_syms * 32)
    idx = p + m * sps + np.where(m % 2, sps // 2, 0) + (sps - 1)
    v = mf[idx].astype(complex)
    if derail:
        return np.where(m % 2 == 0, v, v * (-1j))
    return v


def known_preamble_chips(pilot_syms=PREAMBLE_SYMS, derail=True):
    """前导码复数码片模板: 偶数码片 c, 奇数码片 j·c; derail 后全为实数 ±c"""
    c = np.tile(CHIP[0], pilot_syms)
    if not derail:
        m = np.arange(len(c))
        return np.where(m % 2 == 0, c, 1j * c)
    return c


def correct_frame_chips(mf, p, n_symbols, alpha, beta, d, sps=SPS):
    """整帧处理: 取原始复数码片 -> DC/IQ 逆变换 -> 奇数码片 -j 归位 -> 解扩"""
    n_chips = n_symbols * 32
    m = np.arange(n_chips)
    idx = p + m * sps + np.where(m % 2, sps // 2, 0) + (sps - 1)
    v = mf[idx].astype(complex)
    w = apply_dc_iq_correction(v, alpha, beta, d)
    chips = np.where(m % 2 == 0, w, w * (-1j))
    return despread_chips(chips)


# ---------------------------------------------------------------------------
# 码片软值采样与相干解扩 (多径均衡配套, 802.15.4 采样规则)
# ---------------------------------------------------------------------------

def sample_chips(mf, align, n_chips, sps=SPS, derail=True):
    """按 rx_despread 的码片峰值采样规则取码片软值 (奇数码片乘 -j 归位)。

    越界部分补零 (帧尾对齐点偏移时仍可采样)。
    derail=False 保留原始复数码片 (DC/IQ 校正需在归位前作用于原值)。
    """
    m = np.arange(n_chips)
    idx = align + m * sps + np.where(m % 2, sps // 2, 0) + (sps - 1)
    if idx.max() >= len(mf):
        mf = np.concatenate([mf, np.zeros(int(idx.max() - len(mf) + 1), dtype=mf.dtype)])
    v = mf[idx].astype(complex)
    if not derail:
        return v
    return np.where(m % 2 == 0, v, v * (-1j))


def preamble_chips(n_syms=PREAMBLE_SYMS):
    """前导已知码片: n_syms 个符号 0 -> CHIP[0] 重复 n_syms 次。"""
    return np.tile(CHIP[0], n_syms)


def despread_coherent(chips):
    """相干解扩: 取 32 码片复相关实部最大 (均衡补偿信道相位后适用)。"""
    return spreading.despread(chips, CHIP, coherent=True)


# ---------------------------------------------------------------------------
# 自测试
# ---------------------------------------------------------------------------

def _selftest():
    rng = np.random.default_rng(1)
    # 1) 表性质: 16 序列近似正交
    g = CHIP @ CHIP.T
    off_diag = np.abs(g - np.diag(np.diag(g)))
    assert np.all(np.diag(g) == 32.0), "自相关应为 32"
    assert off_diag.max() <= 12, f"准正交性恶化: max |互相关| = {off_diag.max()}"
    # 2) 无噪声回环: 随机帧 BER = 0
    for _ in range(20):
        psdu = bytes(rng.integers(0, 256, size=rng.integers(1, 30)).tolist())
        syms = ppdu_symbols(psdu)
        tx = modulate_oqpsk(symbols_to_chips(syms))
        p, _ = correlate_preamble(matched_filter(tx))
        rx = rx_despread(matched_filter(tx), p, len(syms))
        assert np.array_equal(rx, syms), "无噪声回环解调失败"
    # 3) 噪声下同步保持
    psdu = bytes(rng.integers(0, 256, size=20).tolist())
    syms = ppdu_symbols(psdu)
    tx = modulate_oqpsk(symbols_to_chips(syms))
    for snr in (0.0, 4.0):
        y = add_awgn(tx, snr, rng=rng)
        p, peak = correlate_preamble(matched_filter(y))
        assert p == 0, f"SNR={snr}dB sync offset: {p}"
    print("phy_802154 selftest: OK  (orthogonality max|cross-corr| = %.0f, loopback BER = 0)" % off_diag.max())


if __name__ == "__main__":
    _selftest()
