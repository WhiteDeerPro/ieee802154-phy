# -*- coding: utf-8 -*-
"""
baseband.filters —— FIR 滤波器设计（窗函数法）与频响分析
==========================================================

为什么需要窗函数:
  理想低通滤波器的冲激响应是 sinc —— **无限长**且非因果。要得到可实现的 FIR,
  只能截断到 N 点, 这在时域上等价于乘一个**矩形窗**。矩形窗的频谱是 sinc 形,
  与理想响应卷积后就产生**吉布斯现象**: 通带/阻带出现约 −13 dB 的波纹、过渡带
  之外还有高旁瓣。

  换用**平滑的窗**(Hann/Hamming/Blackman/Kaiser) 就是让截断边缘平缓过去 ——
  旁瓣显著压低、波纹变小, 代价是**过渡带变宽**。这就是窗函数法的全部权衡:

      窗          主瓣宽(过渡带)   最高旁瓣      典型用途
      rect        最窄 (0.9/N)     −13 dB       几乎不用 (波纹太大)
      Hann        3.1/N            −31 dB       通用, 旁瓣衰减快
      Hamming     3.3/N            −41 dB       旁瓣最低的固定窗, 通信常用
      Blackman    5.5/N            −57 dB       需要极低旁瓣时
      Kaiser      可调 (β)         可调         用 β 在过渡带与旁瓣间连续取舍

  注: 本项目**成形脉冲** (升余弦 / 半正弦 / 整数周期正弦) 是直接给定的解析脉冲,
  不经窗函数设计; 窗函数法在这里服务于**数字中频的低通/抽取滤波** (docs/02 提到
  的 CIC/FIR 那一段)。
"""

import numpy as np

# 各窗的经典指标 (N 抽头, 主瓣宽以 bin 计) —— 供对比实验引用
WINDOW_SPECS = {
    "rect":     dict(main_lobe=0.9, peak_sidelobe_db=-13, desc="矩形: 过渡带最窄, 旁瓣最高"),
    "hann":     dict(main_lobe=3.1, peak_sidelobe_db=-31, desc="Hann: 旁瓣衰减快, 通用"),
    "hamming":  dict(main_lobe=3.3, peak_sidelobe_db=-41, desc="Hamming: 固定窗里旁瓣最低"),
    "blackman": dict(main_lobe=5.5, peak_sidelobe_db=-57, desc="Blackman: 极低旁瓣, 过渡带宽"),
}


def window(name, n_taps):
    """取窗函数系数。name ∈ WINDOW_SPECS 的键, 或 ("kaiser", beta)。"""
    if isinstance(name, (tuple, list)):
        kind, param = name
        if kind != "kaiser":
            raise ValueError(f"未知参数化窗 {name!r}")
        return np.kaiser(n_taps, param)
    table = {"rect": np.ones, "hann": np.hanning,
             "hamming": np.hamming, "blackman": np.blackman}
    if name not in table:
        raise ValueError(f"未知窗 {name!r} (可选 {list(WINDOW_SPECS)} 或 ('kaiser', β))")
    return table[name](n_taps)


def fir_lowpass(cutoff, n_taps, win="hamming"):
    """窗函数法设计**线性相位**低通 FIR。

    cutoff: 归一化截止频率, 相对采样率 (0 ~ 0.5); 0.25 即 fs/4
    n_taps: 抽头数。**奇数** → Type I (整数群延迟, 适合抽取); 偶数 → Type II
    win:    窗名或 ("kaiser", β)

    返回长度为 n_taps 的实系数, 直流增益归一到 1 (Σh = 1)。
    """
    if not 0 < cutoff < 0.5:
        raise ValueError("cutoff 应在 (0, 0.5) 内 (归一化到采样率)")
    n = np.arange(n_taps) - (n_taps - 1) / 2          # 对称中心
    h_ideal = 2 * cutoff * np.sinc(2 * cutoff * n)    # 理想低通冲激响应
    h = h_ideal * window(win, n_taps)
    return h / np.sum(h)                              # 直流增益归一


def freq_response(h, n_fft=4096, fs=1.0):
    """滤波器频响: 返回 ``(freqs, mag_db, phase, group_delay)``。

    freqs 单位与 fs 相同 (默认归一化); group_delay 单位为采样。
    """
    h = np.asarray(h).ravel()
    n_fft = max(n_fft, len(h))
    H = np.fft.rfft(h, n_fft)
    f = np.fft.rfftfreq(n_fft, 1 / fs)
    mag_db = 20 * np.log10(np.abs(H) + 1e-30)
    phase = np.unwrap(np.angle(H))
    w = 2 * np.pi * f / fs                       # 角频率
    dw = np.gradient(w)
    gd = -np.gradient(phase) / np.where(np.abs(dw) > 0, dw, np.nan)
    return f, mag_db, phase, gd


def stopband_db(h, f_start=None, n_fft=8192):
    """阻带最高电平 (dB, 相对主瓣峰值) —— 低通滤波器的核心指标。

    f_start 为阻带起点 (归一化频率); 缺省取 **−3 dB 频率的 1.5 倍**, 即跨过过渡带
    之后再量 —— 若把边界就设在阈值处, 测出来必然等于该阈值, 没有信息量。
    """
    f, mag_db, _, _ = freq_response(h, n_fft)
    if f_start is None:
        f_start = 1.5 * occupied_bandwidth(h, fs=1.0) / 2
    m = f >= f_start
    return float(np.max(mag_db[m])) if m.any() else float("nan")


def window_sidelobe_db(name, n_taps=64, n_fft=8192):
    """窗函数**自身**频谱的最高旁瓣 (dB) —— 与教科书指标对照用。"""
    W = np.abs(np.fft.fft(window(name, n_taps), n_fft))
    db = 20 * np.log10(W / W.max() + 1e-30)
    m = 0
    while m + 1 < n_fft // 2 and db[m + 1] < db[m]:
        m += 1                                       # 主瓣右边界
    return float(db[m:n_fft // 2].max())


def occupied_bandwidth(h, fs=1.0, drop_db=3.0, n_fft=8192):
    """−3 dB 占用带宽 (单位与 fs 相同) —— 过渡带宽窄的实用度量。"""
    f, mag_db, _, _ = freq_response(h, n_fft, fs)
    passband = f[mag_db >= -drop_db]
    return float(2 * passband.max()) if passband.size else 0.0
