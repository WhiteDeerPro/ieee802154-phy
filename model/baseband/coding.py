# -*- coding: utf-8 -*-
"""
baseband.coding —— 通用 CRC 与 LFSR 扰码 (制式无关)

参数化多项式的位宽/初值/位序与反馈抽头, 使不同制式的 CRC / 白化复用同一实现。
802.15.4 的 CRC-16/AUG-CCITT (poly 0x1021) 与 PN9 白化 (x^9+x^5+1) 都是
本模块的实例, 见 phy_802154。
"""


def crc(data, poly, width=16, init=0, msb_first=True):
    """通用逐位 CRC。

    data: 字节串; poly: 生成多项式 (如 0x1021); width: 位宽;
    init: 初值; msb_first: 每字节 MSB-first 逐位处理。
    返回 width 位校验值 (int)。已知向量: crc(b'123456789', 0x1021) == 0x31C3。
    """
    mask = (1 << width) - 1
    crc_reg = init & mask
    for byte in data:
        for i in (range(7, -1, -1) if msb_first else range(8)):
            fb = ((crc_reg >> (width - 1)) & 1) ^ ((byte >> i) & 1)
            crc_reg = ((crc_reg << 1) & mask) ^ (poly if fb else 0)
    return crc_reg


def lfsr_scramble(data, degree, taps, seed=0, lsb_first=True):
    """通用 LFSR 扰码/白化 (Fibonacci 左移, 反馈抽头可配置)。

    degree: 寄存器位数; taps: 反馈抽头位位置序列 (含最高位 degree-1);
    seed: 初始状态; lsb_first: 输出字节位序 (逐字节 LSB-first)。
    PN9 (x^9+x^5+1) 即 degree=9, taps=(8, 4), seed=0x1FF。
    """
    mask = (1 << degree) - 1
    s = seed & mask
    out = bytearray()
    for byte in data:
        o = 0
        for i in range(8):
            pn_bit = (s >> (degree - 1)) & 1
            fb = 0
            for t in taps:
                fb ^= (s >> t) & 1
            s = ((s << 1) & mask) | fb
            o |= (((byte >> i) & 1) ^ pn_bit) << i
        out.append(o)
    return bytes(out)
