#!/usr/bin/env python
"""diag_shadow_compare.py —— A16 vs Csq 影子对比（同流逐帧成败交叉）。

对 4 场景（snr20 / snr6 / mixdev1 / edge）**同流**跑两个 bin（simv_ps_a16 / simv_ps_csq,
参考配置 +PH=0 +NORMT=90），按帧对齐成败：
  成功 = 该帧被任一通道 CRC 通过（FRMA∪FRMB, fcs=1）。
输出: 交叉表（都成 / 仅A16 / 仅Csq / 都败）+ 交换失败帧清单 → shadow_compare/report.md
判据解读: "仅A16" 与 "仅Csq" 数量接近 ⇒ 真打平（无方向性软肋）;
          显著偏一侧 ⇒ 该侧存在结构性劣势（转正评估的关键证据）。
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_dual_mc as R      # noqa: E402

DMC = ROOT / 'model/out/dual_mc'
OUT = DMC / 'shadow_compare'
STREAMS = ['snr20', 'snr6', 'mixdev1', 'edge']
inc_a = R.inc_from_cfo(100e3) & 0xFFFFFF
inc_b = R.inc_from_cfo(-100e3) & 0xFFFFFF


def run_bin(stream, simv_name):
    d = DMC / stream
    m = json.load(open(d / 'meta.json'))
    ev = OUT / f"{stream}_{simv_name}.txt"
    if ev.exists():
        ev.unlink()
    c = [str(R.SIM_DIR / simv_name), f'+MEM={d}/mem.bin', f"+NSMP={m['n_smp']}",
         f'+OUT={ev}', f"+CKS={m['mem_cks']}", f'+INCA={inc_a}', f'+INCB={inc_b}',
         '+PH=0', '+NORMT=90']
    r = subprocess.run(c, cwd=d, env=R.VCS_ENV, capture_output=True, text=True)
    assert r.returncode == 0 and ev.exists(), r.stdout[-800:]
    fs = np.load(d / 'frames.npz')['frame_start'].astype(np.int64)
    ok = set()
    for line in open(ev):
        p = line.split()
        if len(p) >= 4 and p[0] in ('FRMA', 'FRMB') and int(p[3], 0) == 1:
            k = int(p[1])
            f = int(np.searchsorted(fs, k, 'right')) - 1
            if 0 <= f < len(fs):
                ok.add(f)
    return ok, len(fs)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    lines = ["# A16 vs Csq 影子对比（同流逐帧成败交叉）", ""]
    lines.append("| 流 | 帧数 | 都成 | 仅A16 | 仅Csq | 都败 |")
    lines.append("|---|---|---|---|---|---|")
    detail = []
    for stream in STREAMS:
        oka, n = run_bin(stream, 'simv_ps_a16')
        okc, _ = run_bin(stream, 'simv_ps_csq')
        both = oka & okc
        aonly = sorted(oka - okc)
        conly = sorted(okc - oka)
        neither = n - len(both) - len(aonly) - len(conly)
        print(f"{stream:10s} | {n:4d} | {len(both):4d} | {len(aonly):4d} | "
              f"{len(conly):4d} | {neither:4d}", flush=True)
        lines.append(f"| {stream} | {n} | {len(both)} | {len(aonly)} | {len(conly)} | {neither} |")
        if aonly or conly:
            detail.append(f"- **{stream}**: 仅A16={aonly}；仅Csq={conly}")
    if detail:
        lines += ["", "## 交换失败帧明细", ""] + detail
    (OUT / 'report.md').write_text("\n".join(lines) + "\n")
    print("→", OUT / 'report.md')


if __name__ == "__main__":
    main()
