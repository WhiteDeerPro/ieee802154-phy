#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""run_ebuf_entropy.py —— ebuf 内容的熵 / 频谱 / 可压缩性实测。

对象: ebuf 能量样本序列（csq_energy = (i²+q²)>>9, 24 位, 16MHz 采样流）。
三层口径:
  1) 熵: 对数域桶（bit_length, 0..23）的 H0 / H1(条件) / H2 —— "时间维"冗余
     （与 §52 的"位间维"互补）。
  2) 实用压缩: zlib 对样本字节流 —— 整串 / byte-plane 分离 / 截 12 位版。
  3) 频谱: Welch PSD 带能量分布 + 主峰 + ACF(lag 1..16) —— 结构/带宽。

场景: -1dB 工作点; 40 帧。
"""
import sys
import zlib
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'model'))
import chains                               # noqa: E402
import measure                              # noqa: E402

N_FRAMES = 40
W = 16
FULL = 2 ** (W - 1) - 1
FS = 16e6
K = 24


def csq_energy(x):
    fs = float(np.abs(x).max()) or 1.0
    iv = np.round(np.asarray(x).real / fs * FULL).astype(np.int64)
    qv = np.round(np.asarray(x).imag / fs * FULL).astype(np.int64)
    return ((iv * iv + qv * qv) >> 9).astype(np.int64)


def ent(p):
    p = p[p > 0]
    return float(-(p * np.log2(p)).sum())


def h0(a):
    cnt = np.bincount(a)
    return ent(cnt / cnt.sum())


def h1(a, k):
    """H(a_t | a_{t-1})。"""
    prev, cur = a[:-1], a[1:]
    joint = np.zeros((k, k))
    np.add.at(joint, (cur, prev), 1)
    p = joint / joint.sum()
    pp = p.sum(axis=0)
    H = 0.0
    for j in range(k):
        if pp[j] > 0:
            H += pp[j] * ent(p[:, j] / pp[j])
    return H


def h2(a, k):
    """H(a_t | a_{t-1}, a_{t-2})。"""
    a2, a1, a0 = a[:-2], a[1:-1], a[2:]
    joint = np.zeros((k, k, k))
    np.add.at(joint, (a0, a1, a2), 1)
    p = joint / joint.sum()
    pa = p.sum(axis=0)                       # p(a1, a2)
    H = 0.0
    for j in range(k):
        for l in range(k):
            if pa[j, l] > 0:
                H += pa[j, l] * ent(p[:, j, l] / pa[j, l])
    return H


def welch_psd(v, nseg=1024, step=512):
    v = v.astype(float) - v.mean()
    win = np.hanning(nseg)
    acc = np.zeros(nseg // 2 + 1)
    cnt = 0
    for t in range(0, len(v) - nseg, step):
        X = np.fft.rfft(v[t:t + nseg] * win)
        acc += np.abs(X) ** 2
        cnt += 1
    return acc / max(cnt, 1)


def acf(v, maxlag=16):
    v = v.astype(float) - v.mean()
    denom = np.dot(v, v)
    return [float(np.dot(v[:-k], v[k:]) / denom) for k in range(1, maxlag + 1)]


def main():
    rng = np.random.default_rng(7)
    bl_list = []
    psd_acc, psd_cnt = None, 0
    acf_acc = np.zeros(16)
    z_raw = z_plane = z_12 = 0
    n_bytes3 = n_bytes2 = 0
    for _ in range(N_FRAMES):
        payload = bytes(rng.integers(0, 256, size=20).tolist())
        rx = [chains.stage_matched_filter(), chains.stage_sync('honest'),
              chains.stage_sample_chips(), chains.stage_despread(), chains.stage_deframe()]
        c = chains.Chain(name='ebufent')
        c.then_tx(*chains.tx_stages())
        c.then_channel(chains.awgn(-1.0, rng))
        c.then_rx(*rx)
        sig = c.run(payload=payload)
        v = csq_energy(sig.meta['mf'])
        bl_list.append(np.floor(np.log2(v + 1)).astype(int))
        # ---- zlib 压缩测试（逐帧） ----
        v24 = v & 0xFFFFFF
        b3 = np.stack([(v24 & 0xFF), ((v24 >> 8) & 0xFF), ((v24 >> 16) & 0xFF)],
                      axis=1).astype(np.uint8).tobytes()
        z_raw += len(zlib.compress(b3, 6))
        n_bytes3 += len(b3)
        z_plane += sum(len(zlib.compress(p.tobytes(), 6))
                       for p in [((v24 >> 16) & 0xFF), ((v24 >> 8) & 0xFF), (v24 & 0xFF)])
        v12 = (v24 >> 12) & 0xFFF
        b2 = np.stack([(v12 & 0xFF), ((v12 >> 8) & 0xFF)],
                      axis=1).astype(np.uint8).tobytes()
        z_12 += len(zlib.compress(b2, 6))
        n_bytes2 += len(b2)
        # ---- 频谱 / ACF ----
        p = welch_psd(v)
        psd_acc = p if psd_acc is None else psd_acc + p
        psd_cnt += 1
        acf_acc += np.array(acf(v))

    bl = np.concatenate(bl_list)
    psd = psd_acc / psd_cnt
    freqs = np.fft.rfftfreq(1024, d=1 / FS)
    tot = psd[1:].sum()
    band = lambda a, b: psd[(freqs >= a) & (freqs < b)].sum()

    print('=== ebuf 内容: 熵 / 压缩 / 频谱（-1dB, 40 帧）===')
    e0, e1, e2 = h0(bl), h1(bl, K), h2(bl, K)
    print(f'[熵] 对数域桶: H0={e0:.2f}  H1={e1:.2f}  H2={e2:.2f} bit/样本'
          f' | 时间冗余 H0-H1={e0 - e1:.2f}, H1-H2={e1 - e2:.2f}')
    print(f'[压缩] zlib: 整串 {z_raw / n_bytes3 * 100:.1f}% | '
          f'byte-plane 分离 {z_plane / n_bytes3 * 100:.1f}% | '
          f'截12位 {z_12 / n_bytes2 * 100:.1f}%（= 24 位原长的 {z_12 / n_bytes3 * 100:.1f}%）')
    print(f'[频谱] 能量带占比: <0.5M {band(0, 0.5e6) / tot * 100:.0f}% | '
          f'0.5-2M {band(0.5e6, 2e6) / tot * 100:.0f}% | '
          f'2-4M {band(2e6, 4e6) / tot * 100:.0f}% | '
          f'4-8M {band(4e6, FS / 2 + 1) / tot * 100:.0f}% | '
          f'主峰 {freqs[np.argmax(psd[1:]) + 1] / 1e6:.2f} MHz')
    print('[ACF] ' + ' '.join(f'{a:.2f}' for a in acf_acc / psd_cnt))


if __name__ == '__main__':
    main()
