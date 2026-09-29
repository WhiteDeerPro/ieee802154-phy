# -*- coding: utf-8 -*-
"""
baseband.modulation —— 成形脉冲与 O-QPSK 调制 (制式无关)

「波形域」调制层: 把码片/符号 (±1) 经成形脉冲映射为复基带波形, 及接收端
匹配滤波。与「码域」的 coding / spreading 解耦——本层不关心码片从哪来,
只关心给定成形脉冲怎么生成/匹配波形。

  half_sine(sps)              半正弦成形脉冲 (O-QPSK 常用)
  oqpsk_modulate(chips, pulse) O-QPSK 调制器: 偶片→I, 奇片→Q (延迟半片)
  matched_filter(x, pulse)    匹配滤波
"""

import numpy as np


def half_sine(sps=8):
    """半正弦成形脉冲 h[n] = sin(pi·(n+0.5)/sps), 0 <= n < sps; Σh² = sps/2。"""
    n = np.arange(sps)
    return np.sin(np.pi * (n + 0.5) / sps)


def oqpsk_modulate(chips, pulse):
    """O-QPSK 调制: 码片(±1) -> 复基带波形。

    偶数码片 -> I (实部), 奇数码片 -> Q (虚部) 且延迟半个码片 (O-QPSK 偏移);
    每码片以 pulse 成形。输出长度 = len(chips)·sps + sps//2 (sps = len(pulse))。
    """
    chips = np.asarray(chips, dtype=float)
    n = len(chips)
    sps = len(pulse)
    k_even = n // 2                 # 偶数码片个数 (ceil), 奇数 = n - k_even
    i_up = np.zeros(n * sps)
    i_up[np.arange(k_even) * 2 * sps] = chips[0::2]
    q_up = np.zeros(n * sps + sps // 2)
    q_up[np.arange(n - k_even) * 2 * sps + sps + sps // 2] = chips[1::2]
    sI = np.convolve(i_up, pulse)[:n * sps]
    sQ = np.convolve(q_up, pulse)[:n * sps + sps // 2]
    out = np.zeros(n * sps + sps // 2, dtype=complex)
    out.real[:len(sI)] = sI
    out.imag[:len(sQ)] = sQ
    return out


def matched_filter(x, pulse):
    """与给定成形脉冲匹配滤波。

    匹配滤波器 = 脉冲的**时间反转共轭**。脉冲对称时 ``h[::-1] == h``, 与直接卷积
    等价 (本仓库的制式链均属此类, 故行为不变); 但**非对称脉冲** (如整数周期正弦)
    必须反转 —— 否则相关峰落到负值上, 符号会被整体取反。
    """
    return np.convolve(x, np.conj(np.asarray(pulse))[::-1])


# ---------------------------------------------------------------------------
# 通用调制 (PSK / ASK / QAM) —— 供单载波制式与眼图分析使用
# ---------------------------------------------------------------------------
# 上面的 O-QPSK 是 802.15.4 的制式调制; 本段提供与制式无关的常规星座/成形,
# 用于对比不同调制方式的波形、眼图与抗噪性能 (基带码 → 波形 这一段)。

def raised_cosine(beta=0.35, sps=8, span=6):
    """升余弦 (RC) 成形脉冲: h(t) = sinc(t)·cos(πβt)/(1−(2βt)²)。

    长度 span·sps+1 (奇数), 峰值在中心, 单位能量归一 (Σh² = 1) —— 便于不同
    调制/不同 sps 之间的眼图幅度直接比较。β = 滚降系数 (0 = 理想低通)。
    奇点 t = ±1/(2β) 处取极限 (π/4)·sinc(1/(2β))。
    """
    t = np.arange(-span * sps, span * sps + 1) / sps
    with np.errstate(divide="ignore", invalid="ignore"):
        h = np.sinc(t) * np.cos(np.pi * beta * t) / (1 - (2 * beta * t) ** 2)
    if beta > 0:
        sing = np.isclose(np.abs(2 * beta * t), 1.0)
        h[sing] = (np.pi / 4) * np.sinc(1 / (2 * beta))
    return h / np.sqrt(np.sum(h ** 2))


def sine_burst(cycles=1, sps_per_cycle=10, half=False):
    """整数周期正弦脉冲 —— 「乘法器输出」式成形。

    一个符号内恰好包含 ``cycles`` 个完整正弦周期, 每周期 ``sps_per_cycle`` 个采样点
    (即采样率 ≥ 10×脉冲基频 —— 波形上能看清正弦而非折线)。脉冲在符号边界处为 0,
    相邻符号拼接时相位连续。

    与升余弦/半正弦这类**跨符号重叠**的成形不同, 本脉冲完全落在符号内, 因此没有
    符号间干扰的尾巴 —— 代价是频谱滚降慢 (矩形窗的 sinc 旁瓣)。

    half=True 时取半个周期 (与 802.15.4 的半正弦同族), 此时符号内含半个周期。
    返回长度 = cycles·sps_per_cycle (half 时为一半), 峰值归一。
    """
    if half:
        n = max(cycles * sps_per_cycle // 2, 1)
        h = np.sin(np.pi * (np.arange(n) + 0.5) / n)
    else:
        n = cycles * sps_per_cycle
        h = np.sin(2 * np.pi * cycles * np.arange(n) / n)
    return h / np.max(np.abs(h))


def psk_constellation(order):
    """M-PSK 星座 (平均功率归一 = 1)。order=2 → BPSK (±1), 4 → QPSK (±1±1j)。"""
    if order < 2:
        raise ValueError("PSK order 至少为 2")
    k = np.arange(order)
    offset = 0.0 if order == 2 else np.pi / order      # BPSK 落在实轴
    c = np.exp(1j * (2 * np.pi * k / order + offset))
    return c / np.sqrt(np.mean(np.abs(c) ** 2))


def ask_constellation(order):
    """M-ASK 星座 (等间隔实数电平 ±1,±3,… , 平均功率归一 = 1)。"""
    if order < 2 or order % 2:
        raise ValueError("ASK order 应为不小于 2 的偶数")
    a = 2.0 * (np.arange(order) - (order - 1) / 2)
    return (a / np.sqrt(np.mean(a ** 2))).astype(complex)


def qam_constellation(order):
    """M-QAM 方形星座 (order 为完全平方数, 平均功率归一 = 1)。"""
    side = int(round(np.sqrt(order)))
    if side * side != order:
        raise ValueError("QAM order 应为完全平方数")
    a = 2.0 * (np.arange(side) - (side - 1) / 2)
    I, Q = np.meshgrid(a, a)
    c = (I + 1j * Q).ravel()
    return c / np.sqrt(np.mean(np.abs(c) ** 2))


def constellation(scheme, order):
    """按名称取星座: scheme ∈ {'psk','ask','qam'}。"""
    try:
        return {"psk": psk_constellation, "ask": ask_constellation,
                "qam": qam_constellation}[scheme](order)
    except KeyError:
        raise ValueError(f"未知星座类型 {scheme!r} (可选 psk/ask/qam)") from None


def map_symbols(idx, const):
    """整数索引 → 星座点 (const 为 constellation() 的返回)。"""
    return np.asarray(const, dtype=complex)[np.asarray(idx, dtype=int)]


def pulse_shape(symbols, pulse, sps=8):
    """上采样 + 脉冲成形。返回 ``(波形, 群延迟)``。

    群延迟 = (len(pulse)−1)//2 采样 —— 眼图/星座图对齐时需从波形起点减去它,
    否则能看到一个恒定的水平平移 (眼图会被“切开”)。
    """
    sym = np.asarray(symbols, dtype=complex).reshape(-1)
    up = np.zeros(len(sym) * sps, dtype=complex)
    up[::sps] = sym
    return np.convolve(up, pulse), (len(pulse) - 1) // 2


# ---------------------------------------------------------------------------
# 正交上/下变频 —— 复基带 ↔ 单路实信号
# ---------------------------------------------------------------------------
# 为什么平时用不到: ``x = I + jQ`` 是**复包络**, 一个数学对象、不是物理量。以它为
# 载体做完所有处理, 等价于「正交上变频 → 模拟域处理 → 正交下变频」整条链 —— 因为
# 正交性是线性的。这就是“等效基带模型”, 代价是**看不见下变频引入的误差**
# (LO 相位/频率不准 -> I/Q 混串, 即镜像分量)。
#
# 需要看那个误差时, 就用下面两个函数把这一步显式地走一遍。

def upconvert(x, f_lo, fs=16e6):
    """复基带 → **单路实信号**(正交上变频): ``s(t) = Re{ x(t)·e^{j2πf_lo t} }``。

    f_lo 为载波/本振频率。展开就是 ``I·cos(ωt) − Q·sin(ωt)`` —— 信道里真正流动
    的唯一一个电压波形。注意它的带宽是基带的**两倍**(双边带)。
    """
    x = np.asarray(x, dtype=complex).ravel()
    n = np.arange(len(x))
    return np.real(x * np.exp(2j * np.pi * f_lo * n / fs))


def downconvert(s, f_lo, fs=16e6, lpf=None):
    """**单路实信号** → 复基带(正交下变频 + 低通)。

    ``z = LPF{ s(t)·e^{−j2πf_lo t} }`` —— 混频后同时包含基带项与 2f_lo 项,
    ``lpf`` 就是用来滤掉后者的。不传 lpf 时用 FFT 自适应低通(截止 fs/4),
    对带限信号足够。

    f_lo 与发端不一致(或有相位偏差)时, I/Q 会按偏差角度**互相混串**,
    这正是镜像分量的来源 —— 参见 ``impairments.add_iq_imbalance``。
    """
    s = np.asarray(s, dtype=float).ravel()
    n = np.arange(len(s))
    z = s * np.exp(-2j * np.pi * f_lo * n / fs)
    if lpf is not None:
        return np.convolve(z, np.asarray(lpf).ravel(), mode="same")
    # FFT 低通 (截止 fs/4): 保留基带分量, 滤掉 2f_lo 混频项。
    # 用**升余弦过渡带**而不是硬截断 —— 理想砖墙的时域响应是 sinc, 会在信号突变
    # 处振铃, 实测无噪声残余误差就有 5%; 平滑过渡能把它压下来。
    nfft = 1 << (len(z) - 1).bit_length()
    Z = np.fft.fft(z, nfft)
    f = np.abs(np.fft.fftfreq(nfft, 1 / fs))
    fc, beta = fs / 4, fs / 8                      # 截止与过渡带宽度
    Z[f > fc + beta / 2] = 0
    taper = (f > fc - beta / 2) & (f <= fc + beta / 2)
    Z[taper] *= 0.5 * (1 + np.cos(np.pi * (f[taper] - (fc - beta / 2)) / beta))
    return np.fft.ifft(Z, nfft)[:len(z)]
