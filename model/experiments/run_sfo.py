# -*- coding: utf-8 -*-
"""
run_sfo.py —— 采样率偏差 (SFO) 扫描: BER vs ppm × 帧长
=====================================================
验证「短包内定时漂移忽略」先验 (docs/03): 帧内采样点线性漂移,
帧尾漂移 = ppm × 帧时长。短帧 (20B) vs 长帧 (127B, 最坏 4.25 ms)。

链路 (chains 库组装, 见 model/chains.py; SFO 作用于发送波形且在 AWGN 之前,
顺序不可交换):
  载荷 → 组帧(完整链) → 扩频 → 成形 → SFO → AWGN → MF → 窗口同步 → 采样 → 解扩

运行: python model/experiments/run_sfo.py  →  out/impairments/imp_sfo.png + sfo_results.csv
"""
import csv
import sys
from pathlib import Path

# 本脚本位于 model/experiments/ —— 库模块 (chains / measure / phy_802154 / visualize)
# 在上一级的 model/, 而 Python 只自动把脚本自身目录加入 sys.path, 故显式引导。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

import _common
import chains
import measure
import phy_802154 as phy
from baseband import impairments as imp

plt = _common.init()                          # Agg 后端 + 中文字体
OUT = _common.out_dir("impairments")          # model/out/impairments/（已创建）

SNR_DB = -1.0          # 解调需求工作点
N_FRAMES = 400
PPM_GRID = [0, 10, 20, 40, 80, 160]
LENS = [20, 127]
SPS = phy.SPS
FS = 16e6
rng = np.random.default_rng(2026)   # 模块级 rng: 跨扫参点复用、不重置 (保持与既有结果可比)


def build_chain(ppm):
    """本实验的链路: 完整链 + SFO + AWGN + 帧首窗口同步 [0, 8·SPS−1]。

    impairments 顺序即作用顺序 (SFO 在 AWGN 之前); 链路对象在扫参点内复用
    (阶段无状态; rng 由闭包持有, 保证与迁移前逐帧同序可复现)。
    """
    return chains.link([imp.sfo_stage(ppm), chains.awgn(SNR_DB, rng)],
                       full=True, sync="window", win=8 * SPS - 1)


def sim(L, ppm):
    """单点仿真: 返回 BER = 误码比特 / 总比特 (口径: measure.frame_bit_errors)。"""
    chain = build_chain(ppm)
    err = tot = 0
    for _ in range(N_FRAMES):
        psdu = bytes(rng.integers(0, 256, size=L).tolist())
        sig = chain.run(payload=psdu)
        e, t = measure.frame_bit_errors(sig)
        err += e
        tot += t
    return err / tot


print(f"Chip SNR = {SNR_DB} dB, {N_FRAMES} frames/point")
rows = []
for L in LENS:
    n_sym = 10 + 2 + 2 * L + 4
    dur = n_sym * 32 * SPS / FS
    for ppm in PPM_GRID:
        ber = sim(L, ppm)
        drift = dur * ppm / 1e6 * FS      # 帧尾采样漂移 (采样数)
        rows.append((L, ppm, ber, dur, drift))
        print(f"PSDU {L:>3}B (frame {dur*1e3:5.2f} ms) @ {ppm:>4} ppm: "
              f"BER={ber:.2e}, tail drift {drift:.2f} samples ({drift/SPS:.3f} chips)")

with open(OUT / "sfo_results.csv", "w", newline="") as f:
    wcsv = csv.writer(f)
    wcsv.writerow(["psdu_len", "ppm", "ber", "frame_ms", "tail_drift_samp"])
    wcsv.writerows(rows)

fig, ax = plt.subplots(figsize=(10, 6))
for L, c in zip(LENS, ["tab:blue", "tab:red"]):
    xs = [r[1] for r in rows if r[0] == L]
    ys = [r[2] for r in rows if r[0] == L]
    ax.semilogy(xs, ys, "o-", ms=5, lw=1.2, color=c, label=f"PSDU {L} B")
ax.set_xlabel("SFO (ppm, relative TX/RX sample-clock offset)")
ax.set_ylabel("BER")
ax.set_title(f"SFO sweep (chip SNR {SNR_DB} dB): short frame vs long frame")
ax.grid(alpha=0.3)
ax.legend()
ax.axhline(1e-3, color="gray", ls=":", lw=1)
ax.text(155, 1.1e-3, "1e-3", fontsize=8, ha="right")
out = OUT / "imp_sfo.png"
fig.savefig(out, dpi=130)
print(f"\nsaved: {out}")
