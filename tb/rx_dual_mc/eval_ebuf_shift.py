#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""eval_ebuf_shift.py —— ebuf 截位变体评估（VSHIFT=0 vs 12; 对照旧 bin）。

场景: snr20 / snr6 / edge（同边界回归口径, 帧成功率 = A|B 任一通道成功）。
输出: 每 (场景, bin) 的 A/B/A|B 帧数。
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path('/home/host/Desktop/workspace/communication')
sys.path.insert(0, str(ROOT / 'tb' / 'rx_dual_mc'))
import run_dual_mc as R                      # noqa: E402

SIM = R.SIM_DIR
DMC = ROOT / 'model/out/dual_mc'
OUTD = DMC / 'boundary_reg'
OUTD.mkdir(parents=True, exist_ok=True)
inc_a = R.inc_from_cfo(100e3) & 0xFFFFFF
inc_b = R.inc_from_cfo(-100e3) & 0xFFFFFF


def run(stream, simv_name):
    d = DMC / stream
    m = json.load(open(d / 'meta.json'))
    ev = OUTD / f'ev_shift_{stream}_{simv_name}.txt'
    if ev.exists():
        ev.unlink()
    c = [str(SIM / simv_name), f'+MEM={d}/mem.bin', f"+NSMP={m['n_smp']}",
         f'+OUT={ev}', f"+CKS={m['mem_cks']}",
         f'+INCA={inc_a}', f'+INCB={inc_b}', '+PH=0', '+NORMT=90']
    r = subprocess.run(c, cwd=d, env=R.VCS_ENV, capture_output=True, text=True)
    assert r.returncode == 0 and ev.exists(), r.stdout[-1200:]
    fs = np.load(d / 'frames.npz')['frame_start'].astype(np.int64)
    okA, okB = set(), set()
    for line in open(ev):
        p = line.split()
        if len(p) >= 4 and p[0] in ('FRMA', 'FRMB') and int(p[3], 0) == 1:
            k = int(p[1])
            f = int(np.searchsorted(fs, k, 'right')) - 1
            if 0 <= f < len(fs):
                (okA if p[0] == 'FRMA' else okB).add(f)
    return okA, okB, len(fs)


def main():
    for stream in ('snr20', 'snr6', 'edge'):
        for name in ('simv_ps_csq', 'simv_ebuf_v0', 'simv_ebuf_v12'):
            okA, okB, n = run(stream, name)
            print(f'{stream:8s} {name:16s}: A={len(okA):3d} B={len(okB):3d} '
                  f'A|B={len(okA | okB):3d} / {n}', flush=True)


if __name__ == '__main__':
    main()
