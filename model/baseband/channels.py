# -*- coding: utf-8 -*-
"""
baseband.channels —— 信道模型 (制式无关)

AWGN 在此定义; 多径抽头延迟线 / Rayleigh 抽头 / 预置多径场景从
baseband.impairments 复用 (多径本质是信道损伤, 且复用分数延迟实现)。
"""

import numpy as np

from .link import stage
from .impairments import add_multipath as multipath, rayleigh_taps, SCENARIOS


def awgn(x, snr_db, pulse_energy, rng=None):
    """加复高斯白噪声, 使匹配滤波器输出端 SNR = snr_db。

    推导: MF 输出峰值信号 = chip·pulse_energy, 噪声方差 = σw²·pulse_energy
          => SNR = pulse_energy / σw²  =>  σw² = pulse_energy / 10^(snr/10)
    pulse_energy = Σ|成形脉冲|² (half_sine(sps) 的 Σh² = sps/2)。
    """
    if rng is None:
        rng = np.random.default_rng()
    sigma2 = pulse_energy / 10 ** (snr_db / 10)
    w = np.sqrt(sigma2 / 2) * (rng.standard_normal(len(x))
                               + 1j * rng.standard_normal(len(x)))
    return x + w


def awgn_stage(snr_db, pulse_energy, rng=None):
    """AWGN 阶段工厂: 按匹配滤波后码片 SNR 标定的加噪阶段。

    pulse_energy (成形脉冲 Σh²) 是制式相关的量, 由制式配方传入 (如 phy.SPS/2);
    rng 传入同一 Generator 可保证多次实验可复现 —— 扫参时尤其重要。
    """
    return stage(awgn, name=f"awgn({snr_db:g}dB)", snr_db=snr_db,
                 pulse_energy=pulse_energy, rng=rng)


def awgn_waveform(x, snr_db, rng=None):
    """按**采样波形功率**加复高斯白噪声 —— 与 ``awgn`` 的口径不同。

    ``awgn`` 的 SNR 定义在匹配滤波输出端 (含脉冲能量 Σh²), 服务于扩频制式的
    码片 SNR; 本函数的 SNR 直接定义在采样波形上, 用于无扩频 / 常规成形的
    单载波调制 (PSK/ASK/QAM) —— 那里没有“码片”这个量。
    """
    if rng is None:
        rng = np.random.default_rng()
    x = np.asarray(x, dtype=complex)
    sigma2 = float(np.mean(np.abs(x) ** 2)) / 10 ** (snr_db / 10)
    w = np.sqrt(sigma2 / 2) * (rng.standard_normal(len(x))
                               + 1j * rng.standard_normal(len(x)))
    return x + w


def awgn_band_limited(x, snr_db, bw_hz, fs, rng=None):
    """带限 (带内) 复高斯噪声 —— 与全带白噪声 ``awgn_waveform`` 对照。

    噪声功率只落在 ``[−bw/2, +bw/2]`` 内。两者的区别:
      · 经匹配滤波后二者基本等效 (带外噪声会被滤掉);
      · 但**频谱图上一目了然**, 且带通采样时只有带外噪声会折叠进带内
        (见 ``bandpass_sampling_demo`` 的噪声折叠实验)。
    """
    if rng is None:
        rng = np.random.default_rng()
    x = np.asarray(x, dtype=complex)
    n = len(x)
    w = (rng.standard_normal(n) + 1j * rng.standard_normal(n)) / np.sqrt(2)
    W = np.fft.fft(w)
    W[np.abs(np.fft.fftfreq(n, 1 / fs)) > bw_hz / 2] = 0
    w = np.fft.ifft(W)
    p = float(np.mean(np.abs(x) ** 2))
    w = w / np.sqrt(np.mean(np.abs(w) ** 2)) * np.sqrt(p / 10 ** (snr_db / 10))
    return x + w
