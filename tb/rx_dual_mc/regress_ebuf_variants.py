#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""regress_ebuf_variants.py —— ebuf 截位变体的完整回归（复用官方边界回归的函数）。

- v12（落成候选）: 全项（注入边界 + edge + snr20/snr6/mp1/mixdev1）
- v14 / v16（进攻找边界）: 核心项（snr20 / snr6 / edge）
口径与 run_boundary_regression.py 一致（主流量取 FRMA=okA）。
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path('/home/host/Desktop/workspace/communication')
sys.path.insert(0, str(ROOT / 'tb' / 'rx_dual_mc'))
import run_boundary_regression as RB          # noqa: E402（有 __main__ 保护）

DMC = ROOT / 'model' / 'out' / 'dual_mc'


def inj_suite(name):
    fs9 = np.load(DMC / 'snr20' / 'frames.npz')['frame_start'].astype(np.int64)
    fk, f0 = 9, int(fs9[9])
    res = []
    for off in (0, 1000, 1500, 3000):
        okA, okB, _ = RB.run('snr20', name, f'inj{off}', inj=(f0 + off) if off else 0)
        res.append((off, (fk in okA) or (fk in okB)))
    return res


def main():
    RB.ensure_scenes()
    bins_full = ['simv_ebuf_v12']
    bins_core = ['simv_ebuf_v13', 'simv_ebuf_v14', 'simv_ebuf_v16']

    print('=== 注入边界（snr20 帧 9 × offset）===', flush=True)
    for name in bins_full:
        r = inj_suite(name)
        print(f'{name}: ' + ' '.join(f'+{o}:{"OK" if h else "FAIL"}' for o, h in r), flush=True)

    print('=== edge（140 帧, 判据 ≥92）===', flush=True)
    for name in bins_full + bins_core:
        okA, okB, _ = RB.run('edge', name, 'edge')
        n = len(okA | okB)
        print(f'{name}: A|B={n}/140 {"PASS" if n >= 92 else "FAIL"}', flush=True)

    print('=== 主流量（FRMA=okA）===', flush=True)
    for stream, low in (('snr20', 59), ('snr6', 15), ('mp1', 58)):
        for name in bins_full + bins_core:
            okA, okB, fs = RB.run(stream, name, 'main')
            n = len(okA)
            print(f'{stream:6s} {name}: A={n}/{len(fs)} {"PASS" if n >= low else "FAIL"}', flush=True)

    print('=== mixdev1（判据 FA≥126, FB≥49）===', flush=True)
    for name in bins_full:
        okA, okB, _ = RB.run('mixdev1', name, 'main')
        good = len(okA) >= 126 and len(okB) >= 49
        print(f'{name}: FA={len(okA)} FB={len(okB)} {"PASS" if good else "FAIL"}', flush=True)


if __name__ == '__main__':
    main()
