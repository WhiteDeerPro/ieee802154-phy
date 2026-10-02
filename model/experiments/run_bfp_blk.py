#!/usr/bin/env python
"""run_bfp_blk.py —— 块浮点"块长"vs 空间效率（回应"直接浮点还是块浮点"）。

块浮点与"逐样本浮点"是同一谱系的两端（块长 = 指数共享粒度）：
  BLK=1    → 每样本自带指数（"直接浮点"）
  BLK=1024 → 准固定点（整窗一个标度）
数据: pbuf v2 实测窗（1024 采样，含"帧前噪声 + 前导"——跨段动态的真实样本）。
口径: 8bit 尾数（含符号）/轴 + 6bit 指数/块（I/Q 共享）;
      等效 bits/样本 = 16 + 6/BLK（双轴合计）。
"""
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "model" / "out" / "dual_mc" / "pbuf_bfp" / "pbuf_lin.txt"


def load():
    d = np.loadtxt(SRC)
    return d[:, 1], d[:, 2]          # i, q（线性窗）


def enc_snr(x, blk):
    """块浮点编码-复原, 返回 (SNR dB, bits/样本)。x: (n,2)。"""
    n = len(x)
    rec = np.zeros_like(x)
    for b0 in range(0, n, blk):
        seg = x[b0:b0 + blk]
        m = float(np.max(np.abs(seg)))
        B = max(1, int(np.ceil(np.log2(m + 1)))) if m > 0 else 1
        s = B - 7
        t = np.round(seg / (2.0 ** s)) if s >= 0 else np.round(seg * (2.0 ** -s))
        t = np.clip(t, -128, 127)
        rec[b0:b0 + blk] = t * (2.0 ** s)
    e = float(np.sum((x - rec) ** 2))
    p = float(np.sum(x ** 2))
    return 10 * np.log10(p / max(e, 1e-12)), 16.0 + 6.0 / blk


def main():
    i, q = load()
    x = np.stack([i, q], axis=1)
    print("BLK | bits/样本(双轴) | 量化 SNR")
    for blk in (1, 4, 8, 16, 32, 64, 128, 256, 512, 1024):
        snr, bits = enc_snr(x, blk)
        tag = "  ← 现役(硬件)" if blk == 64 else ("  ← 逐样本浮点" if blk == 1 else "")
        print(f"{blk:4d} | {bits:6.2f}          | {snr:5.1f} dB{tag}")
    # 对照: 线性直存（无编码）
    print(f"线性 16bit/轴直存 = 32.00 bits/样本（无损占位对照）")


if __name__ == "__main__":
    main()
