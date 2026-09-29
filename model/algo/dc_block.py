# -*- coding: utf-8 -*-
"""
algo.dc_block —— 极简 DC 阻塞（一阶 IIR 高通）
================================================

与 ``baseband.frontend.estimate_dc_iq`` 的分工:

    frontend     用**已知前导**做联合 LS, 一次解出 (α, β, d) —— 参考模型:
                 条件已知、闭式解、全帧沿用, 可用于 bit-true 比对。
    algo 本模块  不依赖任何已知序列, 只用一阶 IIR 跟踪直流分量再减掉 —— 算法层:
                 可**在数据流上持续跟踪**, LO 漂移 / 温漂 / 长帧都能跟。

复杂度: 每采样 **2 乘 + 2 加**, 一个寄存器。没有 FFT、没有矩阵。

时间常数的取法: ``span`` 要显著**大于符号周期**, 否则会把信号自身的低频成分也
削掉。802.15.4 符号周期 = 256 采样, 取 span ≥ 1000 (截止 ≪ 符号率) 是安全的。
"""

import numpy as np

try:
    from scipy.signal import lfilter
except ImportError:                      # pragma: no cover
    lfilter = None


def dc_block(x, span=1000, eps=1e-30):
    """一阶 IIR 跟踪均值并减掉: ``y[n] = x[n] − m[n]``, ``m ← (1−α)m + αx``。

    span: 均值跟踪的时间常数 (采样)。截止频率约 fs/(2π·span)。
    返回与输入等长的去直流信号。
    """
    x = np.asarray(x, dtype=complex).ravel()
    a = 1.0 - np.exp(-1.0 / max(float(span), 1.0))
    if lfilter is None:                              # 纯 numpy 回退 (与 RTL 等价)
        m = np.empty_like(x)
        acc = 0j
        for n, v in enumerate(x):
            acc = (1 - a) * acc + a * v
            m[n] = acc
    else:
        m = lfilter([a], [1, -(1 - a)], x)
    return x - m


def iq_imbalance_blind(x, span=2000):
    """**盲** I/Q 失衡抑制(仅用二阶统计, 无前导、无矩阵求逆)。

    基于: 理想复基带信号的 I/Q 应**功率相等且互不相关**。若不等, 用实时跟踪到的
    功率比与相关系数做一次解析校正 —— 每采样几个乘加。

    这是 ``frontend.estimate_dc_iq`` 的轻量替代: 精度不如前导 LS, 但不需要已知
    序列、能持续跟踪。返回校正后的信号。
    """
    x = np.asarray(x, dtype=complex).ravel()
    a = 1.0 - np.exp(-1.0 / max(float(span), 1.0))
    I, Q = x.real, x.imag
    if lfilter is None:                              # pragma: no cover
        acc = np.zeros(3)                            # [I², Q², IQ]
        p = np.empty((len(x), 3))
        for n in range(len(x)):
            acc = (1 - a) * acc + a * np.array([I[n] ** 2, Q[n] ** 2, I[n] * Q[n]])
            p[n] = acc
    else:
        p = np.stack([lfilter([a], [1, -(1 - a)], I ** 2),
                      lfilter([a], [1, -(1 - a)], Q ** 2),
                      lfilter([a], [1, -(1 - a)], I * Q)], axis=1)
    pi, pq, piq = p[:, 0], p[:, 1], p[:, 2]
    # 增益失衡: Q 轨按 sqrt(pi/pq) 缩放; 相位失衡: 用相关系数估计并去相关
    g = np.sqrt(np.maximum(pi, 1e-30) / np.maximum(pq, 1e-30))
    rho = piq / np.sqrt(np.maximum(pi * pq, 1e-30))
    Qc = (Q * g - rho * I) / np.sqrt(np.maximum(1 - rho ** 2, 1e-6))
    return I + 1j * Qc
