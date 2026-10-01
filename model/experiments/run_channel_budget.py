# -*- coding: utf-8 -*-
"""run_channel_budget.py —— 通道数的 sweet point：边际覆盖 vs 边际成本

模型
----
* 一个通道 = 一个分辨单元（宽 W，由容限实测定：无损 2×2=4 kHz、工程 10–20 kHz）；
* 设备群是**长尾**的（帧量 Zipf 递减），旋性分布 = 主簇 + 次簇 + 离群；
* 通道分配：贪心——每次选一个 hint 位置，覆盖当前未覆盖帧量最大者；
* 覆盖率 = 被覆盖设备的帧量权重之和（通道 hint 取设备真值，故覆盖即全时服务）。

输出：N 通道 → 覆盖率与边际增益，看拐点在哪。
运行: python model/experiments/run_channel_budget.py
输出: model/out/channel_budget/{report.md, curve.png}
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

import _common

# 设备群：旋性 + 帧量权重（主簇 / 次簇 / 离群 / 长尾）
DEVS = [
    (52e3, 0.28), (47e3, 0.20), (58e3, 0.13), (44e3, 0.07),   # 主簇（4 台）
    (151e3, 0.11), (144e3, 0.06),                              # 次簇（2 台）
    (-200e3, 0.08),                                            # 离群 1
    (95e3, 0.05), (240e3, 0.02),                               # 长尾 2
]
W_LIST = [10e3, 16e3, 24e3]      # 分辨单元宽（容限实测：10-20 kHz 工程口径）


def greedy(n_max, W):
    """贪心分配通道，返回 (通道数, 累计覆盖率, 边际增益)。"""
    cov, hints = set(), []
    rows = []
    for n in range(1, n_max + 1):
        best, gain = None, -1.0
        for cfo, _w in DEVS:
            if cfo in cov:
                continue
            g = sum(w for c2, w in DEVS if abs(c2 - cfo) < W / 2 and c2 not in cov)
            if g > gain:
                best, gain = cfo, g
        if best is None:
            break
        hints.append(best)
        for c2, _w in DEVS:
            if abs(c2 - best) < W / 2:
                cov.add(c2)
        total = sum(w for c2, w in DEVS if c2 in cov)
        rows.append((n, total, max(gain, 0.0), best))
    return rows


def main():
    plt = _common.init()
    lines = ["# 通道数预算：边际覆盖 vs 边际成本\n",
             "设备群 = 主簇 4 台（帧量 68%）+ 次簇 2 台（17%）+ 离群 1 台（8%）+ 长尾 2 台（7%）",
             "（帧量取 Zipf 递减；一个通道 = 一个分辨单元）\n"]
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    for W in W_LIST:
        rows = greedy(len(DEVS), W)
        ns = [r[0] for r in rows]
        cov = [r[1] * 100 for r in rows]
        ax.plot(ns, cov, "o-", label=f"分辨单元 W={W/1e3:.0f} kHz")
        lines.append(f"\n## W = {W/1e3:.0f} kHz\n")
        lines.append("| 通道数 | 累计覆盖 | 该通道边际增益 | 服务位置 |")
        lines.append("|---|---|---|---|")
        for n, c, g, pos in rows:
            lines.append(f"| {n} | {c:.0%} | +{g:.0%} | {pos/1e3:+.0f} kHz |")
        # 拐点：边际增益首次 < 10%
        knee = next((n for n, _c, g, _p in rows if g < 0.10), None)
        lines.append(f"\n**拐点**（边际增益 <10%）：第 **{knee}** 个通道起收益明显变薄。")

    ax.set_xlabel("通道数（= 消旋器数）")
    ax.set_ylabel("帧量加权覆盖率 (%)")
    ax.set_title("通道数的 sweet point：长尾设备群下的覆盖曲线", fontsize=11)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=9)
    fig.tight_layout()
    OUT = _common.out_dir("channel_budget")
    fig.savefig(OUT / "curve.png", dpi=130)
    (OUT / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\n图: {OUT/'curve.png'}")


if __name__ == "__main__":
    main()
