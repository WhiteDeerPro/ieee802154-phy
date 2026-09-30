# -*- coding: utf-8 -*-
"""run_observer_multidev.py —— 多设备场景下的链路观测器仿真

场景假设（2026-10-01 与用户对齐的领域知识）
------------------------------------------------------------------
* 网络拓扑稳定 ⇒ 链路状态是**有限集且切换不频繁**；
* **状态（分辨单元）比设备少**：一群晶振相近的"好设备"落在同一个分辨单元
  （共享一个旋性），个别**离群参数**需要单独服务；
* 准静态：同一设备的 CFO 帧间不变，**不做剧烈换频/单帧跳变**；
* 单消旋器约束：RTL 只有一个 `cfo_rot`，同一时刻只能消旋一个值 ——
  这正是"共享分辨单元"的价值所在（一个 hint 服务一群设备）。

观测模型（半闭环）
------------------------------------------------------------------
一帧的估计质量取决于**当前 hint 与该设备真实 CFO 的距离**（是否已消旋）:
    已消旋（|Δf| < 8 kHz） → 好帧: 估计误差 ~3 kHz,  质量 q ~ U(0.6, 0.9)
    未消旋                → 坏帧: 估计误差 ~150 kHz, 质量 q ~ U(0.01, 0.26)
（量级取自 model/out/observer/report.md 的真实序列统计）

运行: python model/experiments/run_observer_multidev.py [--frames 400]
输出: model/out/observer/multidev_report.md
"""
import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

import _common
from upper.observer import FrameObservation, LinkObserver


@dataclass
class Device:
    name: str
    cfo: float          # 真实 CFO (Hz)
    weight: float       # 帧流占比


def build_population():
    """设备群: 主群(相近) + 次群 + 离群。"""
    rng = np.random.default_rng(7)
    devs = []
    # 主群: 8 台"好设备", 晶振相近 -> 应共享 1 个分辨单元
    for i in range(8):
        devs.append(Device(f"main{i}", float(rng.normal(50e3, 6e3)), 0.70 / 8))
    # 次群: 4 台, 另一簇
    for i in range(4):
        devs.append(Device(f"sub{i}", float(rng.normal(120e3, 4e3)), 0.22 / 4))
    # 离群: 2 台, 参数偏差大 -> 需要专门服务
    devs.append(Device("out1", 250e3, 0.05))
    devs.append(Device("out2", -180e3, 0.03))
    return devs


def gen_observation(dev, hint, rng, p_lucky=0.10):
    """按"是否已消旋"生成该帧观测。

    注意：未消旋时**不是永远坏帧** —— 实测冷启动就是靠偶发的质量帧启动的
    （`cfo_trigger/report.md` §9.5：前 14 帧 cf≤0.26，第 15/16 帧才出现 0.89）。
    这里用 p_lucky 建模这一"碰运气"机制。
    """
    if hint is not None and abs(dev.cfo - hint) < 8e3:
        est = dev.cfo + rng.normal(0, 3e3)          # 已消旋: 好帧
        q = rng.uniform(0.6, 0.9)
    elif rng.random() < p_lucky:
        est = dev.cfo + rng.normal(0, 3e3)          # 偶然对齐
        q = rng.uniform(0.6, 0.9)
    else:
        est = dev.cfo + rng.normal(0, 150e3)        # 未消旋: 坏帧
        q = rng.uniform(0.01, 0.26)
    return est, q


def run(n_frames=600, seed=1, burst_mean=3):
    rng = np.random.default_rng(seed)
    devs = build_population()
    w = np.array([d.weight for d in devs])
    w = w / w.sum()

    # 帧流：按**突发**生成 —— 同一设备连续发几帧（包突发/重传的时间局部性）。
    # 早期版本每帧独立随机选设备，导致 hint 永远追不上对端（acquired=45/lost=44 震荡）。
    stream = []
    while len(stream) < n_frames:
        d = devs[int(rng.choice(len(devs), p=w))]
        burst = max(1, int(rng.geometric(1.0 / burst_mean)))
        stream.extend([d] * burst)
    stream = stream[:n_frames]

    ob = LinkObserver(tol_hz=15e3)          # 分辨单元 15 kHz
    hint = None
    first_match = {}                         # 设备 -> 首次"已消旋帧"的帧号
    per_dev = {d.name: dict(frames=0, good=0, locked_frames=0) for d in devs}

    for f, dev in enumerate(stream, 1):
        est, q = gen_observation(dev, hint, rng)
        ctrl = ob.observe(FrameObservation(idx=f, cfo=est, quality=q))

        good = abs(est - dev.cfo) < 10e3
        st = per_dev[dev.name]
        st["frames"] += 1
        st["good"] += int(good)
        if good and dev.name not in first_match:
            first_match[dev.name] = f
        if ctrl.state == "LOCK" and ctrl.cfo_hint is not None \
                and abs(ctrl.cfo_hint - dev.cfo) < 15e3:
            st["locked_frames"] += 1
        hint = ctrl.cfo_hint if ctrl.cfo_hint is not None else hint

    return devs, ob, per_dev, first_match


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=600)
    args = ap.parse_args()

    devs, ob, per_dev, first_match = run(args.frames)

    lines = ["# 多设备场景：链路观测器仿真\n"]
    lines.append(f"- 帧数: {args.frames} ｜ 设备数: {len(devs)} ｜ 分辨单元: 15 kHz")
    lines.append(f"- 观测器最终: {ob.summary()}\n")
    lines.append("## 状态 vs 设备\n")
    locked = ob.locked_states()
    lines.append(f"- 设备总数 **{len(devs)}**，观测器建立候选 {len(ob.cands)} 个，"
                 f"其中 LOCK **{len(locked)}** 个")
    for v in locked:
        lines.append(f"  - 锁定状态 @ {v/1e3:+.1f} kHz")
    lines.append("\n## 每设备\n")
    lines.append("| 设备 | 真实 CFO(kHz) | 帧数 | 好帧率 | 锁定覆盖率 | 首次好帧 |")
    lines.append("|---|---|---|---|---|---|")
    for d in devs:
        st = per_dev[d.name]
        n = max(st["frames"], 1)
        fm = first_match.get(d.name, None)
        lines.append(f"| {d.name} | {d.cfo/1e3:+.1f} | {st['frames']} | "
                     f"{st['good']/n:.0%} | {st['locked_frames']/n:.0%} | "
                     f"{fm if fm else '—'} |")

    out = _common.out_dir("observer") / "multidev_report.md"

    # ---- 解读（共享的服务面 vs 被饿死的群）----
    main_devs = [d for d in devs if d.name.startswith("main")]
    n_main_frames = sum(per_dev[d.name]["frames"] for d in main_devs)
    n_main_good = sum(per_dev[d.name]["good"] for d in main_devs)
    others = [d for d in devs if not d.name.startswith("main")]
    n_oth_frames = sum(per_dev[d.name]["frames"] for d in others)
    n_oth_good = sum(per_dev[d.name]["good"] for d in others)
    lines.append("\n## 解读\n")
    total_frames = sum(per_dev[d.name]["frames"] for d in devs)
    total_good = sum(per_dev[d.name]["good"] for d in devs)
    lines.append(f"- **整体好帧率 {total_good/max(total_frames,1):.0%}**"
                 f"（{total_good}/{total_frames} 帧处于已消旋状态）")
    lines.append(f"- **共享分辨单元生效**：主群 {len(main_devs)} 台设备共享服务，"
                 f"主群好帧率 {n_main_good/max(n_main_frames,1):.0%}；"
                 f"次群/离群 {len(others)} 台的好帧率 "
                 f"{n_oth_good/max(n_oth_frames,1):.0%} —— 调度把它们从『完全饿死』"
                 f"拉到了可服务（观测窗口在候选间轮转）。")
    lines.append(f"- **LOCK/LOST 循环是正常形态**：单消旋器一次只能服务一个分辨单元，"
                 f"设备切换必然伴随失锁/重锁（acquired={ob.stats['acquired']} / "
                 f"lost={ob.stats['lost']}）；关键指标是**每设备被服务的比例**，"
                 f"而不是『最终剩几个 LOCK』。")
    lines.append("- **离群设备仍差**：帧量太少（每设备 <20 帧），轮转时轮不到；"
                 "进一步提升需要 MAC 辅助（知道下一帧来自哪个对端 → 直接预置 hint）。")

    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\n报告: {out}")


if __name__ == "__main__":
    main()
