# -*- coding: utf-8 -*-
"""run_multidev_scene.py —— 三设备 × 三模式的帧通信场景（真实链路）

场景设计（对齐"网络里一群设备各有旋性"的现实）：
  devA  +50 kHz  帧量 55%   —— "主群"（一群晶振相近的设备，可共享一个分辨单元）
  devB +150 kHz  帧量 30%   —— "次群"
  devC -200 kHz  帧量 15%   —— "离群"（需要专门服务）

接收端：`LinkObserver` 管理模式（多状态 + 服务调度），每帧把 hint 经"残余频偏"
注入真实链路（chains），链路走 honest 同步 + 解帧。

输出（model/out/multidev_scene/scene.png）：
  帧流时间线 / 消旋成功与失败的眼图 / 星座 / 每设备统计

运行: python model/experiments/run_multidev_scene.py [--frames 120]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

import _common
import chains
import measure
from baseband import impairments as imp
from upper.observer import FrameObservation, LinkObserver

PSDU = bytes(range(20))
SNR_DB = 18.0
COVER_HZ = 8e3          # |真值 - hint| 小于它视为"已消旋"（观测质量模型, 由容限实测标定）
FULL_HZ = 2e3           # "完全覆盖"：实测无损边界（run_rtl_residual: δ≤2kHz 无损）
DEVS = [("devA", 50e3, 0.55), ("devB", 150e3, 0.30), ("devC", -200e3, 0.15)]


def gen_observation(cfo_true, hint, rng):
    """观测模型（量级取自容限实测）：已消旋→好帧；未消旋→坏帧，偶发好帧。"""
    residual = abs(cfo_true - (hint if hint is not None else 0.0))
    if residual < COVER_HZ or rng.random() < 0.10:
        return cfo_true + rng.normal(0, 3e3), rng.uniform(0.6, 0.9)
    return cfo_true + rng.normal(0, 150e3), rng.uniform(0.01, 0.26)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=120)
    args = ap.parse_args()

    rng = np.random.default_rng(2026)
    ob = LinkObserver(tol_hz=10e3, lost_run=6, serve_window=2)
    hint = None
    rec = []
    samples = {"ok": [], "bad": []}          # 眼图/星座取样

    # 帧流：按**突发**生成（时间局部性——同一设备连发 1~4 帧）。
    # 教训: 每帧独立随机时 hint 永远慢一拍（前一个多设备仿真踩过）。
    stream = []
    while len(stream) < args.frames:
        d = DEVS[int(rng.choice(len(DEVS), p=[x[2] for x in DEVS]))]
        stream.extend([d] * max(1, int(rng.geometric(1.0 / 3.0))))
    stream = stream[:args.frames]

    for i, (dev, cfo, _w) in enumerate(stream):
        residual = cfo - (hint if hint is not None else 0.0)
        sig = chains.simulate(
            PSDU, [imp.cfo_stage(residual), chains.awgn(SNR_DB, rng)],
            sync="honest", deframe=True)
        ok = not bool(sig.meta.get("sync_fail")) and bool(
            sig.meta.get("fcs_ok", True))

        est, q = gen_observation(cfo, hint, rng)
        ctrl = ob.observe(FrameObservation(idx=i, cfo=est, quality=q,
                                           sync_ok=ok, fcs_ok=ok))
        hint = ctrl.cfo_hint if ctrl.cfo_hint is not None else hint
        rec.append(dict(i=i, dev=dev, cfo=cfo, resid=residual, ok=ok,
                        state=ctrl.state))

        key = "ok" if abs(residual) < COVER_HZ else "bad"
        if len(samples[key]) < 3 and len(sig.rx) > 4096:
            samples[key].append((np.real(np.asarray(sig.rx))[3000:9000],
                                 np.asarray(sig.rx_chips)[:512]))

    # ---------------- 出图 ----------------
    plt = _common.init()
    fig, axes = plt.subplots(2, 3, figsize=(14, 7.4))

    # (0,0) 帧流时间线
    ax = axes[0][0]
    for j, (dev, _c, _w) in enumerate(DEVS):
        xs = [r["i"] for r in rec if r["dev"] == dev]
        oks = [r["ok"] for r in rec if r["dev"] == dev]
        ax.scatter([x for x, o in zip(xs, oks) if o], [j] * sum(oks),
                   marker="o", s=26, label=f"{dev} 成功")
        ax.scatter([x for x, o in zip(xs, oks) if not o], [j] * (len(oks) - sum(oks)),
                   marker="x", s=30, color="crimson")
    ax.set_yticks(range(len(DEVS)))
    ax.set_yticklabels([f"{d[0]}\n{d[1]/1e3:+.0f} kHz" for d in DEVS], fontsize=8)
    ax.set_xlabel("帧序号")
    ax.set_title("帧流时间线（o=解帧成功  x=失败）", fontsize=10)
    ax.grid(alpha=0.3)

    # (0,1)/(0,2) 眼图：消旋成功 vs 失败
    for col, key in ((1, "ok"), (2, "bad")):
        ax = axes[0][col]
        for y, _c in samples[key][:2]:
            E = measure.eye_data(np.asarray(y), 8, n_traces=140)
            for row in E:
                ax.plot(np.arange(8), row, alpha=0.18, lw=0.8,
                        color="tab:blue" if key == "ok" else "crimson")
        ax.set_title(f"眼图：{'已消旋' if key=='ok' else '未消旋(残余大)'}", fontsize=10)
        ax.grid(alpha=0.3)
        ax.set_xlabel("码片内采样")

    # (1,0)/(1,1) 星座对比
    for col, key in ((0, "ok"), (1, "bad")):
        ax = axes[1][col]
        for _y, chips in samples[key][:2]:
            ax.scatter(chips.real, chips.imag, s=7, alpha=0.4,
                       color="tab:blue" if key == "ok" else "crimson")
        lim = 8
        ax.set_xlim(-lim, lim)
        ax.set_ylim(-lim, lim)
        ax.set_title(f"码片软值：{'已消旋' if key=='ok' else '未消旋'}", fontsize=10)
        ax.set_aspect("equal")
        ax.grid(alpha=0.3)

    # (1,2) 每设备统计
    ax = axes[1][2]
    names = [d[0] for d in DEVS]
    rates = []
    for dev in names:
        sub = [r for r in rec if r["dev"] == dev]
        rates.append(100 * sum(r["ok"] for r in sub) / max(len(sub), 1))
    ax.bar(names, rates, color=["tab:green", "tab:olive", "tab:red"])
    for j, v in enumerate(rates):
        ax.text(j, v + 1, f"{v:.0f}%", ha="center", fontsize=9)
    ax.set_ylim(0, 108)
    ax.set_ylabel("解帧成功率 (%)")
    ax.set_title(f"每设备成功率（{args.frames} 帧，SNR {SNR_DB:.0f} dB）", fontsize=10)
    ax.grid(alpha=0.3, axis="y")

    fig.suptitle("三设备 × 三模式：链路观测器的多设备场景"
                 f"（observer 建立 {len(ob.cands)} 个状态，LOCK {len(ob.locked_states())}）",
                 fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    OUT = _common.out_dir("multidev_scene")
    fig.savefig(OUT / "scene.png", dpi=130)

    lines = [f"# 三设备场景（{args.frames} 帧, SNR {SNR_DB:.0f} dB）\n",
             f"- 观测器: {ob.summary()}",
             f"- 锁定状态: {[round(v/1e3,1) for v in ob.locked_states()]}",
             "",
             "分档口径（对齐 run_rtl_residual 实测）：残余 <2 kHz 无损 / 2-8 kHz 降级 / >8 kHz 失败；",
             "括号内为该档内的解帧成功率。\n",
             "| 设备 | 真实 CFO | 帧数 | 帧量占比 | <2kHz 无损 | 2–8kHz 降级 | >8kHz 失败 |",
             "|---|---|---|---|---|---|---|"]
    for dev, cfo, _w in DEVS:
        sub = [r for r in rec if r["dev"] == dev]
        n = max(len(sub), 1)
        b1 = [r for r in sub if abs(r["resid"]) < 2e3]
        b2 = [r for r in sub if 2e3 <= abs(r["resid"]) < 8e3]
        b3 = [r for r in sub if abs(r["resid"]) >= 8e3]
        def fmt(b):
            return f"{len(b)/n:.0%} ({sum(r['ok'] for r in b)/max(len(b),1):.0%})"
        lines.append(f"| {dev} | {cfo/1e3:+.0f} kHz | {len(sub)} | "
                     f"{len(sub)/max(len(rec),1):.0%} | {fmt(b1)} | {fmt(b2)} | {fmt(b3)} |")
    (OUT / "report.md").write_text("\n".join(lines), encoding="utf-8")

    print("\n".join(lines))
    print(f"\n图: {OUT/'scene.png'}")


if __name__ == "__main__":
    main()
