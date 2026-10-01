#!/usr/bin/env python
"""run_snr_est.py —— "能否 estSNR"验证：能量法 SNR 估计（离线版 = RTL 的 pwr/nse 两量）

方法: SNR_est = P_frame / P_noise − 1（线性→dB）
  P_frame = 帧内窗平均功率（信号+噪声）
  P_noise = 帧前空档噪声估计——两种：均值窗（脆）vs 低分位（鲁棒，对应 RTL 的 nse min-tracking）

用途: 验证"按 SNR 自适应调虚警模式"的可行性（只需要"档位可分"，不需绝对值准）。
数据: model/out/dual_mc/chain_report（4 设备 × 3 档 SNR 混流）。

结论（2026-10-02）:
  - 三档可分: 中位 3.7 / -2.0 / -7.5；档间间距 5.7 / 5.6 dB（标签差 8 / 6，偏移为口径常数）；
  - 均值窗会被帧尾拖尾污染（dev1@12dB 出现 -90 离群）；低分位估计稳定 → 印证
    RTL min-tracking（下包络）的正确性；
  - 结论: 能估 SNN_est = pwr/nse − 1（同源），足够驱动 γ/CONF_CNT 的模式选择（慢变、跨帧）。

用法: python model/experiments/run_snr_est.py
"""
import numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def load_smp(path):
    v = np.fromfile(path, dtype='>u4')
    i = (v & 0xFFF).astype(np.int32); i = np.where(i >= 2048, i - 4096, i)
    q = ((v >> 12) & 0xFFF).astype(np.int32); q = np.where(q >= 2048, q - 4096, q)
    return i.astype(np.float64), q.astype(np.float64)

d = ROOT / 'model/out/dual_mc/chain_report'
i, q = load_smp(d / 'mem.bin')
fs = np.load(d / 'frames.npz')['frame_start'].astype(int)
pwr = i * i + q * q
nfr = len(fs)
TAG = [20.0, 12.0, 6.0]

est_mean = {0: [], 1: [], 2: []}
est_q20 = {0: [], 1: [], 2: []}
for f in range(1, nfr):
    idx = f % 12; tier = idx // 4
    lo = int(fs[f])
    P_fr = pwr[lo + 3000:lo + 12000].mean()
    gap = pwr[lo - 1200:lo - 200]
    for store, P_n in ((est_mean, gap.mean()),
                       (est_q20, np.percentile(gap, 20))):
        store[tier].append(10 * np.log10(max(P_fr / P_n - 1.0, 1e-9)))

print('SNR 估计（能量法）——两种噪声窗对比')
print(f'{"SNR档":>6} | {"均值窗: 中位 (IQR)":>28} | {"低分位窗: 中位 (IQR)":>28}')
for t in range(3):
    a = np.array(est_mean[t]); b = np.array(est_q20[t])
    print(f'{TAG[t]:>5}dB | {np.median(a):>7.1f} ({np.percentile(a,25):>7.1f},{np.percentile(a,75):>7.1f}) '
          f'| {np.median(b):>7.1f} ({np.percentile(b,25):>7.1f},{np.percentile(b,75):>7.1f})')

gm = [np.median(est_mean[0]) - np.median(est_mean[1]), np.median(est_mean[1]) - np.median(est_mean[2])]
gq = [np.median(est_q20[0]) - np.median(est_q20[1]), np.median(est_q20[1]) - np.median(est_q20[2])]
print(f'\n档间间距 均值窗: {gm[0]:.1f}/{gm[1]:.1f} dB   低分位窗: {gq[0]:.1f}/{gq[1]:.1f} dB  （标签差 8/6）')
