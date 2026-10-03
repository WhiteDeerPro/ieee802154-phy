#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""run_ebuf_fft.py —— ebuf 能量流的 FFT 视图：PSD / 时频图 / 与 mf 复谱对照。

图 1  fft_psd.png        三 SNR 的 PSD 叠加（0..8MHz, dB, 归一化）:
                         x=频率(MHz), y=10log10(PSD) 相对峰值
图 2  fft_spectrogram.png 时频图（-1dB）: x=时间(μs), y=频率(MHz), 色=dB
                         竖线=前导/数据近似边界
图 3  fft_mf.png         对照：mf 复信号谱 vs 能量流谱（同一帧, -1dB）
"""
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt             # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'model'))
import chains                               # noqa: E402
import measure                              # noqa: E402

OUT = ROOT / 'model' / 'out' / 'dual_mc' / 'viz'
OUT.mkdir(parents=True, exist_ok=True)
W = 16
FULL = 2 ** (W - 1) - 1
FS = 16e6
NSEG = 1024
STEP = 512


def csq_energy(x):
    fs = float(np.abs(x).max()) or 1.0
    iv = np.round(np.asarray(x).real / fs * FULL).astype(np.int64)
    qv = np.round(np.asarray(x).imag / fs * FULL).astype(np.int64)
    return ((iv * iv + qv * qv) >> 9).astype(np.int64)


def one_frame(snr_db, seed=7):
    rng = np.random.default_rng(seed)
    payload = bytes(rng.integers(0, 256, size=20).tolist())
    rx = [chains.stage_matched_filter(), chains.stage_sync('honest'),
          chains.stage_sample_chips(), chains.stage_despread(), chains.stage_deframe()]
    c = chains.Chain(name=f'fft{snr_db}')
    c.then_tx(*chains.tx_stages())
    c.then_channel(chains.awgn(snr_db, rng))
    c.then_rx(*rx)
    sig = c.run(payload=payload)
    return csq_energy(sig.meta['mf']), sig.meta['mf']


def welch(v, nseg=NSEG, step=STEP):
    v = v.astype(float) - v.mean()
    win = np.hanning(nseg)
    acc = np.zeros(nseg // 2 + 1)
    cnt = 0
    for t in range(0, len(v) - nseg, step):
        X = np.fft.rfft(v[t:t + nseg] * win)
        acc += np.abs(X) ** 2
        cnt += 1
    return acc / max(cnt, 1), np.fft.rfftfreq(nseg, d=1 / FS)


def main():
    eb20, _ = one_frame(20.0)
    eb1, mf1 = one_frame(-1.0)
    eb8, _ = one_frame(-8.0)

    # ---- 图 1: PSD 三 SNR ----
    plt.figure(figsize=(11, 4.2))
    peaks = {}
    for tag, v in [('snr20', eb20), ('-1dB', eb1), ('-8dB', eb8)]:
        p, f = welch(v)
        p_db = 10 * np.log10(p + 1e-12)
        p_db -= p_db.max()
        plt.plot(f / 1e6, p_db, lw=1.0, label=tag)
        peaks[tag] = (f[np.argmax(p)], float(np.median(p_db)))
    for h in (1, 2, 3, 4):
        plt.axvline(h, color='gray', ls=':', lw=0.8)
    plt.xlabel('frequency (MHz)   dotted: 1/2/3/4 MHz')
    plt.ylabel('PSD (dB, peak-normalized)')
    plt.title('ebuf energy stream PSD (frame-averaged Welch, 16MSps)')
    plt.legend()
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(OUT / 'fft_psd.png', dpi=110)
    plt.close()

    # ---- 图 2: 时频图（-1dB） ----
    seg, stp = 256, 128
    win = np.hanning(seg)
    rows = []
    for t in range(0, len(eb1) - seg, stp):
        X = np.fft.rfft((eb1[t:t + seg] - eb1.mean()) * win)
        rows.append(np.abs(X) ** 2)
    S = 10 * np.log10(np.array(rows).T + 1e-6)          # freq × time
    S -= S.max()
    plt.figure(figsize=(13, 4.2))
    tmax = len(eb1) / FS * 1e6
    plt.imshow(S, aspect='auto', origin='lower', cmap='inferno',
               extent=[0, tmax, 0, FS / 2 / 1e6])
    plt.axvline(2048 / FS * 1e6, color='cyan', ls='--', lw=1.0)
    plt.text(2048 / FS * 1e6, 7.4, ' preamble | data (approx.)', color='cyan', fontsize=8)
    plt.xlabel('time (us)')
    plt.ylabel('frequency (MHz)')
    plt.title('ebuf energy: spectrogram (-1dB; 256-sample Hann segments)')
    plt.colorbar(label='dB (peak-normalized)')
    plt.tight_layout()
    plt.savefig(OUT / 'fft_spectrogram.png', dpi=110)
    plt.close()

    # ---- 图 3: mf 复谱 对照 ----
    p_eb, f = welch(eb1)
    p_mf, _ = welch(np.asarray(mf1).real)               # I 分量谱
    p_db_eb = 10 * np.log10(p_eb + 1e-12); p_db_eb -= p_db_eb.max()
    p_db_mf = 10 * np.log10(p_mf + 1e-12); p_db_mf -= p_db_mf.max()
    plt.figure(figsize=(11, 4.2))
    plt.plot(f / 1e6, p_db_mf, lw=1.0, label='mf (I component) spectrum')
    plt.plot(f / 1e6, p_db_eb, lw=1.0, label='energy stream spectrum')
    plt.xlabel('frequency (MHz)')
    plt.ylabel('PSD (dB, peak-normalized)')
    plt.title('mf complex-baseband vs energy stream (-1dB)')
    plt.legend()
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(OUT / 'fft_mf.png', dpi=110)
    plt.close()

    # ---- 读数 ----
    print('=== FFT 读数 ===')
    for tag in peaks:
        print(f'[{tag}] 主峰 {peaks[tag][0] / 1e6:.3f} MHz | 噪声地板(中位) {peaks[tag][1]:.1f} dB')
    for h in (1, 2, 3, 4, 5):
        i = np.argmin(np.abs(f - h * 1e6))
        print(f'  1MHz x{h} 处电平(相对峰): {10 * np.log10(p_eb[i] / p_eb.max() + 1e-12):.1f} dB')
    mf_bw = f[p_db_mf > -3][-1]
    eb_bw = f[p_db_eb > -3][-1]
    print(f'[-3dB 带宽] mf 复谱 ≈ {mf_bw / 1e6:.2f} MHz | 能量流 ≈ {eb_bw / 1e6:.2f} MHz')


if __name__ == '__main__':
    main()
