# -*- coding: utf-8 -*-
"""
algo.snr_est —— 帧能量法 SNR 估计（证据收集器；对应 RTL 的 pwr/nse 两量）
============================================================================

为什么需要: ADC 档位决策（adc_gear）需要"环境证据"——帧级 SNR 估计。
方法（低复杂度, 无 FFT）: SNR_est = 10·log10(P_frame / P_noise − 1)
  P_frame = 帧内数据段窗均功率（信号+噪声）
  P_noise = 帧前空档的**低分位**（≈ RTL 的 nse min-tracking 下包络; 均值窗会被
            帧尾拖尾污染——run_snr_est 2026-10-02 实测）
口径同 run_snr_est（三档可分、间距 5.7/5.6 dB）。

接口（RTL 镜像 pwr/nse → 本模块为 ref）:
    f, est = frame_snr_est(pwr, fs, ...)   # 批量: 逐帧估计
    e      = snr_from_pwr(p_fr, p_n)       # 单帧: 由两量算 dB
复杂度: 每帧一次除法 + log —— RTL 侧可查表/近似。
"""
import numpy as np


def snr_from_pwr(p_fr, p_n):
    """由帧功率/噪声功率两量算 SNR（dB）。RTL: pwr/nse 两累加器 + 一次近似除法。"""
    return 10.0 * np.log10(max(p_fr / max(p_n, 1e-12) - 1.0, 1e-9))


def frame_snr_est(pwr, fs, fr_lo=3000, fr_hi=12000, pre_lo=1200, pre_hi=200, q=20.0):
    """逐帧能量法估计。

    帧 f 用 [fs[f]+fr_lo, fs[f]+fr_hi) 为信号窗、[fs[f]-pre_lo, fs[f]-pre_hi) 为
    噪声窗（低分位）。帧 0 无前窗 → 跳过。返回 (帧号数组, 估计 dB 数组)。
    """
    fs = np.asarray(fs, dtype=np.int64)
    out_f, out_e = [], []
    for f in range(1, len(fs)):
        lo = int(fs[f])
        p_fr = pwr[lo + fr_lo: lo + fr_hi].mean()
        gap = pwr[max(lo - pre_lo, 0): lo - pre_hi]
        p_n = np.percentile(gap, q) if len(gap) else pwr.mean()
        out_f.append(f)
        out_e.append(snr_from_pwr(p_fr, p_n))
    return np.array(out_f), np.array(out_e)
