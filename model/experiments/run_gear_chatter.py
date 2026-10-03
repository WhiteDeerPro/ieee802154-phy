#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""run_gear_chatter.py v2 —— 降档 permit 的"估计器振荡边界"实验。

背景: 估计口径相对环境标签有系统偏移（≈ −5.5dB, run_snr_est 口径常数），
因此本实验**不再假设阈值刻度**，改为:
  A) 阈值扫描: 对每环境点, 扫描判定阈值 T 相对 est 均值的偏置 ρ=est−mean(est),
     翻转率(T) —— 峰值即"最坏翻转率"（阈值对准均值时）, 并测 ≥50% 峰值的带宽;
  B) 慢漂移穿越: 环境 19→17dB 线性 ramp（60 帧）——穿越暂态的翻转数（无/有滞回）;
  C) 滞回对照: 上穿 +h / 下穿 −h（h=2dB 即现设计 18/16 间距）的翻转率。

输出: chatter_summary.csv + 控制台表 → "振荡边界"（翻转带宽度 vs σ_est、滞回效果）。
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'model'))
sys.path.insert(0, str(ROOT / 'tb' / 'rx_chain_e2e'))
import mc_gen                              # noqa: E402
from algo.snr_est import frame_snr_est     # noqa: E402

OUT = ROOT / 'model' / 'out' / 'dual_mc' / 'chatter'
OUT.mkdir(parents=True, exist_ok=True)
HYST = 2.0        # 滞回半带（现设计: 18/16 间距 2dB）


def load_pwr(p):
    v = np.fromfile(p / 'mem.bin', dtype='>u4')
    i = (v & 0xFFF).astype(np.float64)
    i = np.where(i >= 2048, i - 4096, i)
    q = ((v >> 12) & 0xFFF).astype(np.float64)
    q = np.where(q >= 2048, q - 4096, q)
    return i * i + q * q


def flips_of(seq):
    return int(np.sum(seq[1:] != seq[:-1]))


def scan_threshold(est, tgrid):
    """对相对阈值网格算翻转率（无滞回）。"""
    n = len(est) - 1
    return np.array([flips_of((est > t).astype(int)) / n for t in tgrid])


def hyst_flips(est, th_hi, th_lo):
    cur = int(est[0] > th_hi)
    flips = 0
    for e in est[1:]:
        if cur == 0 and e > th_hi:
            cur = 1
            flips += 1
        elif cur == 1 and e < th_lo:
            cur = 0
            flips += 1
    return flips


def main():
    print("=== A) 阈值扫描（阈值相对 est 均值偏置 ρ; 翻转率/帧）===\n")
    rows = []
    for snr in range(14, 22):
        d = OUT / f'snr{snr}'
        if not (d / 'mem.bin').exists():
            mc_gen.gen_point(float(snr), 40, 20, 8.0, 21, 400, 600, d,
                             gap_jitter=16, cfo_list=[100e3])
        pwr = load_pwr(d)
        fs = np.load(d / 'frames.npz')['frame_start'].astype(int)
        _, est = frame_snr_est(pwr, fs)
        if snr == 18:
            est18 = est.copy()
        mean, std = est.mean(), est.std()
        tgrid = mean + np.linspace(-1.5, 1.5, 61)
        rates = scan_threshold(est, tgrid)
        peak = rates.max()
        # ≥50% 峰值的带宽（相对均值的偏置区间）
        mask = rates >= max(peak * 0.5, 1e-9)
        width = (tgrid[mask].max() - tgrid[mask].min()) if mask.any() else 0.0
        # 滞回版（在峰值阈值处）
        t_peak = tgrid[int(np.argmax(rates))]
        fl_h = hyst_flips(est, t_peak + HYST / 2, t_peak - HYST / 2) / (len(est) - 1)
        rows.append((snr, mean, std, peak, width, fl_h))
        print(f'  env {snr:>2}dB: est {mean:>5.1f}±{std:.2f}  '
              f'最坏翻转率 {peak:.2f}/帧 (带宽 {width:.2f}dB)  滞回后 {fl_h:.3f}/帧')

    print(f'\n（参考: 翻转率 0.2/帧=平均 5 帧一翻; 0.02=50 帧一翻）')

    print("\n=== B) 慢漂移穿越（19→17dB 线性 ramp, 60 帧）===\n")
    d = OUT / 'ramp'
    if not (d / 'mem.bin').exists():
        specs = [(20, 19.0 - 2.0 * k / 59.0) for k in range(60)]
        mc_gen.gen_point(19.0, 60, 20, 8.0, 21, 400, 600, d,
                         gap_jitter=16, cfo_list=[100e3], frame_specs=specs)
    pwr = load_pwr(d)
    fs = np.load(d / 'frames.npz')['frame_start'].astype(int)
    _, est = frame_snr_est(pwr, fs)
    tgrid = est.mean() + np.linspace(-1.5, 1.5, 61)
    rates = scan_threshold(est, tgrid)
    t_peak = tgrid[int(np.argmax(rates))]
    fl0 = int(np.argmax(rates) >= 0) * flips_of((est > t_peak).astype(int))
    fl0 = flips_of((est > t_peak).astype(int))
    fl_h = hyst_flips(est, t_peak + HYST / 2, t_peak - HYST / 2)
    print(f'  ramp est: {est.min():.1f}..{est.max():.1f}（穿越全程）')
    print(f'  无滞回翻转 {fl0} 次 / 59 帧间隔; 滞回(±1dB) {fl_h} 次')

    # C) 滞回带扫描（死区设计曲线）
    print("\n=== C) 滞回带 h 扫描（翻转率 vs 死区宽度; 静态最坏点与 ramp）===\n")
    est_ramp = est  # B 段的 est（ramp）
    t0s = est18.mean()
    t0r = est_ramp.mean()
    print(f'{"h(dB)":>6} {"静态最坏(阈值对准均值)":>18} {"ramp 穿越":>12}')
    for h in [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0]:
        if h == 0.0:
            fs_ = flips_of((est18 > t0s).astype(int)) / (len(est18) - 1)
            fr_ = flips_of((est_ramp > t0r).astype(int)) / (len(est_ramp) - 1)
        else:
            fs_ = hyst_flips(est18, t0s + h / 2, t0s - h / 2) / (len(est18) - 1)
            fr_ = hyst_flips(est_ramp, t0r + h / 2, t0r - h / 2) / (len(est_ramp) - 1)
        print(f'{h:>6.1f} {fs_:>18.3f} {fr_:>12.3f}')

    with open(OUT / 'chatter_summary.csv', 'w') as f:
        f.write('snr,est_mean,est_std,peak_flip_rate,width_db,hyst_flip_rate\n')
        for r in rows:
            f.write(f'{r[0]},{r[1]:.2f},{r[2]:.3f},{r[3]:.3f},{r[4]:.2f},{r[5]:.3f}\n')
    print(f'\n（表 → {OUT}/chatter_summary.csv）')


if __name__ == '__main__':
    main()
