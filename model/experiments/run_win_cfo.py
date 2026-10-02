#!/usr/bin/env python
"""win_cfo.py —— "多少点够用"实证: 前导窗长 vs CFO 估计精度。

设计: 对每帧先用真值 CFO 消旋（模拟"粗级已锁"，消除模糊），再测残余估计误差。
  方法 A（固定基线 D=256 采样, 重叠累加）: 点数 N 增大 → 噪声误差 ↓（~√N）
  方法 B（单对, 基线 D = N/2）: 点数 N 增大 → 分辨率 ↑（~1/N）且残余小无模糊
数据: snr20（60 帧, ±100k 交替）与 snr6（60 帧）。
"""
import sys, json
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'tb' / 'rx_chain_e2e'))
sys.path.insert(0, str(ROOT / 'model'))
import phy_802154 as phy

FS = 16e6
D_A = 256          # 方法 A 的固定基线（采样）
NS = [256, 512, 1024, 2048]


def load(stream):
    d = ROOT / f'model/out/dual_mc/{stream}'
    raw = np.fromfile(d / 'mem.bin', dtype='>u4')
    i = (raw & 0xFFF).astype(np.int64)
    q = ((raw >> 12) & 0xFFF).astype(np.int64)
    i = np.where(i >= 2048, i - 4096, i); q = np.where(q >= 2048, q - 4096, q)
    mf = phy.matched_filter(i + 1j * q)
    fs = np.load(d / 'frames.npz')['frame_start'].astype(np.int64)
    meta = json.load(open(d / 'meta.json'))
    cf = np.resize(np.asarray(meta['cfo_per_frame'], float), len(fs))
    return mf, fs, cf


def derot(y, f, t0):
    t = (np.arange(len(y)) + t0) / FS
    return y * np.exp(-2j * np.pi * f * t)


def est_f(y, D):
    r = np.sum(y[D:] * np.conj(y[:-D]))
    return float(np.angle(r)) / (2 * np.pi * D / FS)


print(f"{'流':>6} {'N':>5} | {'方法A(τ=256) 中位误差':>20} | {'方法B(τ=N/2) 中位误差':>20}")
for stream in ('snr20', 'snr6'):
    mf, fs, cf = load(stream)
    for N in NS:
        errsA, errsB = [], []
        for f0, c in zip(fs, cf):
            seg = mf[int(f0): int(f0) + N]
            if len(seg) < N:
                continue
            yd = derot(seg, c, int(f0))
            # 方法 A：固定基线 256 的整段累加
            if N > D_A:
                errsA.append(abs(est_f(yd, D_A)))
            # 方法 B：单对，基线 = N/2
            errsB.append(abs(est_f(yd, N // 2)))
        mA = np.median(errsA) if errsA else float('nan')
        mB = np.median(errsB) if errsB else float('nan')
        print(f"{stream:>6} {N:>5} | {mA/1e3:>17.2f} kHz | {mB/1e3:>17.2f} kHz")
