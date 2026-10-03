#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""run_rand200.py —— 200 帧随机场景对照（回归方法的样本量扩展: 60 → 200）。

场景: rand200_s20 / rand200_s6（200 帧, seed=77, +100k CFO, gap 400±16——同主回归口径）
对照: simv_ps_csq（基线） vs simv_ps_oct8（8 边形换装）
输出: 每场景 收帧数 / 帧集差集 / 字节流差异帧 → 检验"零传导"在大样本下是否保持。
用法: cd tb/rx_dual_mc && ../../.venv/bin/python run_rand200.py [--only s6|s20]
"""
import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tb' / 'rx_dual_mc'))
sys.path.insert(0, str(ROOT / 'tb' / 'rx_chain_e2e'))
import run_boundary_regression as RB          # noqa: E402
import mc_gen                                 # noqa: E402

DMC = ROOT / 'model/out/dual_mc'
SCENES = {'s20': ('rand200_s20', 20.0), 's6': ('rand200_s6', 6.0)}
BUILDS = [('simv_ps_csq', 'csq'), ('simv_ps_oct8', 'oct8')]
SEED = 77


def gen_scene(tag, snr):
    d = DMC / tag
    if (d / 'mem.bin').exists() and (d / 'meta.json').exists():
        print(f'[gen] {tag} 已存在, 跳过')
        return
    d.mkdir(parents=True, exist_ok=True)
    meta = mc_gen.gen_point(snr, 200, 20, 8.0, SEED, 400, 600, d,
                            gap_jitter=16, cfo_list=[100e3])
    print(f'[gen] {tag}: n_smp={meta["n_smp"]} CKS={meta["mem_cks"]}')


def collect(ev, fs):
    ok, byta = set(), {}
    for line in open(ev):
        p = line.split()
        if len(p) >= 4 and p[0] == 'FRMA' and int(p[3], 0) == 1:
            f = int(np.searchsorted(fs, int(p[1]), 'right')) - 1
            if 0 <= f < len(fs):
                ok.add(f)
        elif len(p) >= 3 and p[0] == 'BYTA':
            f = int(np.searchsorted(fs, int(p[1]), 'right')) - 1
            byta.setdefault(f, []).append(int(p[2]) & 0xFF)
    return ok, byta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', choices=['s6', 's20', 'all'], default='all')
    args = ap.parse_args()
    keys = ['s6', 's20'] if args.only == 'all' else [args.only]

    for k in keys:
        tag, snr = SCENES[k]
        gen_scene(tag, snr)
        fs = np.load(DMC / tag / 'frames.npz')['frame_start'].astype(np.int64)
        res = {}
        for simv_name, short in BUILDS:
            okA, okB, _ = RB.run(tag, simv_name, 'r200')
            ev = RB.OUTD / f'ev_{tag}_{simv_name}_r200.txt'
            ok, byta = collect(ev, fs)
            res[short] = (ok, byta)
            print(f'[{tag}/{short}] A {len(okA)} / B {len(okB)} → 合并 {len(ok)}/200'
                  f'（出字节帧 {len(byta)}）', flush=True)
        c_ok, c_by = res['csq']
        o_ok, o_by = res['oct8']
        lost, new = sorted(c_ok - o_ok), sorted(o_ok - c_ok)
        diff_frames = sorted(f for f in set(c_by) | set(o_by)
                             if c_by.get(f) != o_by.get(f))
        print(f'[{tag}] 帧集: csq {len(c_ok)} vs oct8 {len(o_ok)} | '
              f'丢 {lost[:8]}{"..." if len(lost) > 8 else ""} | '
              f'增 {new[:8]}{"..." if len(new) > 8 else ""}')
        print(f'[{tag}] 字节流差异帧: {len(diff_frames)} 个 {diff_frames[:8]}'
              f'{"..." if len(diff_frames) > 8 else ""}', flush=True)


if __name__ == '__main__':
    main()
