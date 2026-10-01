# -*- coding: utf-8 -*-
"""run_multichannel.py —— 多通道并行接收：每通道自带消旋器

对照三种接收形态（同一条帧流、同一份 IQ）：
  A. 单通道·固定     一个消旋器, hint 固定给主群 —— 次群/离群永久降级
  B. 单通道·调度     一个消旋器 + observer 时分调度（run_multidev_scene 的形态）
  C. 三通道·并行     每个分辨单元一个通道,**各自带消旋器**, 全时覆盖

"谁规定只能一个消旋器" —— 单通道架构当然只有一个；多通道架构里每个通道
自带消旋器（假设殊异 ⇒ 消旋值不同 ⇒ 必须各自持有），后级按 FCS 选优。
共享的是前端（ADC/MF）,不共享的是消旋器及其后的解调链。

运行: python model/experiments/run_multichannel.py [--frames 60]
输出: model/out/multichannel/report.md  (+ 对比柱状图)
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

import _common
import chains
from baseband import impairments as imp

PSDU = bytes(range(20))
SNR_DB = 18.0
DEVS = [("devA", 50e3, 0.55), ("devB", 150e3, 0.30), ("devC", -200e3, 0.15)]
CHANNELS = [50e3, 150e3, -200e3]        # 三个通道各自的假设（observer 已锁定的状态）
GUARD_HZ = 20e3                          # 残余超过它不可能解出（跳过以省时间）


def gen_stream(n, rng):
    stream = []
    while len(stream) < n:
        d = DEVS[int(rng.choice(len(DEVS), p=[x[2] for x in DEVS]))]
        stream.extend([d] * max(1, int(rng.geometric(1.0 / 3.0))))
    return stream[:n]


def run_one(stream, hints, rng):
    """hints[i] 给出第 i 帧可用的消旋假设集合（单通道为单元素）。"""
    ok = []
    for (dev, cfo, _w), hs in zip(stream, hints):
        good = False
        for h in hs:
            if abs(cfo - h) > GUARD_HZ:
                continue
            sig = chains.simulate(PSDU, [imp.cfo_stage(cfo - h), chains.awgn(SNR_DB, rng)],
                                  sync="honest", deframe=True)
            if not bool(sig.meta.get("sync_fail")):
                good = True
                break                      # 早退：一个通道成功就够
        ok.append(good)
    return np.array(ok)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=60)
    args = ap.parse_args()
    rng = np.random.default_rng(2026)
    stream = gen_stream(args.frames, rng)

    # A. 单通道·固定（hint 永远给主群）
    okA = run_one(stream, [[CHANNELS[0]]] * len(stream), rng)
    # C. 三通道·并行
    okC = run_one(stream, [CHANNELS] * len(stream), rng)

    def per_dev(ok):
        out = []
        for dev, cfo, _w in DEVS:
            idx = [i for i, s in enumerate(stream) if s[0] == dev]
            if not idx:
                out.append((dev, 0.0))
                continue
            out.append((dev, float(ok[idx].mean())))
        return out

    pA, pC = per_dev(okA), per_dev(okC)
    overall = (okA.mean(), okC.mean())

    lines = [f"# 多通道并行接收（{args.frames} 帧, SNR {SNR_DB:.0f} dB）\n",
             "同一帧流、同一份 IQ 的三种接收形态对比（真实链路，honest 同步）：\n",
             "| 形态 | 消旋器数 | 每设备覆盖率（A/B/C） | 整体 |",
             "|---|---|---|---|",
             f"| A 单通道·固定 | 1 | {pA[0][1]:.0%} / {pA[1][1]:.0%} / {pA[2][1]:.0%} | {overall[0]:.0%} |",
             f"| C 三通道·并行 | 3 | {pC[0][1]:.0%} / {pC[1][1]:.0%} / {pC[2][1]:.0%} | {overall[1]:.0%} |",
             "",
             "（B 单通道·调度见 `run_multidev_scene`：覆盖率受时分限制，主群 80% / 次群 20% / 离群 25%）",
             "",
             "**结论**：每通道自带消旋器后，三个分辨单元**全时覆盖**——",
             "'单消旋器容量限制'随架构消失，不是原理约束。",
             "代价：消旋器及其后的解调链 ×N（前端 ADC/MF 仍共享）。"]

    plt = _common.init()
    fig, ax = plt.subplots(figsize=(7, 4))
    x = np.arange(len(DEVS))
    ax.bar(x - 0.2, [v for _d, v in pA], width=0.38, label="单通道·固定（1 消旋器）")
    ax.bar(x + 0.2, [v for _d, v in pC], width=0.38, label="三通道·并行（各带消旋器）")
    ax.set_xticks(x)
    ax.set_xticklabels([f"{d}\n{c/1e3:+.0f} kHz" for d, c, _w in DEVS], fontsize=9)
    ax.set_ylabel("帧覆盖率")
    ax.set_ylim(0, 1.08)
    ax.legend(fontsize=8)
    ax.set_title(f"每通道自带消旋器 vs 单消旋器（{args.frames} 帧）", fontsize=10)
    ax.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    OUT = _common.out_dir("multichannel")
    fig.savefig(OUT / "compare.png", dpi=130)
    (OUT / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\n图: {OUT/'compare.png'}")


if __name__ == "__main__":
    main()
