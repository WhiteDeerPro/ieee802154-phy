# -*- coding: utf-8 -*-
"""
baseband.sync —— 载波同步 (制式无关)

消旋 correct_cfo 是最通用的基带载波纠正: 给定估计频偏, 对采样流做反向
复指数旋转, 把残余 CFO 压到解调容限内。频偏估计本身依赖具体制式的前导
结构, 留在制式实例 (phy_802154) 里实现; 消旋是制式无关的。
"""

import numpy as np


def correct_cfo(y, f_est, fs=16e6):
    """数字消旋: y[n] *= exp(-j·2π·f_est·n/fs), 纠正估计频偏 f_est。"""
    n = np.arange(len(y))
    return y * np.exp(-2j * np.pi * f_est * n / fs)
