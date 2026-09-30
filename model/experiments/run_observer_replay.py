# -*- coding: utf-8 -*-
"""run_observer_replay.py —— 用 RTL 仿真的估计序列回放链路观测器

输入：tb/rx_chain_e2e 的 events.txt（含 EST 事件）。
生成方式（见 model/out/cfo_trigger/report.md §5）：

    python tb/rx_chain_e2e/run_mc.py --snr-list=20 --cfo-list=100000 --frames 50 \
        --jobs 1 --top-chain --chipoff=3 --est-skip-t3=0 --trigext=0 --out-dir /tmp/mc_auto

对比两条路径：
  现状（RTL）  第一个 est_ok=1 的估计被采纳 —— 单帧硬判决
  观测器       质量加权证据累积 -> VERIFY -> LOCK —— 多帧软判决

运行: python model/experiments/run_observer_replay.py [/path/to/events.txt]
输出: model/out/observer/report.md
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import _common
from upper.observer import FrameObservation, LinkObserver

FS = 16e6             # 采样率
PHASE_FS = 2 ** 24    # phase_inc 满量程 = 2π

DEFAULT_EVENTS = Path("/tmp/mc_auto_obs/snrp20_cfo100k/events.txt")


def load_events(path):
    """解析 events.txt 的 EST 事件 -> [(k, cfo_hz, cf, ok), ...]

    字段: EST k phase_inc est_ok pbest (aa_i+aa_q) am
    质量 cf = (aa_i+aa_q)/am  （与 cfo_est 的质量门控同源）
    """
    out = []
    for line in Path(path).read_text().splitlines():
        p = line.split()
        if len(p) == 7 and p[0] == "EST":
            k = int(p[1])
            cfo = int(p[2]) / PHASE_FS * FS
            ok = int(p[3])
            num, den = int(p[5]), int(p[6])
            cf = num / den if den else 0.0
            out.append((k, cfo, cf, ok))
    return out


def main():
    ev_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EVENTS
    if not ev_path.exists():
        print(f"找不到 {ev_path}；请按文件头的命令先生成数据")
        sys.exit(1)

    ev = load_events(ev_path)
    true_cfo = 100e3      # 本次仿真的 CFO 真值
    print(f"载入 {len(ev)} 次 EST 估计（{ev_path}）")

    # ---- 路径 A: 现状（单帧硬判决: 第一个 est_ok=1 即采纳）----
    first_ok = next((i for i, e in enumerate(ev, 1) if e[3]), None)
    rtl_err = abs(ev[first_ok - 1][1] - true_cfo) if first_ok else None

    # ---- 路径 B: 观测器回放 ----
    ob = LinkObserver()
    states, hints = [], []
    for i, (_k, cfo, cf, _ok) in enumerate(ev, 1):
        c = ob.observe(FrameObservation(idx=i, cfo=cfo, quality=cf))
        states.append(c.state)
        hints.append(c.cfo_hint)
    first_lock = next((i for i, s in enumerate(states, 1) if s == "LOCK"), None)
    obs_err = abs(hints[first_lock - 1] - true_cfo) if first_lock else None

    # ---- 热启动演示：同设备第二段 ----
    ob2 = LinkObserver()
    warm = ob2.warm_start(ob.recall() or hints[-1] or true_cfo)
    warm_err = abs(warm.cfo_hint - true_cfo)

    # ---- 报告 ----
    lines = []
    lines.append("# 链路观测器回放：RTL 估计序列（CFO 真值 100 kHz）\n")
    lines.append(f"- 数据: `{ev_path}`（{len(ev)} 次估计）\n")
    lines.append("## 逐帧\n")
    lines.append("| # | cfo(kHz) | cf | est_ok | 观测器状态 | 观测器 hint(kHz) |")
    lines.append("|---|---|---|---|---|---|")
    for i, ((_k, cfo, cf, ok), s, h) in enumerate(zip(ev, states, hints), 1):
        hs = f"{h/1e3:+.1f}" if h is not None else "—"
        lines.append(f"| {i} | {cfo/1e3:+.1f} | {cf:.2f} | {'✓' if ok else '·'} | {s} | {hs} |")

    lines.append("\n## 对比\n")
    lines.append(f"- **现状（RTL，单帧硬判决）**: 第 {first_ok} 次估计 est_ok=1 即采纳 "
                 f"({ev[first_ok-1][1]/1e3:+.1f} kHz)，误差 **{rtl_err/1e3:.1f} kHz**")
    lines.append(f"- **观测器（多帧软判决）**: 第 {first_lock} 次估计 LOCK，"
                 f"锁定值 {hints[first_lock-1]/1e3:+.1f} kHz，误差 **{obs_err/1e3:.1f} kHz**")
    lines.append(f"- **热启动**: 用状态表直接 LOCK，误差 **{warm_err/1e3:.1f} kHz**（0 帧捕获）")
    lines.append(f"\n观测器统计: {ob.summary()}")

    out = _common.out_dir("observer") / "report.md"
    out.write_text("\n".join(lines), encoding="utf-8")

    print("\n".join(lines[-6:]))
    print(f"\n报告: {out}")


if __name__ == "__main__":
    main()
