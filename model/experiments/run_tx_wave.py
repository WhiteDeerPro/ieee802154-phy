#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""run_tx_wave.py —— 发射波形检查（"直接发射的波形"的数字域版本）。

数据: tb/tx_framer 的 tx_chain bit-true 测试导出（model/out/tx_wave/tx_iq_rtl.npy）。
产出: 时域片段 / 符号包络 / 频谱(含 -3dB/-20dB 带宽) / I-Q 轨迹 + report.md。
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / 'model'))
import _common            # noqa: E402

plt = _common.init()
OUT = _common.out_dir('tx_wave')
FS = 16e6

iq = np.load(OUT / 'tx_iq_rtl.npy')
i_got, q_got = iq[0].astype(float), iq[1].astype(float)
meta = {}
for line in (OUT / 'tx_iq_meta.txt').read_text().splitlines():
    if '=' in line:
        k, v = line.split('=', 1)
        meta[k] = v
Li, Lq = int(meta.get('Li', 0)), int(meta.get('Lq', 0))

# 对齐到帧起点（I/Q 各自偏移）
n = min(len(i_got) - Li, len(q_got) - Lq, 20000)
I = i_got[Li:Li + n]
Q = q_got[Lq:Lq + n]
z = I + 1j * Q

fig, axes = plt.subplots(4, 1, figsize=(11, 12))

# ① 时域片段（前导 512 采样）
ax = axes[0]
pre = slice(64, 64 + 512)
ax.plot(np.arange(512) / 8.0, I[pre], lw=0.9, label='I')
ax.plot(np.arange(512) / 8.0, Q[pre], lw=0.9, alpha=.7, label='Q')
ax.set_xlabel('码片数（1 码片=8 采样）'); ax.set_ylabel('幅度')
ax.set_title('① 发射波形（前导段 64 采样偏移处 512 采样 = 2 码片组）')
ax.grid(alpha=.3); ax.legend()

# ② 符号包络（每 8 采样取 |z|，展宽视图）
ax = axes[1]
env = np.abs(z)
ax.plot(np.arange(len(env)) / 8.0, env, lw=0.6)
ax.set_xlabel('码片数'); ax.set_ylabel('|z|')
ax.set_title('② 采样包络（半正弦脉冲流, 每码片一峰）')
ax.grid(alpha=.3)
ax.set_xlim(0, min(len(env) / 8, 700))

# ③ 频谱（Welch 近似：整段 FFT + 平滑）
ax = axes[2]
seg = z[:min(len(z), 16384)]
w = np.hanning(len(seg))
X = np.fft.fftshift(np.abs(np.fft.fft(seg * w)))
f = np.fft.fftshift(np.fft.fftfreq(len(seg), 1 / FS))
P = 20 * np.log10(X / X.max() + 1e-12)
ax.plot(f / 1e6, P, lw=0.8)
ax.set_xlim(-6, 6); ax.set_ylim(-80, 5)
ax.set_xlabel('频率 (MHz), 0 = 基带中心'); ax.set_ylabel('dB')
ax.set_title('③ 发射频谱（半正弦 O-QPSK; 标注 -3/-20 dB 带宽）')
ax.grid(alpha=.3)
# 带宽测量（主瓣）
pos = P >= -3
w3 = (f[pos].max() - f[pos].min()) / 1e6 if pos.any() else float('nan')
pos20 = P >= -20
w20 = (f[pos20].max() - f[pos20].min()) / 1e6 if pos20.any() else float('nan')
ax.axhline(-3, color='r', ls='--', lw=.7); ax.axhline(-20, color='r', ls='--', lw=.7)
ax.text(3.2, -3, f'-3dB 带宽 {w3:.2f} MHz', color='r', fontsize=9)
ax.text(3.2, -20, f'-20dB 带宽 {w20:.2f} MHz', color='r', fontsize=9)

# ④ I-Q 轨迹（前导段）
ax = axes[3]
seg2 = slice(64, 64 + 2048)
ax.plot(z.real[seg2], z.imag[seg2], lw=0.5)
ax.set_xlabel('I'); ax.set_ylabel('Q')
ax.set_title('④ I-Q 轨迹（前导段：半正弦过渡弧）')
ax.grid(alpha=.3); ax.axis('equal')

fig.suptitle(f"TX 波形检查（RTL bit-true 导出, PSDU={meta.get('PSDU','?')[:16]}…）")
fig.tight_layout()
fig.savefig(OUT / 'tx_wave_check.png', dpi=110)

(OUT / 'report.md').write_text(
    "# TX 发射波形检查（数字域）\n\n"
    f"- 数据: tb/tx_framer 的 tx_chain bit-true 测试导出（RTL 采样, 已与黄金逐位匹配）\n"
    f"- PSDU: `{meta.get('PSDU','?')}`; 符号数 {meta.get('n_sym','?')}; "
    f"I/Q 对齐偏移 {Li}/{Lq}\n"
    f"- FCS 符号(末 4): {meta.get('fcs_sym_bytes','?')}\n"
    f"- 带宽: **-3dB {w3:.2f} MHz / -20dB {w20:.2f} MHz**（基带, 采样率 16 MHz）\n\n"
    "## 图\n- `tx_wave_check.png`: ①时域 ②包络 ③频谱 ④I-Q 轨迹\n\n"
    "## 说明\n"
    "- 该波形即 RTL 发射链（tx_framer+oqpsk_modulator）数字输出；bit-true 已对拍黄金模型,\n"
    "  本检查提供直观印证与谱宽测量；**模拟域质量（EVM/杂散/掩模）需在 analog 侧验证**。\n")
print(f"[tx_wave] -> {OUT}/report.md + tx_wave_check.png")
print(f"带宽: -3dB {w3:.2f} MHz, -20dB {w20:.2f} MHz")
