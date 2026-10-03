#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""run_ebuf_viz.py —— ebuf 内容的"索引图像"（模式可视化）+ 白噪声性质检验。

图 1  folded.png   折叠瀑布图: x = 样本地址（每格 16 拍）, y = 采样相位 0..15,
                   色 = log2(能量)——暴露周期结构（前导）vs 随机纹（数据段）
图 2  bitplane.png 位平面图: x = 样本索引, y = 位号 b0..b23, 色 = 位值——
                   高位块状（慢变）vs 低位雪花（白噪）的直接证据
图 3  phasemap.png 16 相滑窗能量图: x = 滑窗序号（步 128 拍）, y = 相位 0..15,
                   色 = 窗能量, 红线 = argmax——定相过程的直接可视化
图 4  snr_tri.png  三 SNR 折叠对比（snr20 / -1 / -8）

场景: 各 3 帧取 1; -1dB 用于图 1-3。
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
WIN, STEP = 512, 128


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
    c = chains.Chain(name=f'viz{snr_db}')
    c.then_tx(*chains.tx_stages())
    c.then_channel(chains.awgn(snr_db, rng))
    c.then_rx(*rx)
    sig = c.run(payload=payload)
    return csq_energy(sig.meta['mf'])


def phase_energies(v):
    out = []
    for t in range(WIN, len(v), STEP):
        w = v[t - WIN:t].reshape(WIN // 16, 16)
        out.append(w.sum(axis=0))
    return np.array(out)                     # (nwin, 16)


def fig_folded(v, fname, title):
    n = len(v) // 16 * 16
    M = v[:n].reshape(-1, 16)                # (rows, 16 phases)
    img = np.log2(M + 1.0).T                 # y=phase, x=row
    plt.figure(figsize=(15, 2.6))
    plt.imshow(img, aspect='auto', origin='lower', cmap='viridis')
    plt.ylabel('sample phase (0..15)')
    plt.xlabel('time: 16-sample steps (sample address // 16)')
    plt.title(title)
    plt.colorbar(label='log2(energy+1)')
    plt.tight_layout()
    plt.savefig(OUT / fname, dpi=110)
    plt.close()
    # 读数: 前导段（前 1400 拍）列均值峰 vs 数据段（后段）
    col_pre = M[:128].mean(axis=0)           # 前导区（~2048 采样）
    col_dat = M[-128:].mean(axis=0)          # 帧尾数据区
    return int(np.argmax(col_pre)), int(np.argmax(col_dat)), \
        float(col_pre.max() / max(col_pre.mean(), 1e-9)), \
        float(col_dat.max() / max(col_dat.mean(), 1e-9))


def fig_bitplane(v, fname, n=2048):
    x = v[:n]
    bits = ((x[:, None] >> np.arange(24)[None, :]) & 1).astype(np.uint8)
    plt.figure(figsize=(15, 3))
    plt.imshow(bits.T, aspect='auto', origin='lower', cmap='gray_r',
               interpolation='nearest')
    plt.ylabel('bit index (b0..b23)')
    plt.xlabel('sample index')
    plt.title(f'bit plane (-1dB, first {n} samples)')
    plt.tight_layout()
    plt.savefig(OUT / fname, dpi=110)
    plt.close()


def fig_phasemap(v, fname):
    pe = phase_energies(v)
    am = pe.argmax(axis=1)
    plt.figure(figsize=(15, 3))
    plt.imshow(np.log2(pe + 1).T, aspect='auto', origin='lower', cmap='viridis')
    plt.plot(np.arange(len(am)), am, 'r-', lw=1.0, label='argmax (coarse phase)')
    plt.ylabel('phase candidate (0..15)')
    plt.xlabel('sliding window index (step=128 samples)')
    plt.title('-1dB: 16-phase sliding-window energy (csq coarse search)')
    plt.legend(loc='upper right', fontsize=8)
    plt.colorbar(label='log2(window energy+1)')
    plt.tight_layout()
    plt.savefig(OUT / fname, dpi=110)
    plt.close()
    return am


def fig_tri(vs, fname):
    fig, axes = plt.subplots(3, 1, figsize=(15, 6.5), sharex=True)
    for ax, (tag, v) in zip(axes, vs):
        n = len(v) // 16 * 16
        M = v[:n].reshape(-1, 16)
        ax.imshow(np.log2(M + 1.0).T, aspect='auto', origin='lower', cmap='viridis')
        ax.set_ylabel('phase')
        ax.set_title(tag, fontsize=9)
    axes[-1].set_xlabel('time: 16-sample steps')
    plt.tight_layout()
    plt.savefig(OUT / fname, dpi=110)
    plt.close()


def main():
    print('=== ebuf 索引图像（输出到 model/out/dual_mc/viz/）===')
    v1 = one_frame(-1.0)
    p_pre, p_dat, ratio_pre, ratio_dat = fig_folded(
        v1, 'folded.png', 'ebuf energy folded map (-1dB): x=time(16-step), y=phase, color=log2(E)')
    print(f'[folded] 前导段最优相位列={p_pre}（列均值峰/均值={ratio_pre:.1f}x）; '
          f'数据段最优列={p_dat}（{ratio_dat:.1f}x）')
    fig_bitplane(v1, 'bitplane.png')
    print('[bitplane] 已出（高位块状 vs 低位雪花 = 结构与白噪的分层）')
    am = fig_phasemap(v1, 'phasemap.png')
    print(f'[phasemap] argmax 轨迹: 起点相位={am[0]}, 众数={np.bincount(am, minlength=16).argmax()}, '
          f'后 1/4 窗内众数={np.bincount(am[len(am) * 3 // 4:], minlength=16).argmax()}')
    vs = [(f'snr20', one_frame(20.0)), ('-1dB', v1), ('-8dB', one_frame(-8.0))]
    fig_tri(vs, 'snr_tri.png')
    print('[snr_tri] 已出（三 SNR 折叠对比）')


if __name__ == '__main__':
    main()
