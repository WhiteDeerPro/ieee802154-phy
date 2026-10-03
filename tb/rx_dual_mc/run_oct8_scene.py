#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""run_oct8_scene.py —— oct8 换装整链场景对照: 符号级分歧能否传导到帧级?

构建: simv_ps_oct8 = 主 SOURCES（csq 配置）+ variants/despreader_oct8.sv + `define DESP_OCT8`
      （rx_chip_backend 的 ifdef 换装; 默认未定义 = 原版, 逐位等价）。
      → 机制证据: 仿真 stdout 必须出现 "[rx_chip_backend] despreader = oct8"（否则宏未生效, 直接判错）。
场景: snr20 / snr6 / mp1 / mixdev1（驱动与归帧口径完全复用 RB.run; 判据取自主回归）。
用法: cd tb/rx_dual_mc && ../../.venv/bin/python run_oct8_scene.py [--force-build]
产出: model/out/dual_mc/boundary_reg/ev_*_simv_ps_oct8_oct8.txt + 控制台对照表。
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tb' / 'rx_dual_mc'))
import run_dual_mc as R                       # noqa: E402
import run_boundary_regression as RB          # noqa: E402

SIMV_NAME = 'simv_ps_oct8'
MARKER = '[rx_chip_backend] despreader = oct8'


def build_oct8(force=False):
    simv = R.SIM_DIR / SIMV_NAME
    if simv.exists() and not force:
        return simv
    csrc = R.SIM_DIR / 'csrc_ps_oct8'
    csrc.mkdir(parents=True, exist_ok=True)
    cmd = [f"{R.VCS_ENV['VCS_HOME']}/bin/vcs", "-full64", "-sverilog",
           "-timescale=1ns/1ps", "-o", str(simv),
           "+incdir+" + str(ROOT / 'rtl' / 'rx'),
           "-pvalue+dual_mc_tb.RSTEN=1", "+define+DESP_OCT8"]
    cmd += [str(ROOT / s) for s in R.SOURCES]
    cmd.append(str(ROOT / 'rtl/rx/variants/despreader_oct8.sv'))
    t0 = time.time()
    r = subprocess.run(cmd, cwd=csrc, env=R.VCS_ENV, capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stdout[-3000:] + "\n" + r.stderr[-2000:])
        raise SystemExit('[oct8] build FAILED')
    print(f'[oct8] build ok ({time.time()-t0:.1f}s) -> {simv}')
    return simv


def check_marker(simv):
    """机制证据: 直接跑 snr20 场景, 抓 stdout 中的换装 marker。"""
    d = RB.DMC / 'snr20'
    m = json.load(open(d / 'meta.json'))
    ev = RB.OUTD / 'ev_marker_oct8.txt'
    if ev.exists():
        ev.unlink()
    cmd = [str(simv), f'+MEM={d}/mem.bin', f"+NSMP={m['n_smp']}", f'+OUT={ev}',
           f"+CKS={m['mem_cks']}", f'+INCA={RB.inc_a}', f'+INCB={RB.inc_b}',
           '+PH=0', '+NORMT=90']
    r = subprocess.run(cmd, cwd=d, env=R.VCS_ENV, capture_output=True, text=True)
    ok = MARKER in r.stdout and MARKER in r.stderr or MARKER in (r.stdout + r.stderr)
    print(f"[oct8] marker 检查: {'PASS' if ok else 'FAIL'}（{'已换装' if ok else '宏未生效!'}）")
    if not ok:
        sys.stderr.write(r.stdout[-1500:])
        raise SystemExit('[oct8] DESP_OCT8 宏未生效, 拒绝继续')
    return ev


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--force-build', action='store_true')
    args = ap.parse_args()

    simv = build_oct8(force=args.force_build)
    check_marker(simv)

    print('=== oct8 整链场景对照（基线 = csq 主回归） ===')
    PASS = True

    okA, okB, fs = RB.run('snr20', SIMV_NAME, 'oct8')
    n = len(okA)
    print(f"snr20 : FRMA {n}/60        （基线 60, 判据 ≥59） {'PASS' if n >= 59 else 'FAIL'}")
    PASS &= n >= 59

    okA, okB, fs = RB.run('snr6', SIMV_NAME, 'oct8')
    n = len(okA)
    print(f"snr6  : FRMA {n}/60        （基线 17, 判据 ≥15） {'PASS' if n >= 15 else 'FAIL'}")
    PASS &= n >= 15

    okA, okB, fs = RB.run('mp1', SIMV_NAME, 'oct8')
    n = len(okA)
    print(f"mp1   : FRMA {n}/60        （基线 58, 判据 ≥58） {'PASS' if n >= 58 else 'FAIL'}")
    PASS &= n >= 58

    okA, okB, fs = RB.run('mixdev1', SIMV_NAME, 'oct8')
    fa, fb = len(okA), len(okB)
    print(f"mixdev1: FA {fa} / FB {fb}  （基线 127/50, 判据 FA≥126, FB≥49）"
          f" {'PASS' if fa >= 126 and fb >= 49 else 'FAIL'}")
    PASS &= fa >= 126 and fb >= 49

    print(f"=== 总结: {'ALL PASS' if PASS else 'FAIL'} ===")
    sys.exit(0 if PASS else 1)


if __name__ == '__main__':
    main()
