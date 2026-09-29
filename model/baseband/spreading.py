# -*- coding: utf-8 -*-
"""
baseband.spreading —— 通用 DSSS 扩频/解扩 (制式无关)

把「符号 -> 码片」与「码片软值 -> 符号」从具体码片表解耦, 码片表作为参数。
802.15.4 的 16×32 码片表 (CHIP) 是实例, 见 phy_802154。
"""

import numpy as np


def spread(symbols, chip_table):
    """符号 -> 码片: 按码片表映射。chip_table 形状 (n_sym, chips_per_sym)。"""
    return chip_table[np.asarray(symbols, dtype=int)].reshape(-1)


def despread(chips, chip_table, coherent=False):
    """码片软值 -> 符号: 与码片表做复相关, 取 argmax。

    coherent=False 取 |·| 最大 (幅值检测, 免疫相位旋转);
    coherent=True  取实部最大 (相位已对齐 / 均衡补偿后适用)。
    """
    mat = np.asarray(chips, dtype=complex).reshape(-1, chip_table.shape[1])
    s = mat @ chip_table.T
    return np.argmax(np.real(s) if coherent else np.abs(s), axis=1)
