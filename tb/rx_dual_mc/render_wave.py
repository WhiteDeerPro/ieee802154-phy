#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""render_wave.py —— 把 dual_mc_tb 的 +VCD 波形渲染成时序图（PNG）。

用法: python tb/rx_dual_mc/render_wave.py <dut.vcd> <out.png> [t0 t1]
默认展示同步器内部关键信号（Csq/原版通用）:
  i_in / state / c_e(或 pmax) / pcnt(或 mcnt) / accR / accI / detect / locked_phase
VCD 由 tb 的 +VCD=<path> 生成（scope = dut.u_fe.g_scan.u_sync）。
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

WANT = ["i_in", "q_in", "state", "c_e", "pmax", "pcnt", "mcnt", "ewcnt",
        "smp_cnt", "accR", "accI", "serve", "detect", "locked_phase", "ready"]


def parse_vcd(path):
    """返回 {name: [(t, value_int), ...]}（只收 WANT 里的信号, 只留变化点）。"""
    sig_ids = {}
    with open(path) as f:
        # header
        for line in f:
            line = line.strip()
            if line.startswith("$var"):
                p = line.split()
                # $var wire <w> <id> <name> [range] $end
                try:
                    width = int(p[2]); vid = p[3]; name = p[4]
                except (IndexError, ValueError):
                    continue
                if name in WANT:
                    sig_ids[vid] = name
            elif line.startswith("$enddefinitions"):
                break
        data = {n: [] for n in sig_ids.values()}
        t = 0
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line[0] == "#":
                t = int(line[1:])
            elif line[0] in "01xzXZ" or line[0] == "b":
                if line[0] == "b":
                    parts = line.split()
                    val, vid = parts[0][1:], parts[1]
                    if vid in sig_ids:
                        try:
                            data[sig_ids[vid]].append((t, int(val, 2)))
                        except ValueError:
                            pass
                else:
                    vid = line[1:]
                    if vid in sig_ids:
                        data[sig_ids[vid]].append((t, 0 if line[0] == "0" else 1))
    return data


def to_series(pairs):
    if not pairs:
        return np.array([0]), np.array([0])
    t = np.array([p[0] for p in pairs], dtype=np.float64)
    v = np.array([p[1] for p in pairs], dtype=np.float64)
    return t, v


def main():
    vcd = sys.argv[1]
    out = sys.argv[2]
    t0 = int(sys.argv[3]) if len(sys.argv) > 3 else None
    t1 = int(sys.argv[4]) if len(sys.argv) > 4 else None
    data = parse_vcd(vcd)
    print('signals:', {k: len(v) for k, v in data.items() if v})

    fig, axes = plt.subplots(7, 1, figsize=(13, 12), sharex=True)
    ax = axes[0]
    for nm, color in (("i_in", "C0"), ("q_in", "C1")):
        if data.get(nm):
            t, v = to_series(data[nm])
            ax.step(t, v, where="post", lw=0.8, color=color, label=nm)
    ax.set_ylabel("MF I/Q"); ax.legend(loc="upper right", fontsize=8); ax.grid(alpha=.3)

    ax = axes[1]
    for nm, c in (("state", "C3"), ("detect", "C2"), ("ready", "C4")):
        if data.get(nm):
            t, v = to_series(data[nm])
            ax.step(t, v, where="post", lw=1.0, color=c, label=nm)
    ax.set_ylabel("state/detect"); ax.legend(loc="upper right", fontsize=8); ax.grid(alpha=.3)

    ax = axes[2]
    for nm, c in (("c_e", "C0"), ("locked_phase", "C2"), ("pmax", "C4")):
        if data.get(nm):
            t, v = to_series(data[nm])
            ax.step(t, v, where="post", lw=1.2, color=c, label=nm)
    ax.set_ylabel("phase"); ax.legend(loc="upper right", fontsize=8); ax.grid(alpha=.3)

    ax = axes[3]
    for nm, c in (("pcnt", "C0"), ("mcnt", "C1"), ("ewcnt", "C5")):
        if data.get(nm):
            t, v = to_series(data[nm])
            ax.step(t, v, where="post", lw=1.0, color=c, label=nm)
    ax.set_ylabel("counters"); ax.legend(loc="upper right", fontsize=8); ax.grid(alpha=.3)

    ax = axes[4]
    for nm, c in (("accR", "C0"), ("accI", "C1")):
        if data.get(nm):
            t, v = to_series(data[nm])
            ax.plot(t, v, lw=0.9, color=c, label=nm)
    ax.set_ylabel("accR/accI"); ax.legend(loc="upper right", fontsize=8); ax.grid(alpha=.3)

    ax = axes[5]
    if data.get("serve"):
        t, v = to_series(data["serve"])
        ax.step(t, v, where="post", lw=1.0, color="C2", label="serve")
    ax.set_ylabel("serve"); ax.legend(loc="upper right", fontsize=8); ax.grid(alpha=.3)

    ax = axes[6]
    if data.get("smp_cnt"):
        t, v = to_series(data["smp_cnt"])
        ax.step(t, v, where="post", lw=0.8, color="k", label="smp_cnt")
    ax.set_ylabel("smp_cnt"); ax.set_xlabel("时间 (采样/拍)")
    ax.legend(loc="upper right", fontsize=8); ax.grid(alpha=.3)

    if t0 is not None or t1 is not None:
        for a in axes:
            a.set_xlim(left=t0, right=t1)
    fig.suptitle(f"同步器内部波形（{Path(vcd).name}）")
    fig.tight_layout()
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=110)
    print('[render_wave] ->', out)


if __name__ == "__main__":
    main()
