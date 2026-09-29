# -*- coding: utf-8 -*-
"""
baseband.equalization —— 信道估计与线性均衡 (制式无关)

把「多径 + 匹配滤波 + 码片采样」建模为码片速率等效离散 FIR 信道:
    y[m] = Σ_k h[k]·c[m-k] + n[m]
用已知参考序列做 LS 信道估计, 再频域 MMSE 均衡恢复干净码片软值。
"""

import numpy as np


def estimate_channel_ls(y, ref, L):
    """LS 估计等效离散信道 h[0..L-1]: y[m] ≈ Σ_k h[k]·ref[m-k], m = L-1..N-1。

    ref 为已知实数 ±1 参考序列 (如前导码片), y 为同长度观测码片软值 (复数)。
    """
    N = len(ref)
    if N < L:
        raise ValueError(f"reference length {N} < taps {L}")
    M = N - L + 1
    C = np.zeros((M, L))
    for k in range(L):
        C[:, k] = ref[(L - 1 - k):(L - 1 - k) + M]
    h, *_ = np.linalg.lstsq(C, y[L - 1:], rcond=None)
    return h


def equalize_mmse(y, h, noise_var):
    """频域 MMSE 均衡: c_hat = IFFT( FFT(y)·H* / (|H|² + noise_var) )。

    noise_var 为码片软值域噪声方差, 与 h 的幅度尺度一致。
    """
    n = len(y)
    H = np.fft.fft(h, n)
    V = np.fft.fft(y, n)
    G = np.conj(H) / (np.abs(H) ** 2 + noise_var)
    return np.fft.ifft(V * G)
