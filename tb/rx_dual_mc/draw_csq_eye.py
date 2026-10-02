#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""draw_csq_eye.py —— 从 +EYEDUMP 的 MF 采样画"16 相位结构"图（Csq 粗搜依据）。

用法: python tb/rx_dual_mc/draw_csq_eye.py <dump.txt> <frames.npz> <cfof_hz> <out.png> [frame_idx]
输出: ① 16 相位能量曲线（Csq 能量窗的形态: 两峰=片对格点）
      ② |mf| 折叠散点（16 相位持久图）
      ③ 去旋后 I 分量折叠散点
"""
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = ["Noto Sans CJK SC", "Noto Sans CJK TC",
                                   "Noto Serif CJK SC", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

dump, frz, cfof, outp = sys.argv[1], sys.argv[2], float(sys.argv[3]), sys.argv[4]
fidx = int(sys.argv[5]) if len(sys.argv) > 5 else 0

raw = np.loadtxt(dump, dtype=np.int64)
k = raw[:, 0]
z = raw[:, 1] + 1j * raw[:, 2]
z = z * np.exp(-2j * np.pi * cfof * k / 16e6)          # 采样级理想消旋
fs = np.load(frz)["frame_start"].astype(np.int64)
f0 = int(fs[fidx])
seg = (k >= f0 + 256) & (k < f0 + 2048)                 # 前导中段（避开边界）
kk, zz = k[seg], z[seg]
ph = kk % 16

E = np.array([np.sum(np.abs(zz[ph == p]) ** 2) for p in range(16)])
E = E / E.max()

fig, axes = plt.subplots(3, 1, figsize=(9, 10))
ax = axes[0]
ax.plot(range(16), E, "o-", color="C0")
top = np.argsort(E)[::-1][:4]
ax.set_xticks(range(16))
ax.set_xlabel("采样相位 p（mod 16）"); ax.set_ylabel("归一化能量 E[p]")
ax.set_title("① 16 相位能量（前导段）——片对格点应为两峰\n"
             f"top4 相位 = {sorted(top.tolist())}")
ax.grid(alpha=.3)

ax = axes[1]
ax.scatter(ph, np.abs(zz), s=1, alpha=.15, color="C1")
ax.set_xlabel("采样相位 p"); ax.set_ylabel("|mf|")
ax.set_title("② |mf| 折叠持久图（16 相位）")
ax.grid(alpha=.3)

ax = axes[2]
ax.scatter(ph, zz.real, s=1, alpha=.15, color="C2")
ax.set_xlabel("采样相位 p"); ax.set_ylabel("Re(mf)（去旋后）")
ax.set_title("③ I 分量折叠散点（去旋后）")
ax.grid(alpha=.3)

fig.suptitle(f"Csq 粗搜的 16 相位结构（帧 f0={f0}, CFO={cfof/1e3:.0f} kHz）")
fig.tight_layout()
Path(outp).parent.mkdir(parents=True, exist_ok=True)
fig.savefig(outp, dpi=110)
print("[draw_csq_eye] ->", outp)
print("E[p] =", " ".join(f"{x:.2f}" for x in E))
