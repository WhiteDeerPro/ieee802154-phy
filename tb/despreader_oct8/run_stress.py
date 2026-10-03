#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""run_stress.py —— despreader_oct8 压力测试（文件回放, 纯 VCS, 无 cocotb）。

流程: ① 生成最坏条件激励（SNR=-6dB, φ=0°——由扫描确定的最坏组合）
      ② numpy 模型预期（与 RTL 同输入、同整数算法）
      ③ VCS 编译 + stress_tb 运行（加速路径: 无 per-cycle Python 往返）
      ④ 断言 RTL 统计与模型预期**逐项完全一致**

用法: cd tb/despreader_oct8 && ../../.venv/bin/python run_stress.py [--nsym 100000]
产出: tb/despreader_oct8/sim_build/stress/{mem.bin, gt.hex, meta.json}
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'model'))
import phy_802154 as phy                # noqa: E402

TB = Path(__file__).resolve().parent
BUILD = TB / 'sim_build'
WORK = BUILD / 'stress'
C = phy.CHIP


def oct8_metric(S):
    """与 RTL 完全一致的整数移位实现: 15/16·max + 15/32·min。"""
    a = np.abs(S.real)
    b = np.abs(S.imag)
    hi = np.maximum(a, b)
    lo = np.minimum(a, b)
    return hi - (hi // 16) + (lo // 2) - (lo // 32)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--nsym', type=int, default=100000)
    ap.add_argument('--snr', type=float, default=-6.0)
    ap.add_argument('--phi', type=float, default=0.0)
    ap.add_argument('--amp', type=int, default=512)
    ap.add_argument('--seed', type=int, default=101)
    ap.add_argument('--skip-run', action='store_true', help='只生成激励与模型预期')
    args = ap.parse_args()

    WORK.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(args.seed)
    sig = 10 ** (-args.snr / 20)            # 相对量纲: 码片=±1
    d = rng.integers(0, 16, args.nsym)
    nz = (rng.normal(0, sig / np.sqrt(2), (args.nsym, 32))
          + 1j * rng.normal(0, sig / np.sqrt(2), (args.nsym, 32)))
    r = (C[d] + nz) * args.amp * np.exp(1j * np.deg2rad(args.phi))   # 一次缩放
    ri = np.clip(np.round(r.real), -2048, 2047).astype(int)
    rq = np.clip(np.round(r.imag), -2048, 2047).astype(int)

    # ---- ② 模型预期（与 RTL 同输入、同算法）----
    z = ri + 1j * rq
    S = z @ C.T
    dref = (S.real ** 2 + S.imag ** 2).argmax(1)
    doct = oct8_metric(S).argmax(1)
    exp_ref = int((dref != d).sum())
    exp_oct = int((doct != d).sum())
    exp_diff = int((doct != dref).sum())
    print(f"[模型预期] N={args.nsym} ref_err={exp_ref} oct_err={exp_oct} diff={exp_diff}")

    # ---- ① 落盘 ----
    packed = ((ri & 0xFFF).astype(np.uint32) | ((rq & 0xFFF).astype(np.uint32) << 12))
    packed.astype('>u4').tofile(WORK / 'mem.bin')
    (WORK / 'gt.hex').write_text("\n".join(f"{x:x}" for x in d) + "\n")
    (WORK / 'meta.json').write_text(json.dumps(dict(
        nsym=args.nsym, snr=args.snr, phi=args.phi, amp=args.amp, seed=args.seed,
        exp_ref=exp_ref, exp_oct=exp_oct, exp_diff=exp_diff), indent=2))

    if args.skip_run:
        return

    # ---- ③ 编译 + 运行 ----
    env = dict(os.environ)
    env.setdefault("VCS_HOME", "/opt/synopsys/vcs201809")
    env.setdefault("VCS_TARGET_ARCH", "amd64")
    env.setdefault("SNPSLMD_LICENSE_FILE", "/opt/synopsys/Synopsys.dat")
    env.setdefault("LM_LICENSE_FILE", "/opt/synopsys/Synopsys.dat")
    simv = BUILD / 'simv_stress'
    if not simv.exists():
        cmd = [f"{env['VCS_HOME']}/bin/vcs", "-full64", "-sverilog", "-q",
               "-o", str(simv),
               str(ROOT / 'rtl/rx/backend/despreader.sv'),
               str(ROOT / 'rtl/rx/variants/despreader_oct8.sv'),
               str(TB / 'stress_tb.sv')]
        r = subprocess.run(cmd, cwd=BUILD, env=env, capture_output=True, text=True)
        assert r.returncode == 0, (r.stdout[-2000:] + r.stderr[-2000:])
        print('[编译] simv_stress OK')
    r = subprocess.run([str(simv),
                        f'+MEM={WORK}/mem.bin', f'+NSMP={args.nsym * 32}',
                        f'+GT={WORK}/gt.hex'],
                       cwd=WORK, env=env, capture_output=True, text=True, timeout=3600)
    print(r.stdout[-1800:])
    assert r.returncode == 0, r.stderr[-1200:]

    # ---- ④ 解析 + 比对 ----
    m = re.search(r"stress done: N=(\d+) ref_err=(\d+) oct_err=(\d+) diff=(\d+)", r.stdout)
    assert m, r.stdout[-2000:]
    got = tuple(int(x) for x in m.groups()[1:])
    exp = (exp_ref, exp_oct, exp_diff)
    print(f"[比对] RTL={got} 模型={exp}")
    assert got == exp, f'RTL 与模型不一致: RTL={got} vs 模型={exp}'
    print(f"=== 压力测试 PASS: N={args.nsym} 符号, RTL 统计与模型逐项完全一致 ===")


if __name__ == '__main__':
    main()
