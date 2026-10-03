#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""run_bit_inter.py —— 位间分析: 编码内部的位间耦合（可压缩空间）与编码间对比。

口径: 对"相邻样本的翻转事件"Δ_b ∈ {0,1}（b = 位号）做统计:
  · 单位翻转率 p_b → 二元熵 H(Δ_b)
  · 相邻位对互信息 I(Δ_b; Δ_{b+1})（bit）——局部耦合强度
  · 组内冗余 = Σ H(Δ_b) − H(Δ_组)（组的联合熵, 经验分布）——
    **这就是"编码本身利用位间结构可压缩的活动量"（bit/样本）**

对象: chips（补码 vs 符号-幅度）与 ebuf 能量样本（无符号 24 位）。
场景: -1dB 工作点; 40 帧。
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'model'))
import chains                               # noqa: E402
import measure                              # noqa: E402

N_FRAMES = 40
W = 16
FULL = 2 ** (W - 1) - 1


def ent2(p):
    p = float(p)
    if p <= 0 or p >= 1:
        return 0.0
    return -(p * np.log2(p) + (1 - p) * np.log2(1 - p))


def mi_bits(a, b):
    """两个二元序列的互信息（bit）。"""
    n = len(a)
    p1a = a.mean(); p1b = b.mean()
    p11 = (a & b).mean()
    p10 = p1a - p11; p01 = p1b - p11; p00 = 1 - p11 - p10 - p01
    Hj = 0.0
    for p in (p00, p01, p10, p11):
        if 0 < p < 1:
            Hj -= p * np.log2(p)
    return ent2(p1a) + ent2(p1b) - Hj


def group_red(F, cols):
    """组冗余 = ΣH(单个) − H(联合)。F: (M, Wb) bool。"""
    sub = F[:, list(cols)]
    idx = sub.dot(2 ** np.arange(len(cols)))
    cnt = np.bincount(idx, minlength=2 ** len(cols)).astype(float)
    p = cnt / cnt.sum()
    p = p[p > 0]
    Hj = float(-(p * np.log2(p)).sum())
    Hi = float(sum(ent2(F[:, c].mean()) for c in cols))
    return Hi - Hj


def flips(v, enc, Wb, raw=False):
    """数值序列 → 翻转事件矩阵 (M, Wb)。enc: 'twos' | 'sm'; raw=已是位域整数。"""
    if raw:
        q = np.asarray(v).astype(np.int64)
    else:
        fs = float(np.abs(v).max())
        if fs <= 0:
            return np.zeros((0, Wb), bool)
        q = np.round(np.asarray(v) / fs * FULL).astype(np.int64)
        q = np.clip(q, -FULL - 1, FULL)
    if enc == 'twos':
        c = q & (2 ** Wb - 1)
    else:
        c = ((q < 0).astype(np.int64) << (Wb - 1)) | (np.abs(q) & (2 ** (Wb - 1) - 1))
    bits = ((c[:, None] >> np.arange(Wb)[None, :]) & 1).astype(bool)
    return bits[1:] != bits[:-1]


def csq_energy(x):
    fs = float(np.abs(x).max()) or 1.0
    iv = np.round(np.asarray(x).real / fs * FULL).astype(np.int64)
    qv = np.round(np.asarray(x).imag / fs * FULL).astype(np.int64)
    return ((iv * iv + qv * qv) >> 9)


def report(name, F, groups4, groups8):
    Wb = F.shape[1]
    p = F.mean(axis=0)
    H = np.array([ent2(pi) for pi in p])
    print(f'--- {name}（翻转口径; 活动 Σp = {p.sum():.2f} 位/样本）---')
    print('  相邻位对 I(Δb;Δb+1) [bit]: ' +
          ' '.join(f'{mi_bits(F[:, i], F[:, i + 1]):.2f}' for i in range(Wb - 1)))
    print('  4 位组冗余 [bit/样本]: ' +
          ' | '.join(f'[{c[0]}:{c[-1]}] {group_red(F, c):.2f}' for c in groups4))
    print('  8 位组冗余 [bit/样本]: ' +
          ' | '.join(f'[{c[0]}:{c[-1]}] {group_red(F, c):.2f}' for c in groups8))
    print(f'  组内冗余合计（4 位组; = 位间可压缩活动）: {sum(group_red(F, c) for c in groups4):.2f} bit/样本')
    print()


def main():
    rng = np.random.default_rng(7)
    F_ch_tw, F_ch_sm, F_eb = [], [], []
    for _ in range(N_FRAMES):
        payload = bytes(rng.integers(0, 256, size=20).tolist())
        rx = [chains.stage_matched_filter(), chains.stage_sync('honest'),
              chains.stage_sample_chips(), chains.stage_despread(), chains.stage_deframe()]
        c = chains.Chain(name='bitinter')
        c.then_tx(*chains.tx_stages())
        c.then_channel(chains.awgn(-1.0, rng))
        c.then_rx(*rx)
        sig = c.run(payload=payload)
        x = sig.rx_chips
        F_ch_tw.append(flips(np.asarray(x).real, 'twos', W))
        F_ch_tw.append(flips(np.asarray(x).imag, 'twos', W))
        F_ch_sm.append(flips(np.asarray(x).real, 'sm', W))
        F_ch_sm.append(flips(np.asarray(x).imag, 'sm', W))
        eb = csq_energy(sig.meta['mf'])
        F_eb.append(flips(eb, 'twos', 24, raw=True))
    F_ch_tw = np.concatenate(F_ch_tw)
    F_ch_sm = np.concatenate(F_ch_sm)
    F_eb = np.concatenate(F_eb)

    print('=== 位间分析（-1dB, 40 帧）===')
    print()
    g4_16 = [(0, 1, 2, 3), (4, 5, 6, 7), (8, 9, 10, 11), (12, 13, 14, 15)]
    g8_16 = [(0, 1, 2, 3, 4, 5, 6, 7), (8, 9, 10, 11, 12, 13, 14, 15)]
    report('chips 补码', F_ch_tw, g4_16, g8_16)
    report('chips 符号-幅度', F_ch_sm, g4_16, g8_16)
    g4_24 = [tuple(range(k, k + 4)) for k in range(0, 24, 4)]
    g8_24 = [tuple(range(k, k + 8)) for k in range(0, 24, 8)]
    report('ebuf 能量样本（24 位无符号）', F_eb, g4_24, g8_24)


if __name__ == '__main__':
    main()
