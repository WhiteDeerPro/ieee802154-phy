#!/usr/bin/env python
"""run_edge_scan.py —— 边界扫描测试例：SNR 细网格 × pd 抖动 × 两方案成败。

生成 edge 流（6~12dB × 20 帧），跑 A16/Csq（既有 bin），从 events 的 PDR 段提取
"每帧前导期 pd 段数/首段时机"，输出 段数×成功率 / 时机×成功率 交叉表 + 图。

用法: python model/experiments/run_edge_scan.py [--no-run]
产出: model/out/dual_mc/edge/{events_*.txt, report.md, jitter_vs_success.png}
"""
import sys
import json
import time
from collections import Counter

import numpy as np

ROOT = __import__('pathlib').Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / 'tb' / 'rx_dual_mc'))
sys.path.insert(0, str(ROOT / 'tb' / 'rx_chain_e2e'))
import run_dual_mc as R          # noqa: E402
import mc_gen                    # noqa: E402
import subprocess                # noqa: E402

import _common                   # noqa: E402
plt = _common.init()             # Agg + 中文字体（统一，替代手写 rcParams）

DMC = ROOT / 'model/out/dual_mc'
OUT = DMC / 'edge'
SPECS = [(20, 6), (20, 7), (20, 8), (20, 9), (20, 10), (20, 11), (20, 12)]
SEED = 123
inc_a = R.inc_from_cfo(100e3) & 0xFFFFFF
inc_b = R.inc_from_cfo(-100e3) & 0xFFFFFF


def gen():
    if not (OUT / 'meta.json').exists():
        mc_gen.gen_point(20, 140, 20, 8.0, SEED, 400, 600, OUT, gap_jitter=16,
                         cfo_list=[100e3, -100e3], frame_specs=SPECS)


def run(simv, tag):
    ev = OUT / f'events_edge_{tag}.txt'
    if ev.exists():
        return ev
    meta = json.load(open(OUT / 'meta.json'))
    c = [str(simv), f'+MEM={OUT}/mem.bin', f"+NSMP={meta['n_smp']}", f'+OUT={ev}',
         f"+CKS={meta['mem_cks']}", f'+INCA={inc_a}', f'+INCB={inc_b}',
         '+PH=0', '+NORMT=90']
    r = subprocess.run(c, cwd=OUT, env=R.VCS_ENV, capture_output=True, text=True)
    assert r.returncode == 0 and ev.exists(), r.stdout[-1500:]
    return ev


def pd_segments(ev_file):
    ks = [int(l.split()[1]) for l in open(ev_file) if l.startswith('PDR')]
    segs = []
    for k in ks:
        if segs and k == segs[-1][1] + 1:
            segs[-1][1] = k
        else:
            segs.append([k, k])
    return segs


def frame_ok(ev_file, fs):
    ok = set()
    for line in open(ev_file):
        p = line.split()
        if len(p) >= 4 and p[0] in ('FRMA', 'FRMB') and int(p[3], 0) == 1:
            k = int(p[1]); f = int(np.searchsorted(fs, k, 'right')) - 1
            if 0 <= f < len(fs):
                ok.add(f)
    return ok


def main():
    gen()
    evA = run(R.SIM_DIR / 'simv_ps_a16', 'a16')
    evC = run(R.SIM_DIR / 'simv_ps_csq', 'csq')
    meta = json.load(open(OUT / 'meta.json'))
    fs = np.load(OUT / 'frames.npz')['frame_start'].astype(np.int64)
    snrs = np.array(meta['per_frame_snr_db'])
    segs = pd_segments(evC)
    okA = frame_ok(evA, fs)
    okC = frame_ok(evC, fs)

    rows = []
    for i, f0 in enumerate(fs):
        cand = [(a, b) for a, b in segs if f0 - 64 <= a <= f0 + 3000]
        rows.append((i, int(round(snrs[i])), len(cand),
                     (cand[0][0] - f0) if cand else None, i in okA, i in okC))

    cnt = Counter(r[2] for r in rows)
    lines = [f"# 边界扫描：pd 抖动 × 成功率（edge 流, {len(fs)} 帧, seed={SEED}）", "",
             "抖动指标 = 每帧前导期 pd 段数（scan_restart 次数）；时机 = 首段相对 f0。", "",
             "## 段数 × 成功率", "", "| seg | n | A16 | Csq |", "|---|---|---|---|"]
    xs, ya, yc, ns = [], [], [], []
    for s in sorted(cnt):
        sub = [r for r in rows if r[2] == s]
        a = sum(1 for r in sub if r[4]); c = sum(1 for r in sub if r[5])
        xs.append(s); ya.append(a / len(sub)); yc.append(c / len(sub)); ns.append(len(sub))
        lines.append(f"| {s} | {len(sub)} | {a}/{len(sub)} | {c}/{len(sub)} |")
    lines += ["", "## 时机（首段相对 f0）× 成功率", "",
              "| 时机(拍) | n | A16 | Csq |", "|---|---|---|---|"]
    for lo, hi in [(0, 200), (200, 400), (400, 600), (600, 900), (900, 1300), (1300, 2100)]:
        sub = [r for r in rows if r[3] is not None and lo <= r[3] < hi]
        if sub:
            lines.append(f"| {lo}-{hi} | {len(sub)} | "
                         f"{sum(1 for r in sub if r[4])}/{len(sub)} | "
                         f"{sum(1 for r in sub if r[5])}/{len(sub)} |")
    lines += ["", f"- 总: A16 {sum(1 for r in rows if r[4])}/{len(fs)}"
                  f" | Csq {sum(1 for r in rows if r[5])}/{len(fs)}", ""]

    fig, ax = plt.subplots(figsize=(7.5, 4.5))
    ax.plot(xs, ya, 'o-', label='A16')
    ax.plot(xs, yc, 's-', label='Csq')
    for x, y, n in zip(xs, ya, ns):
        ax.annotate(f'n={n}', (x, y), textcoords='offset points',
                    xytext=(2, 6), fontsize=7)
    ax.set_xlabel('前导期 pd 段数（抖动强度）')
    ax.set_ylabel('帧成功率')
    ax.set_title('pd 抖动 vs 帧成功率（edge 流, 6~12dB × 20 帧）')
    ax.grid(alpha=.3); ax.legend(); ax.set_ylim(0, 1.05)
    fig.tight_layout()
    fig.savefig(OUT / 'jitter_vs_success.png', dpi=110)
    (OUT / 'report.md').write_text("\n".join(lines))
    print(f"[edge_scan] -> {OUT/'report.md'} + jitter_vs_success.png")
    print(f"总: A16 {sum(1 for r in rows if r[4])}/{len(fs)} | Csq {sum(1 for r in rows if r[5])}/{len(fs)}")


if __name__ == '__main__':
    main()
