#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""run_despread_norm.py —— 解扩判决度量的"多边形化"实验（去乘法器路线）。

数据路径角色: 相关 s_k → **排序度量** → argmax。判决只需"最大", 因此度量可为任意
保序近似的范数——平方（I²+Q², "圆"）只是其中一种; 用多边形范数（加/移位/比较实现）
可把 32 个平方器（§65: 6.3 万门/82%）全部免掉。

度量族:
  sq       : I²+Q²            （圆, 基线）
  diamond  : |I|+|Q|          （菱形, 45° 最差 +41%）
  oct2     : max+min/2        （八边形）
  oct4     : max+min/4        （更近圆）
  hex16    : 4 段线性拟合 √(1+k²)（"十六边形", 系数现场数值拟合）

两个测法:
  ① 帧失败数（snr 2/0/-2/-4 压到失败区——理想模型链比 RTL 场景温和得多）;
  ② 符号级 argmax 一致率（vs sq, 不依赖帧失败——直接量"度量失真导致的判决差异"）。
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'model'))
import chains                               # noqa: E402
import measure                              # noqa: E402
import phy_802154 as phy                    # noqa: E402


def fit_hex16():
    """4 段线性 a+b·k 拟合 f(k)=√(1+k²), 最小化段内相对误差的 max/min 比。"""
    ks = np.linspace(0, 1, 801)
    f = np.sqrt(1 + ks ** 2)
    edges = [0, 0.25, 0.5, 0.75, 1.0]
    coefs = []
    for i in range(4):
        seg = (ks >= edges[i]) & (ks <= edges[i + 1])
        kk, ff = ks[seg], f[seg]
        best = None
        for a in np.arange(0.85, 1.16, 0.01):
            for b in np.arange(0.10, 0.75, 0.01):
                r = (a + b * kk) / ff
                ratio = r.max() / r.min()
                if best is None or ratio < best[0]:
                    best = (ratio, round(a, 3), round(b, 3))
        coefs.append(best)
    return coefs


def metric(s, kind, coefs=None):
    re, im = np.abs(s.real), np.abs(s.imag)
    if kind == 'sq':
        return re * re + im * im
    mx, mn = np.maximum(re, im), np.minimum(re, im)
    if kind == 'diamond':
        return re + im
    if kind == 'oct2':
        return mx + mn * 0.5
    if kind == 'oct4':
        return mx + mn * 0.25
    if kind == 'hex16':
        k = mn / np.maximum(mx, 1e-12)
        out = np.zeros_like(mx)
        edges = [0, 0.25, 0.5, 0.75, 1.01]
        for i, (_, a, b) in enumerate(coefs):
            m = (k >= edges[i]) & (k < edges[i + 1])
            out[m] = a * mx[m] + b * mn[m]
        return out
    raise ValueError(kind)


def despread_norm(chips, kind, coefs=None):
    mat = np.asarray(chips, dtype=complex).reshape(-1, phy.CHIP.shape[1])
    s = mat @ phy.CHIP.T
    return np.argmax(metric(s, kind, coefs), axis=1)


def stage(kind, coefs=None):
    def apply(sig):
        chips = sig.rx_chips
        n = len(chips) // 32
        sig.rx_symbols = despread_norm(chips, kind, coefs).astype(int)[:n]
    apply.__name__ = f'despread:{kind}'
    return apply


def make_chain(kind, snr, rng, coefs=None):
    rx = [chains.stage_matched_filter(), chains.stage_sync('honest'),
          chains.stage_sample_chips(), stage(kind, coefs), chains.stage_deframe()]
    c = chains.Chain(name=f'dn:{kind}')
    c.then_tx(*chains.tx_stages())
    c.then_channel(chains.awgn(snr, rng))
    c.then_rx(*rx)
    return c


def run_point(kind, snr, coefs=None, n_frames=60, seed=0):
    rng = np.random.default_rng(seed)
    fails = 0
    for _ in range(n_frames):
        payload = bytes(rng.integers(0, 256, size=20).tolist())
        c = make_chain(kind, snr, rng, coefs)
        sig = c.run(payload=payload)
        n_err, _ = measure.frame_bit_errors(sig)
        if n_err > 0:
            fails += 1
    return fails


def agreement_point(snrs, coefs, n_frames=40, seed=0):
    """同一批 chips 上, 各度量 vs sq 的 argmax 差异率（符号级）。"""
    kinds = ['diamond', 'oct2', 'oct4', 'hex16']
    out = {k: [0, 0] for k in kinds}
    for snr in snrs:
        rng = np.random.default_rng(seed)
        rx = [chains.stage_matched_filter(), chains.stage_sync('honest'),
              chains.stage_sample_chips(), chains.stage_despread(),
              chains.stage_deframe()]
        for _ in range(n_frames):
            payload = bytes(rng.integers(0, 256, size=20).tolist())
            c = chains.Chain(name='agr')
            c.then_tx(*chains.tx_stages())
            c.then_channel(chains.awgn(snr, rng))
            c.then_rx(*rx)
            sig = c.run(payload=payload)
            chips = sig.rx_chips
            n = len(chips) // 32
            mat = np.asarray(chips, dtype=complex)[:n * 32].reshape(-1, 32)
            s = mat @ phy.CHIP.T
            ref = np.argmax(metric(s, 'sq'), axis=1)
            for k in kinds:
                got = np.argmax(metric(s, k, coefs), axis=1)
                out[k][0] += int((got != ref).sum())
                out[k][1] += len(ref)
    return out


def main():
    coefs = fit_hex16()
    print('hex16 分段系数（k 区间 [0,.25/.5/.75/1], a+b·k; 括号内 = 段内 max/min 误差比）:')
    edges = [0, 0.25, 0.5, 0.75, 1.0]
    for i, (ratio, a, b) in enumerate(coefs):
        print(f'  [{edges[i]:.2f},{edges[i + 1]:.2f}]: a={a:.2f} b={b:.2f}  (ratio {ratio:.3f})')
    print()
    kinds = ['sq', 'diamond', 'oct2', 'oct4', 'hex16']
    print('--- ① 帧失败数（snr 2/0/-2/-4; 60 帧/点）---')
    snrs = [2.0, 0.0, -2.0, -4.0]
    print(f'{"度量":<10}' + ''.join(f'{("snr" + str(int(s))):>11}' for s in snrs))
    for kind in kinds:
        row = [str(run_point(kind, snr, coefs)) for snr in snrs]
        print(f'{kind:<10}' + ''.join(f'{v + "/60":>11}' for v in row), flush=True)
    print()
    print('--- ② 符号级 argmax 一致率（vs sq; snr 20/6/0/-4 各 40 帧）---')
    ag = agreement_point([20.0, 6.0, 0.0, -4.0], coefs)
    for k, (d, t) in ag.items():
        print(f'  {k:<8}: {d}/{t} 差异  ({d / t * 100:.3f}%)')


if __name__ == '__main__':
    main()
