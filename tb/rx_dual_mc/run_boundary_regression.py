#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""run_boundary_regression.py —— 边界回归（涉及 restart/latch/同步时序的改动后必跑）。

三项：
  1) 注入边界：snr20 帧 9 × offset {0,+1000,+1500,+3000} × {A16, Csq} —— 判据全 OK
  2) edge 抖动扫描（140 帧）：A16 ≥ 93（基线 94）、Csq ≥ 85（基线 86）
  3) 主流量：snr20 ≥ 59/60（基线 60）、snr6 ≥ 16/60（17）、mixdev1 FA/FB ≥ 126/127

用法: python tb/rx_dual_mc/run_boundary_regression.py
产出: model/out/dual_mc/boundary_reg/report.md（+ 控制台表；退出码 0=全过）
"""
import sys
import json
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tb' / 'rx_dual_mc'))
import run_dual_mc as R          # noqa: E402
import numpy as np               # noqa: E402

SIM = R.SIM_DIR
DMC = ROOT / 'model/out/dual_mc'
OUTD = DMC / 'boundary_reg'
OUTD.mkdir(parents=True, exist_ok=True)
inc_a = R.inc_from_cfo(100e3) & 0xFFFFFF
inc_b = R.inc_from_cfo(-100e3) & 0xFFFFFF
PASS = True


def ensure_scenes():
    """场景缺失（如清理过 out 产物）时自动重建——regen_scenes 一键恢复。"""
    need = ['snr20', 'snr6', 'mixdev1', 'edge', 'mp1']
    missing = [d for d in need
               if not (DMC / d / 'mem.bin').exists()
               or not (DMC / d / 'frames.npz').exists()]
    if missing:
        print(f"[reg] 场景缺失 {missing} —— 自动重建（regen_scenes）...", flush=True)
        import regen_scenes as RS
        RS.gen_dual('snr20', 20)
        RS.gen_dual('snr6', 6)
        RS.gen_mixdev()
        RS.gen_edge()
        RS.gen_mp1()


def run(stream, simv_name, tag, inj=0):
    d = DMC / stream
    m = json.load(open(d / 'meta.json'))
    ev = OUTD / f'ev_{stream}_{simv_name}_{tag}.txt'
    if ev.exists():
        ev.unlink()
    c = [str(SIM / simv_name), f'+MEM={d}/mem.bin', f"+NSMP={m['n_smp']}",
         f'+OUT={ev}', f"+CKS={m['mem_cks']}",
         f'+INCA={inc_a}', f'+INCB={inc_b}', '+PH=0', '+NORMT=90']
    if inj:
        c.append(f'+INJRST={inj}')
    r = subprocess.run(c, cwd=d, env=R.VCS_ENV, capture_output=True, text=True)
    assert r.returncode == 0 and ev.exists(), r.stdout[-1200:]
    fs = np.load(d / 'frames.npz')['frame_start'].astype(np.int64)
    okA, okB = set(), set()
    for line in open(ev):
        p = line.split()
        if len(p) >= 4 and p[0] in ('FRMA', 'FRMB') and int(p[3], 0) == 1:
            k = int(p[1]); f = int(np.searchsorted(fs, k, 'right')) - 1
            if 0 <= f < len(fs):
                (okA if p[0] == 'FRMA' else okB).add(f)
    return okA, okB, fs


def main():
    global PASS
    lines = ["# 边界回归报告", ""]
    t0 = time.time()
    ensure_scenes()

    # ---- 1) 注入边界 ----
    lines += ["## 1. 注入边界（snr20 帧 9：±{0,1000,1500,3000}）", ""]
    fs9 = np.load(DMC / 'snr20' / 'frames.npz')['frame_start'].astype(np.int64)
    fk, f0 = 9, int(fs9[9])
    for name in ('simv_ps_a16', 'simv_ps_csq'):
        for off in (0, 1000, 1500, 3000):
            okA, okB, fs = run('snr20', name, f'inj{off}',
                               inj=(f0 + off) if off else 0)
            hit = fk in okA or fk in okB
            row = f"| {name} | +{off} | {'OK' if hit else 'FAIL'} |"
            lines.append(row)
            if not hit:
                PASS = False
    lines.append("")

    # ---- 2) edge 抖动扫描 ----
    lines += ["## 2. edge 抖动扫描（140 帧）", ""]
    for name, low in (('simv_ps_a16', 93), ('simv_ps_csq', 92)):
        okA, okB, fs = run('edge', name, 'edge')
        n = len(okA | okB)
        good = n >= low
        lines.append(f"- {name}: {n}/140（判据 ≥{low}） {'PASS' if good else 'FAIL'}")
        if not good:
            PASS = False

    # ---- 3) 主流量 ----
    lines += ["", "## 3. 主流量", ""]
    checks = [('snr20', 'simv_ps_a16', 59), ('snr20', 'simv_ps_csq', 59),
              ('snr6', 'simv_ps_a16', 16), ('snr6', 'simv_ps_csq', 16),
              ('mixdev1', 'simv_ps_a16', None), ('mixdev1', 'simv_ps_csq', None),
              ('mp1', 'simv_ps_a16', 58), ('mp1', 'simv_ps_csq', 58)]
    for stream, name, low in checks:
        okA, okB, fs = run(stream, name, 'main')
        if low is None:
            good = len(okA) >= 126 and len(okB) >= 49    # mixdev1 基线 127/50
            lines.append(f"- {stream} {name}: FA {len(okA)} / FB {len(okB)}"
                         f"（判据 FA≥126, FB≥49） {'PASS' if good else 'FAIL'}")
        else:
            good = len(okA) >= low
            lines.append(f"- {stream} {name}: FRMA {len(okA)}/{len(fs)}"
                         f"（判据 ≥{low}） {'PASS' if good else 'FAIL'}")
        if not good:
            PASS = False

    lines += ["", f"**总结: {'ALL PASS' if PASS else 'FAIL'}**"
                  f"（耗时 {time.time()-t0:.0f}s）"]
    (OUTD / 'report.md').write_text("\n".join(lines))
    print("\n".join(lines))
    sys.exit(0 if PASS else 1)


if __name__ == '__main__':
    main()
