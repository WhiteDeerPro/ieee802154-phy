# -*- coding: utf-8 -*-
"""run_residual_viz.py —— 残余 CFO 的表征图：眼图 + 星座图

对三个残余频偏（0 / 5 / 15 kHz）各出一组图，直观展示"残余 CFO 如何吃掉链路"：
- 眼图（匹配滤波后波形折叠）：残余越大眼越闭；
- 星座图（解扩软值）：残余让星座点旋转、模糊。

运行: python model/experiments/run_residual_viz.py
输出: model/out/residual_viz/residual_viz.png
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

import _common
import chains
import measure
from baseband import impairments as imp

PSDU = bytes(range(20))
SNR_DB = 18.0
CASES = [(0.0, "δ=0（理想）"), (30e3, "δ=30 kHz（边缘）"), (60e3, "δ=60 kHz（崩）")]


def main():
    plt = _common.init()
    rng = np.random.default_rng(7)
    n = len(CASES)
    fig, axes = plt.subplots(2, n, figsize=(4.4 * n, 6.6))

    for j, (d, label) in enumerate(CASES):
        sig = chains.simulate(
            PSDU, [imp.cfo_stage(d), chains.awgn(SNR_DB, rng)],
            sync="genie", deframe=True)

        # ---- 眼图：复基带波形取实部（I 路），取中段避开帧边缘 ----
        y = getattr(sig, "rx", None)
        sps = 8
        if y is not None and len(y) > 4096:
            y = np.real(np.asarray(y, dtype=complex))[3000:9000]
            E = measure.eye_data(y, sps, n_traces=140)
            ax = axes[0][j]
            for row in E:
                ax.plot(np.arange(sps), row, color="tab:blue", alpha=0.22, lw=0.8)
            ax.set_title(f"眼图 {label}（I 路, 每码片 8 采样）", fontsize=10)
            ax.set_xlabel("码片内采样")
            ax.grid(alpha=0.3)

        # ---- 星座图：解扩软值 + 理想参考 ----
        soft = np.asarray(getattr(sig, "rx_soft", np.array([])), dtype=complex)
        ref = np.asarray(getattr(sig, "soft_ref", np.array([])), dtype=complex)
        ax = axes[1][j]
        if soft.size:
            ax.scatter(soft.real, soft.imag, s=9, alpha=0.45, label="接收软值")
        if ref.size:
            ax.scatter(ref.real, ref.imag, marker="+", color="k", s=42,
                       linewidths=1.0, label="理想参考")
        lim = max(1e-9, max(np.abs(soft).max() if soft.size else 1.0,
                            np.abs(ref).max() if ref.size else 1.0) * 1.2)
        ax.set_xlim(-lim, lim)
        ax.set_ylim(-lim, lim)
        ax.set_title(f"星座 {label}（解扩软值）", fontsize=10)
        ax.set_aspect("equal")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=7, loc="upper right")

    fig.suptitle("残余 CFO 如何吃掉链路：眼图与星座（SNR 18 dB，genie 定时）", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    OUT = _common.out_dir("residual_viz")
    fig.savefig(OUT / "residual_viz.png", dpi=130)

    # 量化：EVM / 眼高（判决点统计）
    lines = ["# 残余 CFO 表征（眼图 / 星座）\n",
             f"- SNR {SNR_DB} dB，genie 定时（隔离解调环节）\n",
             "| δ (kHz) | 星座点数 | |soft| 均值 |", "|---|---|---|"]
    for d, label in CASES:
        sig = chains.simulate(PSDU, [imp.cfo_stage(d), chains.awgn(SNR_DB, rng)],
                              sync="genie", deframe=True)
        soft = np.asarray(getattr(sig, "rx_soft", np.array([])), dtype=complex)
        if soft.size:
            lines.append(f"| {d/1e3:.0f} | {soft.size} | {np.abs(soft).mean():.3f} |")
    (OUT / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"图: {OUT/'residual_viz.png'}")


if __name__ == "__main__":
    main()
