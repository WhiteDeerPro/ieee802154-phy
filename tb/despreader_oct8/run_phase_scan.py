#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""run_phase_scan.py —— RTL 相位扫描：residual phase φ 对三种度量的影响。

方法: 每点 = 同一 seed 生成噪声底（iid 复高斯）→ 统一乘 e^{jφ}（模拟帧内恒定残余相位）
      → 落盘 → stress_tb（ref/oct8/quad 三 DUT 并排）RTL 跑 → 统计（RTL 与模型已逐项断言）。
      φ 是点间唯一变量, 点间可比。
用途: ①在 quad（效应大）上验证"相位敏感性可在 RTL 扫到"; ②据结果决定对 oct8 的精扫网格。
用法: cd tb/despreader_oct8 && ../../.venv/bin/python run_phase_scan.py \
          [--phis 0,5,...,45] [--nsym 20000] [--snr -6]
产出: tb/despreader_oct8/sim_build/phase_scan.json + 控制台表。
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

TB = Path(__file__).resolve().parent


def run_point(phi, nsym, snr, seed):
    cmd = [sys.executable, str(TB / 'run_stress.py'), '--nsym', str(nsym),
           '--snr', str(snr), '--phi', str(phi), '--seed', str(seed)]
    r = subprocess.run(cmd, cwd=TB, capture_output=True, text=True, timeout=3600)
    if r.returncode != 0:
        sys.stderr.write(r.stdout[-1500:])
        raise SystemExit(f'phi={phi} FAILED')
    m = re.search(r"stress done: N=(\d+) ref_err=(\d+) oct_err=(\d+) diff=(\d+) "
                  r"quad_err=(\d+) diff_qref=(\d+)", r.stdout)
    assert m, r.stdout[-800:]
    keys = ['n', 'ref', 'oct', 'diff_oct', 'quad', 'diff_quad']
    return dict(zip(keys, (int(x) for x in m.groups())))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--phis', default='0,5,10,15,20,25,30,35,40,45')
    ap.add_argument('--nsym', type=int, default=20000)
    ap.add_argument('--snr', type=float, default=-6.0)
    ap.add_argument('--seed', type=int, default=301)
    ap.add_argument('--out', default=None)
    args = ap.parse_args()
    phis = [float(x) for x in args.phis.split(',')]
    rows = []
    print(f" phi° |  ref_err   oct8_err   quad_err  | diff_oct  diff_quad   (N={args.nsym}, snr={args.snr})")
    for phi in phis:
        st = run_point(phi, args.nsym, args.snr, args.seed)
        print(f"{phi:5.1f} | {st['ref']:8d}  {st['oct']:8d}  {st['quad']:8d}  | "
              f"{st['diff_oct']:8d}  {st['diff_quad']:8d}", flush=True)
        rows.append(dict(phi=phi, **st))
    out = Path(args.out) if args.out else TB / 'sim_build' / 'phase_scan.json'
    out.write_text(json.dumps(dict(snr=args.snr, nsym=args.nsym, seed=args.seed,
                                   phis=phis, rows=rows), indent=2))
    print(f'[scan] 结果 → {out}')


if __name__ == '__main__':
    main()
