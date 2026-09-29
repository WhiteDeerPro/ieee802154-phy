# -*- coding: utf-8 -*-
"""
baseband.frontend —— 零中频前端 DC/IQ 不平衡联合 LS 校正 (制式无关)

模型: r = α·s + β·conj(s) + d   (α 通道增益, β 镜像项, d DC 偏移)
给定已知参考码片 s 与观测码片软值 r, 实/虚堆叠线性回归一次解出 6 实未知数,
再对整帧应用逆变换。
"""

import numpy as np


def estimate_dc_iq(r_chips, s_chips):
    """联合 LS 估计 r = α·s + β·conj(s) + d; 返回 (alpha, beta, d) 复数。"""
    s = np.asarray(s_chips, dtype=complex)
    r = np.asarray(r_chips, dtype=complex)
    N = len(s)
    Re_s, Im_s, Re_r, Im_r = s.real, s.imag, r.real, r.imag
    # 未知量 x = [Reα, Imα, Reβ, Imβ, Re d, Im d]
    A = np.zeros((2 * N, 6))
    A[:N, 0] = Re_s;  A[:N, 1] = -Im_s; A[:N, 2] = Re_s;  A[:N, 3] = Im_s;  A[:N, 4] = 1
    A[N:, 0] = Im_s;  A[N:, 1] = Re_s;  A[N:, 2] = -Im_s; A[N:, 3] = Re_s;  A[N:, 5] = 1
    b = np.concatenate([Re_r, Im_r])
    x, *_ = np.linalg.lstsq(A, b, rcond=None)
    return complex(x[0], x[1]), complex(x[2], x[3]), complex(x[4], x[5])


def apply_dc_iq_correction(r, alpha, beta, d):
    """逆变换: s_hat = (conj(α)(r-d) - β·conj(r-d)) / (|α|²-|β|²)。"""
    r0 = np.asarray(r, dtype=complex) - d
    denom = abs(alpha) ** 2 - abs(beta) ** 2
    return (np.conj(alpha) * r0 - beta * np.conj(r0)) / denom
