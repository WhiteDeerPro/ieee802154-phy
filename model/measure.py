# -*- coding: utf-8 -*-
"""
measure —— 测量层: 对波形/码流做参数表征 (制式无关)

从「信号处理链」里把「参数提取」独立出来: 纯函数、返回数据 (numpy 数组/标量),
不画图。可复用、可测试, 也可直接喂给 RTL 比对或指标导出。

方法参照 (数字通信教学常规 + CommPy utilities):
  power / BER / SER / EVM / SNR(估计) / 频谱 / 眼图 / 相关 / 星座软值
"""

import numpy as np


def signal_power(x):
    """信号平均功率 = mean(|x|²)。"""
    return float(np.mean(np.abs(np.asarray(x)) ** 2))


def ber(syms_rx, syms_tx, bits_per_sym=4):
    """符号流 BER (按 bits_per_sym 折算比特; 符号为整数 0..2^b-1)。"""
    x = np.asarray(syms_rx, dtype=int) ^ np.asarray(syms_tx, dtype=int)
    n_err = int(sum(bin(int(v)).count("1") for v in x))
    return n_err / (len(x) * bits_per_sym)


def ser(syms_rx, syms_tx):
    """符号错误率 SER = mean(sym_rx != sym_tx)。"""
    return float(np.mean(np.asarray(syms_rx) != np.asarray(syms_tx)))


def evm(soft, ref, normalize=True):
    """误差矢量幅度 EVM = sqrt( mean|s - s_ref|² / mean|s_ref|² )。

    soft 为接收星座软值 (复数), ref 为参考符号 (复数, 尺度需与 soft 一致)。
    """
    s = np.asarray(soft, dtype=complex)
    r = np.asarray(ref, dtype=complex)
    denom = np.mean(np.abs(r) ** 2) if normalize else 1.0
    return float(np.sqrt(np.mean(np.abs(s - r) ** 2) / denom))


def snr_from_evm(evm_val):
    """EVM → SNR (dB): SNR ≈ -20·log10(EVM) (高 SNR 近似)。"""
    return float(-20 * np.log10(max(float(evm_val), 1e-12)))


def spectrum(x, fs=1.0, n_fft=None):
    """幅度谱: 返回 (freqs, mag)。fs 为采样率, n_fft 默认 len(x)。"""
    x = np.asarray(x)
    n_fft = n_fft or len(x)
    spec = np.fft.fft(x, n_fft)
    freqs = np.fft.fftfreq(n_fft, 1 / fs)
    return freqs, np.abs(spec)


def eye_data(y, sps, n_traces=None):
    """眼图数据: 采样流按每符号 sps 点折叠成 (n_traces, sps) 矩阵 (每行一条迹线)。"""
    y = np.asarray(y)
    n_traces = n_traces or (len(y) // sps)
    return y[:n_traces * sps].reshape(n_traces, sps)


def eyes_metrics(soft, ref):
    """判决点统计: 返回 (眼高, 判决点 SNR dB)。

    soft/ref 为同长的接收软值与理想值。眼高取**相邻电平的最小间距**
    (最内层眼是打开得最小的那个, 决定实际余量)。
    """
    soft = np.asarray(soft).ravel()
    ref = np.asarray(ref).ravel()[:len(soft)]
    lv = np.unique(np.round(ref, 6))
    gaps = np.diff(np.unique(np.r_[lv, -lv]))
    err = soft - ref
    height = float(gaps.min()) if gaps.size else float("nan")
    snr = float(10 * np.log10(np.mean(np.abs(ref) ** 2) /
                              max(np.mean(np.abs(err) ** 2), 1e-30)))
    return height, snr


def occupied_bandwidth(x, fs=1.0, frac=0.99, n_fft=None):
    """占用带宽: 包含``frac``比例总功率的最小频宽 (工程惯例取 99%, ITU-R 口径)。

    返回 (带宽, f_lo, f_hi)。它是「带内/带外」的客观判据 —— 比人为指定带宽可靠。
    """
    v = np.asarray(x).ravel()
    n_fft = n_fft or len(v)
    X = np.abs(np.fft.fftshift(np.fft.fft(v, n_fft))) ** 2
    f = np.fft.fftshift(np.fft.fftfreq(n_fft, 1 / fs))
    c = np.cumsum(X)
    if c[-1] <= 0:
        return 0.0, 0.0, 0.0
    c /= c[-1]
    lo, hi = (1 - frac) / 2, 1 - (1 - frac) / 2
    f_lo = float(f[min(np.searchsorted(c, lo), len(f) - 1)])
    f_hi = float(f[min(np.searchsorted(c, hi), len(f) - 1)])
    return f_hi - f_lo, f_lo, f_hi


def spectrum_envelope(freqs, mag, smooth_bins=9):
    """频谱包络: 对幅度谱做滑动平均, 返回 (包络, 包络均值)。

    “包络均值”即对频率取平均后的**平均幅度密度** —— 它把谱的形状抹平成一个数,
    适合快速比较不同实验的整体电平; 但要看滚降/旁瓣形状则必须看谱本身。
    想比较能量而非幅度时用 ``energy`` 口径 (|X|²/fs 的平均)。
    """
    m = np.asarray(mag).ravel()
    k = max(int(smooth_bins), 1)
    env = np.convolve(m, np.ones(k) / k, mode="same")
    return env, float(np.mean(env))


def xcorr(a, b):
    """互相关 r[k] = Σ a[n+k]·conj(b[n]), FFT 实现 (完整非归一化)。"""
    a = np.asarray(a, dtype=complex)
    b = np.asarray(b, dtype=complex)
    n = 1 << (len(a) + len(b) - 1).bit_length()
    A = np.fft.fft(a, n)
    B = np.fft.fft(b, n)
    return np.fft.ifft(A * np.conj(B))[:len(a) + len(b) - 1]


def corr_peak(sig, tmpl):
    """模板相关峰值与位置: 返回 (k, peak)。"""
    c = np.abs(xcorr(sig, tmpl))
    k = int(np.argmax(c))
    return k, float(c[k])


def soft_values_from_chips(chips, chip_table, ref_syms, chip_gain=None):
    """码片软值 -> 符号星座软值, 归一化到 ~±1。

    码片软值的采样 (O-QPSK 奇偶规则) 属制式层 (见 phy_802154.sample_chips);
    本函数只做「码片软值 → 星座点」的通用相关表征, 制式无关。
    chip_gain 为码片软值幅度 (MF 峰值增益, 半正弦 Σh²=4); 默认自动估计。
    """
    mat = np.asarray(chips, dtype=complex).reshape(-1, chip_table.shape[1])
    template = chip_table[np.asarray(ref_syms, dtype=int)]
    s = np.sum(mat * template, axis=1)
    if chip_gain is None:
        chip_gain = float(np.mean(np.abs(mat.real))) + 1e-12  # O-QPSK 星座在实轴
    return s / (chip_table.shape[1] * chip_gain)


def corr_power(chips, chip_table):
    """码片软值 -> 各 PN 路相关功率: 返回 (n_sym, n_pn) 的 |相关|² 矩阵。

    解扩软输出 (非相干): 与码片表每路复相关取功率, 供 argmax 判决 / 余量分析。
    """
    mat = np.asarray(chips, dtype=complex).reshape(-1, chip_table.shape[1])
    return np.abs(mat @ chip_table.T) ** 2


def ber_bits(rx_bits, tx_bits):
    """比特级 BER: 逐比特比对 (长度取短者)。返回 (ber, n_err, n_bits)。"""
    a = np.asarray(rx_bits).reshape(-1).astype(np.uint8)
    b = np.asarray(tx_bits).reshape(-1).astype(np.uint8)
    n = min(len(a), len(b))
    if n == 0:
        return 0.0, 0, 0
    n_err = int(np.count_nonzero(a[:n] ^ b[:n]))
    return n_err / n, n_err, n


def snr_m2m4(x):
    """盲 SNR 估计 (dB): M2M4 矩估计, 假设 MPSK 星座 + 高斯噪声。

    M2 = E|y|², M4 = E|y|⁴ ⇒ SNR = √(2M2²−M4) / (M2 − √(2M2²−M4))。
    无需已知参考序列, 可直接对「信道波形」做质量表征 (代价: 星座越接近
    恒模越准, O-QPSK/PSK 适用, 高阶 QAM 会低估)。

    **务必用在符号采样点上**, 不要直接丢过采样的成形波形进来 —— 成形脉冲使其
    非恒模, 高阶矩随之失真 (实测: 同一 16 dB 信号, 采样点估 14.8 dB, 波形估 6.1 dB)。
    """
    y = np.asarray(x, dtype=complex).reshape(-1)
    m2 = float(np.mean(np.abs(y) ** 2))
    m4 = float(np.mean(np.abs(y) ** 4))
    disc = 2 * m2 * m2 - m4
    if disc <= 0:
        return float("inf")
    root = float(np.sqrt(disc))
    denom = m2 - root
    if denom <= 0:
        return float("inf")
    return float(10 * np.log10(root / denom))


def iq_image_rejection(alpha, beta):
    """镜像抑制比 IRR (dB) = 20·log10(|α|/|β|), 解析式。

    α / β 取自 IQ 不平衡模型 r = α·s + β·conj(s) + d; 实测路径为
    ``baseband.frontend.estimate_dc_iq(r_chips, s_chips)`` -> 本函数。
    β = 0 (理想 IQ) 返回 +inf。
    """
    b = abs(beta)
    if b < 1e-15:
        return float("inf")
    return float(20 * np.log10(abs(alpha) / b))


# ---------------------------------------------------------------------------
# 链路级表征: 一次汇总整链
# ---------------------------------------------------------------------------

def link_report(sig, bits_per_sym=4) -> dict:
    """一次汇总整链的表征 (消费 ``baseband.link.Signal``; 鸭子类型, 不依赖 link)。

    只报告链上确实产生过的量 —— 缺失的观测点不会出现在结果里, 因此同一份
    代码可用于「只跑 TX」「理想信道」「全校正」等不同深度的实验。
    返回 dict; 键名约定: 比率类无后缀 (ber/ser/evm), dB 类以 _db 结尾。
    """
    out = {}
    # 载荷级 —— 最强判据 (含 CRC 校验的制式可直接看 payload_ok)
    if sig.payload is not None and sig.rx_payload is not None:
        out["payload_ok"] = bool(sig.rx_payload == sig.payload)
    # 符号级
    if sig.symbols is not None and sig.rx_symbols is not None:
        n = min(len(sig.symbols), len(sig.rx_symbols))
        out["ser"] = ser(sig.rx_symbols[:n], sig.symbols[:n])
        out["ber"] = ber(sig.rx_symbols[:n], sig.symbols[:n], bits_per_sym)
    # 比特级
    if sig.bits is not None and sig.rx_bits is not None:
        b, n_err, n_bits = ber_bits(sig.rx_bits, sig.bits)
        out.update(ber_bits=b, bit_errors=n_err, n_bits=n_bits)
    # 星座级 (EVM 需要判决前的软值 + 同尺度参考, 由制式在接收段填入)
    if sig.soft_ref is not None and sig.rx_soft is not None:
        e = evm(sig.rx_soft, sig.soft_ref)
        out["evm"] = e
        out["evm_db"] = float(20 * np.log10(max(e, 1e-12)))
        out["snr_from_evm_db"] = snr_from_evm(e)
    # 伴随量 (同步位置 / 估计量 / 信道 / 校验)
    for k in ("sync", "cfo_est", "fcs_ok", "phr_len", "channel", "dc_iq"):
        if k in sig.meta:
            out[k] = sig.meta[k]
    if "probes" in sig.meta:
        out["probes"] = sorted(sig.meta["probes"])
    return out


def frame_bit_errors(sig, bits_per_sym=4, sync_fail_as_error=True):
    """一帧的比特误码统计 (消费 ``baseband.link.Signal``)。

    返回 ``(n_bit_err, n_bit)``。同步失败 (帧尾越界) 时整帧按
    ``bits_per_sym·n_sym`` 计错 —— 与既有 run 脚本的保守统计一致:
    同步误判意味着整帧不可用, 不应把侥幸对上的符号算作正确。

    这是实验层的统一统计口径, 使新链路与迁移前的数值可直接对照。
    """
    n_sym = len(sig.symbols)
    n_bit = bits_per_sym * n_sym
    if sync_fail_as_error and sig.meta.get("sync_fail"):
        return n_bit, n_bit
    x = (np.asarray(sig.rx_symbols, dtype=np.int64)
         ^ np.asarray(sig.symbols, dtype=np.int64))
    return int(sum(bin(int(v)).count("1") for v in x)), n_bit


def format_report(report: dict, title: str = None) -> str:
    """把 ``link_report`` 的结果格式化为可读多行文本 (纯函数, 不打印)。"""
    lines = [title] if title else []
    for k, v in report.items():
        if isinstance(v, float):
            lines.append(f"  {k:<16} {v:.4g}")
        elif isinstance(v, (list, tuple, np.ndarray)) and not isinstance(v, str):
            arr = np.asarray(v)
            lines.append(f"  {k:<16} array{arr.shape}" if arr.size > 4
                         else f"  {k:<16} {list(np.asarray(v).ravel())}")
        else:
            lines.append(f"  {k:<16} {v}")
    return "\n".join(lines)
