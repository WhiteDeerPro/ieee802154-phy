#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""eval_rand_boundaries.py —— 随机流量下的位宽边界扫描。

6 批随机流（rand1..rand6）× 5 个位宽档（v0/v12/v13/v14/v16）→ FRMA 成功率。
"地板" = v0（24 位）在同批的天然失败; 各位宽的**额外失败**才是截位代价。
"""
import sys
from pathlib import Path

ROOT = Path('/home/host/Desktop/workspace/communication')
sys.path.insert(0, str(ROOT / 'tb' / 'rx_dual_mc'))
import run_boundary_regression as RB          # noqa: E402

BINS = ['simv_ebuf_v0', 'simv_ebuf_v12', 'simv_ebuf_v13',
        'simv_ebuf_v14', 'simv_ebuf_v16']
RANDS = ['rand1', 'rand2', 'rand3', 'rand4', 'rand5', 'rand6']


def main():
    totals = {b: [0, 0] for b in BINS}
    print('=== 随机流量 × 位宽档（FRMA 成功/总帧）===', flush=True)
    for stream in RANDS:
        row = []
        for name in BINS:
            okA, okB, fs = RB.run(stream, name, 'rand')
            n, tot = len(okA), len(fs)
            totals[name][0] += n
            totals[name][1] += tot
            row.append(f'{n}/{tot}')
        print(f'{stream}: ' + ' | '.join(f'{b.split("_")[-1]}={v}' for b, v in zip(BINS, row)),
              flush=True)
    print()
    print('=== 汇总 ===', flush=True)
    for b in BINS:
        n, tot = totals[b]
        print(f'{b}: {n}/{tot} ({n / tot * 100:.1f}%)', flush=True)


if __name__ == '__main__':
    main()
