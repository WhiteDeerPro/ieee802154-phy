#!/usr/bin/env python
"""diag_dev1_gap.py —— dev1 扫描缺口诊断（2026-10-02 一轮）

背景: 扫描模式（RSTEN+PH0+N90）下 dev1=36 vs dev2=50 的缺口, 之前多种子
证实为系统性。本脚本固化两个判别实验（结论见文末, 均已实测）:

实验 1: 流变体对照（同 seed, 只改设备顺序/参数）
  - swap12  : 交换 dev1/dev2 的时隙 —— 排除"位置/邻帧"效应;
  - nodrift4: dev4 不漂移 —— 排除"dev4 漂移干扰";
  - no4     : 去掉 dev4 —— 排除"dev4 存在";
  - dual_pm / dual_mp: 双设备交替, CFO 符号对调 —— 排除"CFO 方向性不对称".
实验 2: ext 注入 offset 分设备扫描（8/9/10）
  - 判断"最优抽样相位"是否对所有设备一致.

实测结论（2026-10-02）:
  - swap12: dev1=39（原位 36）、dev2=50（原位 50）→ 位置无关;
  - nodrift4: dev1=28 → dev4 漂移无关; no4: dev1=49/67（73%）→ 仍最低, dev4 无关;
  - dual_pm: 92/92, dual_mp: 94/92 → **CFO 方向对称**（双设备下 +100k 与
    -100k 等价）;
  - 失败帧 latch 时刻（1460-1493）与成功帧（中位 1485）一致、latch 值正确
    → 缺口不在"锁定"环节;
  - ext offset 扫描: dev1 最优≈10（44→48）、dev4 最优≈8/9（到 10 掉 8 点）、
    dev2 全宽容 → **不同设备的最优抽样相位差 ±2 采样, 而 deinterleave 相位
    是全局共享的** —— 共享定时架构的固有约束;
  - 剩余: 扫描模式相对 ext 的额外损失 ~8-9 点（dev1 36 vs 48）未定位.

用法: python tb/rx_dual_mc/diag_dev1_gap.py [--which all]
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tb" / "rx_dual_mc"))
sys.path.insert(0, str(ROOT / "tb" / "rx_chain_e2e"))
import run_dual_mc as R      # noqa: E402
import mc_gen                # noqa: E402

DRIFT = 25e3


def stream_variant(frames, mode):
    cfo, spec, dev_of = [], [], []
    n4 = sum(1 for f in range(frames) if f % 4 == 3)
    k4 = 0
    for f in range(frames):
        m = f % 3 if mode == "no4" else f % 4
        if mode == "swap12":
            dev = {0: 1, 1: 0, 2: 2, 3: 3}[m]
        else:
            dev = m
        if dev == 0:
            cfo.append(100e3); spec.append((20, 20.0))
        elif dev == 1:
            cfo.append(-100e3); spec.append((20, 20.0))
        elif dev == 2:
            cfo.append(100e3); spec.append((64, 14.0))
        else:
            drift = 0.0 if mode == "nodrift4" else DRIFT * (k4 / max(1, n4 - 1))
            cfo.append(100e3 + drift); spec.append((20, 20.0)); k4 += 1
        dev_of.append(dev)
    return cfo, spec, np.array(dev_of)


def run_stream(tag, mode, frames=200, simv=None):
    od = ROOT / "model/out/dual_mc/diag" / tag
    od.mkdir(parents=True, exist_ok=True)
    cfo, spec, dev_of = stream_variant(frames, mode)
    meta = mc_gen.gen_point(20.0, frames, 20, 8.0, 1, 400, 600, od,
                            gap_jitter=16, cfo_list=cfo, frame_specs=spec)
    gt = np.load(od / "frames.npz"); fs = gt["frame_start"].astype(int)
    out = f"/tmp/diag_{tag}.txt"
    cmd = [str(simv), f"+MEM={od/'mem.bin'}", f"+NSMP={meta['n_smp']}", f"+OUT={out}",
           f"+INCA={R.inc_from_cfo(100e3) & 0xFFFFFF}",
           f"+INCB={R.inc_from_cfo(-100e3) & 0xFFFFFF}", "+PH=0", "+NORMT=90"]
    r = subprocess.run(cmd, cwd=od, env=R.VCS_ENV, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-600:]
    succ = np.zeros(len(fs), bool)
    for l in open(out):
        p = l.split()
        if p and p[0] in ("FRMA", "FRMB") and p[3] == "1":
            k = int(p[1]); succ[int(np.searchsorted(fs, k, "right")) - 1] = True
    res = {}
    for dev, name in [(0, "dev1"), (1, "dev2"), (2, "dev3"), (3, "dev4")]:
        idx = dev_of == dev
        if idx.any():
            res[name] = f"{int(succ[idx].sum())}/{int(idx.sum())}"
    print(f"{tag:10s}: " + "  ".join(f"{k}={v}" for k, v in res.items()))


def run_ext_offsets(tag="mixdev1", offs=(8, 9, 10)):
    d = ROOT / "model/out/dual_mc" / tag
    gt = np.load(d / "frames.npz"); fs = gt["frame_start"].astype(int)
    meta = json.load(open(d / "meta.json"))
    simv = R.build(ext=True)
    print(f"ext 注入 offset 扫描（{tag}, 分设备）:")
    print(f"{'offset':>6}   dev1 dev2 dev3 dev4")
    for off in offs:
        offv = off & 15
        (d / "phtab_tmp.txt").write_text(
            "".join(f"{int(s)} {int((s+offv)&15)}\n" for s in fs))
        out = f"/tmp/ext_off{off}.txt"
        cmd = [str(simv), f"+MEM={d/'mem.bin'}", f"+NSMP={meta['n_smp']}", f"+OUT={out}",
               f"+INCA={R.inc_from_cfo(100e3) & 0xFFFFFF}",
               f"+INCB={R.inc_from_cfo(-100e3) & 0xFFFFFF}",
               f"+PHTAB={d/'phtab_tmp.txt'}"]
        r = subprocess.run(cmd, cwd=d, env=R.VCS_ENV, capture_output=True, text=True)
        assert r.returncode == 0, r.stdout[-600:]
        fa = np.zeros(len(fs), np.int8); fb = np.zeros(len(fs), np.int8)
        for l in open(out):
            p = l.split()
            if p and p[0] in ("FRMA", "FRMB"):
                k = int(p[1]); f = int(np.searchsorted(fs, k, "right")) - 1
                (fa if p[0] == "FRMA" else fb)[f] = 1 if int(p[3]) else 2
        res = []
        for m in range(4):
            idx = [f for f in range(len(fs)) if f % 4 == m]
            res.append(sum(1 for f in idx if fa[f] == 1 or fb[f] == 1))
        print(f"{off:6d}   {res[0]:4d} {res[1]:4d} {res[2]:4d} {res[3]:4d}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--which", default="all",
                    choices=["all", "streams", "offsets"])
    a = ap.parse_args()
    if a.which in ("all", "streams"):
        simv = R.build(ext=False, force=True)   # RSTEN 编译变体
        for tag, mode in [("swap12", "swap12"), ("nodrift4", "nodrift4"),
                          ("no4", "no4")]:
            run_stream(tag, mode, simv=simv)
    if a.which in ("all", "offsets"):
        run_ext_offsets()


if __name__ == "__main__":
    main()
