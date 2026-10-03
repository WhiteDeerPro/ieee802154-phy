#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""run_csq_buf.py —— Csq 缓冲内容分析（以 Csq 设计为基准）: ebuf 能量样本的"压缩程度"。

基准公式（csq RTL, W=16 域）:
    v_sq = i² + q²（≤33 位）→ v_sq_c = v_sq[32:9]（24 位）→ ebuf[512]（加新减旧滑窗）
    用途 = 16 相滑窗能量 → argmax 定相（每 CREF=128 拍刷新, 窗=512 拍）。

两层证据:
  [A] 内容痕迹: 24 位翻转剖面 + 有效位（最高有效位分布）+ 动态范围（max/中位 dB）。
  [B] 功能保真: 滑窗定相 argmax 一致率 vs 截位深度
      （v_sq_c >> k 后重算定相, 与全 24 位参照比对——直接回答"要几位"）。

场景: snr20 / -1dB / -8dB; 40 帧。
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
VB = 24                      # v_sq_c 位宽（= 2W-8）
WIN, STEP = 512, 128         # csq 滑窗参数


def csq_energy(x):
    """复数 mf 序列（帧自适应定标到 W=16 域）→ v_sq_c（24 位能量样本）。"""
    fs = float(np.abs(x).max()) or 1.0
    iv = np.round(np.asarray(x).real / fs * FULL).astype(np.int64)
    qv = np.round(np.asarray(x).imag / fs * FULL).astype(np.int64)
    return (iv * iv + qv * qv) >> 9          # ≤33 位 → 右移 9 → 24 位域


def phase_argmax_seq(v, shift=0):
    """滑窗（512, step 128）16 相分组和的 argmax 序列（csq 粗搜行为近似）。"""
    vv = v >> shift
    out = []
    for t in range(WIN, len(vv), STEP):
        w = vv[t - WIN:t].reshape(WIN // 16, 16)
        out.append(int(w.sum(axis=0).argmax()))
    return out


def bit_profile(seq):
    bits = ((seq[:, None] >> np.arange(VB)[None, :]) & 1).astype(bool)
    return (bits[:-1] != bits[1:]).mean(axis=0)      # [0] = b0


def collect(snr_db, n_frames=N_FRAMES, seed=7):
    rng = np.random.default_rng(seed)
    prof = np.zeros(VB)
    msb, dr = [], []
    shifts = [0, 4, 8, 10, 12, 14, 16]
    agree = {k: [] for k in shifts if k > 0}
    for _ in range(n_frames):
        payload = bytes(rng.integers(0, 256, size=20).tolist())
        rx = [chains.stage_matched_filter(), chains.stage_sync('honest'),
              chains.stage_sample_chips(), chains.stage_despread(), chains.stage_deframe()]
        c = chains.Chain(name='csqbuf')
        c.then_tx(*chains.tx_stages())
        c.then_channel(chains.awgn(snr_db, rng))
        c.then_rx(*rx)
        sig = c.run(payload=payload)
        v = csq_energy(sig.meta['mf'])
        prof += bit_profile(v)
        nz = v[v > 0]
        msb.append(int(np.floor(np.log2(max(int(nz.max()), 1)))))
        dr.append(20 * np.log10(max(nz.max() / max(float(np.median(nz)), 1.0), 1.0)))
        ref = phase_argmax_seq(v, 0)
        for k in agree:
            got = phase_argmax_seq(v, k)
            agree[k].append(float(np.mean([a == b for a, b in zip(ref, got)])))
    out = {'prof': prof / n_frames, 'msb': msb, 'dr': dr,
           'agree': {k: float(np.mean(v_)) for k, v_ in agree.items()}}
    return out


def main():
    scenes = [('snr20', 20.0), ('-1dB', -1.0), ('-8dB', -8.0)]
    data = {tag: collect(snr) for tag, snr in scenes}
    print('=== Csq ebuf 能量样本（24 位）内容分析: 位翻转率(%) 剖面 ===')
    hdr = '          ' + ''.join(f'{("b" + str(b)):>7}' for b in range(VB - 1, -1, -1))
    print(hdr)
    for tag, _ in scenes:
        t = data[tag]['prof']
        print(f'{tag:>6}    ' + ''.join(f'{v * 100:>7.1f}' for v in t[::-1]))
    print()
    print('=== 有效位与动态范围 ===')
    for tag, _ in scenes:
        d = data[tag]
        msb = np.array(d['msb'])
        print(f'  {tag:>6}: 最高有效位 p50={int(np.median(msb))} max={int(msb.max())} '
              f'(位宽需求 ≤{int(msb.max()) + 1}) | max/中位 {float(np.mean(d["dr"])):.1f} dB')
    print()
    print('=== 功能保真: 定相 argmax 一致率 vs 截位（v_sq_c >> k; 参照=全 24 位）===')
    print('          ' + ''.join(f'{(">>" + str(k)):>8}' for k in [4, 8, 10, 12, 14, 16]))
    for tag, _ in scenes:
        ag = data[tag]['agree']
        print(f'{tag:>6}    ' + ''.join(f'{ag[k] * 100:>7.1f}%' for k in [4, 8, 10, 12, 14, 16]))
    print()
    print('（>>k 后样本位宽 = 24-k: >>12→12 位, >>14→10 位, >>16→8 位）')


if __name__ == '__main__':
    main()
