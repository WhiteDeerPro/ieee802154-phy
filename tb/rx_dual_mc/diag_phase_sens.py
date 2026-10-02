#!/usr/bin/env python
"""diag_phase_sens.py —— 相位敏感度诱导实验（"否决证据"链的验证）。

方法: EXTPH=1（相位全外置）+ PHTAB 注入"每帧相位 = (帧起点 + offset) & 15"。
标定正确值 = 8（(fs+8)&15，见 dual_mc_tb 注释）；注入 8±1 / 8±2 / 8±4 观察：
  · 成功率随相位偏差的退化曲线（"相位敏感度"边界）;
  · 错误相位下链上"否决器"（SFD 相关 / CRC）如何响应（拒帧 vs 假通过）。

场景: 200 帧 @20dB 单设备 +100k（seed=77，与 fa20 同族）。
产出: model/out/dual_mc/phase_sens/（各点 events）+ 终端汇总表。
"""
import collections
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / 'tb' / 'rx_chain_e2e'))
import run_dual_mc as R      # noqa: E402
import mc_gen                # noqa: E402

OUT = ROOT / 'model/out/dual_mc/phase_sens'
OFFSETS = [8, 9, 7, 10, 6, 12, 4]      # 正确 / ±1 / ±2 / ±4
NFRAMES = 200


def run_one(simv, off):
    d = OUT / f"off{off:02d}"
    d.mkdir(parents=True, exist_ok=True)
    meta = mc_gen.gen_point(20, NFRAMES, 20, 8.0, 77, 400, 600, d,
                            gap_jitter=16, cfo_list=[100e3])
    gt = (d / 'frames.npz')
    import numpy as np
    fs = np.load(gt)['frame_start'].astype(np.int64)
    phtab = d / 'phtab.txt'
    phtab.write_text("".join(f"{int(s)} {int((s + off) & 15)}\n" for s in fs))
    ev = d / 'events.txt'
    cmd = [str(simv), f"+MEM={d}/mem.bin", f"+NSMP={meta['n_smp']}",
           f"+OUT={ev}", f"+CKS={meta['mem_cks']}",
           f"+INCA={R.inc_from_cfo(100e3) & 0xFFFFFF}",
           f"+INCB={R.inc_from_cfo(-100e3) & 0xFFFFFF}",
           '+PH=0', '+NORMT=90', f"+PHTAB={phtab}"]
    r = subprocess.run(cmd, cwd=d, env=R.VCS_ENV, capture_output=True, text=True)
    ok = sum(1 for line in open(ev) if line.startswith("FRMA") and line.split()[3] == '1')
    f0 = sum(1 for line in open(ev) if line.startswith("FRMA") and line.split()[3] == '0')
    fsta = sum(1 for line in open(ev) if line.startswith("FSTA"))
    return ok, f0, fsta


def main():
    simv = R.build(ext=True)      # simv_dual_mc_ext（EXTPH=1）
    print(f"EXTPH bin: {simv.name}; 每点 {NFRAMES} 帧")
    print("offset | CRC通过 | CRC失败 | 定界(FSTA) | 备注")
    for off in OFFSETS:
        ok, f0, fsta = run_one(simv, off)
        tag = "正确(基准=8)" if off == 8 else f"偏差 {off-8:+d}"
        print(f"  {off:2d}   |  {ok:3d}/{NFRAMES} |  {f0:3d}   |  {fsta:3d}     | {tag}")


if __name__ == "__main__":
    main()
