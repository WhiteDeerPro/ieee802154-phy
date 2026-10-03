#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""gen_rand_scenes.py —— 随机流量场景生成（"把位宽边界打出来"用）。

6 批随机流（每批 30 帧; 每帧独立随机 (psdu_len, snr) + 批次主题差异）:
  rand1 温和:   len~U(1,100),   snr~U(10,22)
  rand2 边缘:   len~U(1,125),   snr~U(6,12)
  rand3 长帧:   len~U(80,125),  snr~U(8,16)
  rand4 低SNR:  len~U(1,125),   snr~U(4,10)
  rand5 多径:   mp=[1,.5]@4 采样, snr~U(8,16)
  rand6 极端:   len~U(60,125),  snr~U(5,10)
用法: python tb/rx_dual_mc/gen_rand_scenes.py
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tb' / 'rx_chain_e2e'))
import mc_gen                               # noqa: E402

DMC = ROOT / 'model' / 'out' / 'dual_mc'
FRAMES = 30

BATCHES = [
    ('rand1', 101, 1, 100, 10.0, 22.0, None),
    ('rand2', 102, 1, 125, 6.0, 12.0, None),
    ('rand3', 103, 80, 125, 8.0, 16.0, None),
    ('rand4', 104, 1, 125, 4.0, 10.0, None),
    ('rand5', 105, 1, 100, 8.0, 16.0, dict(gains=[1.0, 0.5], delays=[0, 4])),
    ('rand6', 106, 60, 125, 5.0, 10.0, None),
]


def gen_rand(tag, seed, len_lo, len_hi, snr_lo, snr_hi, mp, frames=FRAMES):
    out = DMC / tag
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed + 1000)
    specs = [(int(rng.integers(len_lo, len_hi + 1)),
              float(rng.uniform(snr_lo, snr_hi))) for _ in range(frames)]
    meta = mc_gen.gen_point(20, frames, 20, 8.0, seed, 400, 600, out,
                            gap_jitter=16, cfo_list=[100e3],
                            frame_specs=specs, mp=mp)
    lsum = sum(L for L, _ in specs) / len(specs)
    ssum = sum(s for _, s in specs) / len(specs)
    print(f"{tag}: {meta['n_smp']} 采样 | 帧长均值 {lsum:.0f} | snr 均值 {ssum:.1f} dB"
          + (" | 多径" if mp else ""))


def main():
    for tag, seed, l_lo, l_hi, s_lo, s_hi, mp in BATCHES:
        gen_rand(tag, seed, l_lo, l_hi, s_lo, s_hi, mp)
    print('随机流量场景完成（rand1..rand6, 各 30 帧）')


if __name__ == '__main__':
    main()
