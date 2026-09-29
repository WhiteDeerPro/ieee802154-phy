# -*- coding: utf-8 -*-
"""
algo.agc —— 自动增益控制与自适应判决门限
==========================================

为什么需要: 参考模型里判决是「对**已知的**理想星座取最近点」—— 前提是增益精确
已知。真实接收机里 AGC 有误差 (尤其在短前导、低 SNR 下收敛不完), 16-QAM 这类
多电平调制一旦增益偏几个 dB, 判决就系统性出错。

本模块提供两种从数据估增益的办法, 复杂度都是「几个乘加」量级:

    gain_from_moment()      一阶/二阶矩直接估 —— 一次遍历, 无迭代
    gain_from_decision()    决策导向 (DD) 迭代 —— 用判决结果回头校正, 更准

两者都不需要 FFT、不需要排序、不需要矩阵。
"""

import numpy as np

try:                                     # 一阶 IIR 用 lfilter 表达 (与 RTL 等价)
    from scipy.signal import lfilter
except ImportError:                      # pragma: no cover
    lfilter = None


def _iir_alpha(n_samples, span):
    """把「时间常数 span 个采样」换算成一阶 IIR 的 α (α = 1 − e^(−1/span))。"""
    return 1.0 - np.exp(-1.0 / max(span, 1.0))


def agc(x, span=200, target=1.0, eps=1e-12):
    """一阶 IIR 自动增益控制 (跟踪滑动功率, 归一化到 target)。

    复杂度: 每采样 1 乘 (功率递推) + 1 除 + 2 乘 —— **无 FFT、无矩阵**。
    span 为功率跟踪的时间常数 (采样); 太小会跟着调制起伏, 太大会跟不上衰落。

    注意: 这是「逐采样」的宽带 AGC。若只想给判决点定标, 用下面的 gain_from_* 更准。
    """
    x = np.asarray(x, dtype=complex)
    a = _iir_alpha(len(x), span)
    if lfilter is None:
        p = np.empty(len(x))
        acc = 0.0
        for n, v in enumerate(np.abs(x) ** 2):
            acc = (1 - a) * acc + a * v
            p[n] = acc
    else:
        p = lfilter([a], [1, -(1 - a)], np.abs(x) ** 2)
    return x * (target / np.sqrt(p + eps))


def gain_from_moment(soft, nominal):
    """用**二阶矩**从接收软值估增益 —— 一次遍历, 纯乘加。

        g = sqrt( E|x|² / E|x_nom|² )

    前提: 软值已去掉 DC、且噪声功率远小于信号 (低 SNR 时会高估增益, 因为噪声
    也计入总功率)。这是最省的做法, 适合做 AGC 的粗定标。
    """
    soft = np.asarray(soft, dtype=complex).ravel()
    nominal = np.asarray(nominal, dtype=complex).ravel()
    if nominal.size == 0:
        raise ValueError("nominal 为空")
    # 软值按标称星座的占比抽样, 否则均值口径不一致: 按等概率符号假设计
    p_rx = float(np.mean(np.abs(soft) ** 2))
    p_nom = float(np.mean(np.abs(nominal) ** 2))
    return float(np.sqrt(p_rx / p_nom)) if p_nom > 0 else 1.0


def gain_from_decision(soft, const, n_iter=3, tol=1e-4):
    """**决策导向 (DD)** 增益估计: 迭代「判决 → 用**判决出的点**重新定标」。

        g ← sqrt( E|x|² / E|ŝ|² ),   ŝ = 按当前 g 判决出的理想点

    注意更新式本身**不含 g** —— 否则 ``g ← g·sqrt(g²P/P) = g²`` 会正反馈发散。
    每轮 O(N) 乘加, 通常 2~3 轮收敛 (RTL 里对应「每帧几轮」或「逐块跟踪一次」)。
    与矩估计的区别: 它只用**实际被判决到**的星座点定标, 不用等概率假设。
    """
    soft = np.asarray(soft, dtype=complex).ravel()
    const = np.asarray(const, dtype=complex).ravel()
    g = gain_from_moment(soft, const)
    p_rx = float(np.mean(np.abs(soft) ** 2))
    for _ in range(max(int(n_iter), 1)):
        dec = const[np.argmin(np.abs(soft[:, None] - (g * const)[None, :]), axis=1)]
        p_dec = float(np.mean(np.abs(dec) ** 2))
        if p_dec <= 0:
            break
        g_new = float(np.sqrt(p_rx / p_dec))
        if abs(g_new - g) <= tol * max(abs(g), 1e-9):
            g = g_new
            break
        g = g_new
    return g


def decide_with_gain(soft, const, gain=1.0):
    """按给定增益判决: 对缩放后的星座取最近点。"""
    const = np.asarray(const, dtype=complex).ravel()
    soft = np.asarray(soft, dtype=complex).ravel()
    scaled = const * gain
    return const[np.argmin(np.abs(soft[:, None] - scaled[None, :]), axis=1)]
