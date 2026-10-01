#!/usr/bin/env python
"""run_pd_matrix.py —— 前导检测参数矩阵（γ × 确认拍数 × 判据源）

RTL 支持（rtl/rx/preamble_detect.sv）:
  THR_SRC   0=窗能量 pwr(历史) / 1=噪声底 nse（min-tracking 快降慢升）
  NOISE_LEAK 噪声底慢升速率 1/2^NOISE_LEAK
  CONF_CNT  连续命中确认拍数（2=历史行为）
  GAMMA_NUM/GAMMA_SHIFT  γ = NUM/2^SHIFT

场景: dev1@6dB / dev1@20dB 各 60 帧（远距离 vs 灵敏度量级）+ 纯噪声 250ms。
用途: 验证"放松 γ + 加长确认"的耦合收益；标定 nse 判据。

结论（2026-10-02 实测，见 model/out/dual_mc/ab_rotation/report_ab.md §9）:
  推荐 γ=13/32, CONF_CNT=6（pwr 型）: 6dB 17→45, 20dB 持平 56, 下游 0 假帧。
  注意代价: 低 SNR 侧 L1 虚警脉冲增多（12928/250ms，被级联挡住）。

用法: python model/experiments/run_pd_matrix.py   （约 8 分钟：6 编译 + 18 仿真）
"""
import sys, json, subprocess, collections
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tb/rx_dual_mc'))
sys.path.insert(0, str(ROOT / 'tb/rx_chain_e2e'))
import run_dual_mc as R          # noqa: E402

SB = ROOT / 'tb/rx_dual_mc/sim_build'
COMBOS = [
    ('base',       dict(GAMMA_NUM=21, GAMMA_SHIFT=5, THR_SRC=0, CONF_CNT=2)),
    ('g10c2',      dict(GAMMA_NUM=10, GAMMA_SHIFT=5, THR_SRC=0, CONF_CNT=2)),
    ('g10c4',      dict(GAMMA_NUM=10, GAMMA_SHIFT=5, THR_SRC=0, CONF_CNT=4)),
    ('g13c6',      dict(GAMMA_NUM=13, GAMMA_SHIFT=5, THR_SRC=0, CONF_CNT=6)),
    ('nse_c2_g64', dict(GAMMA_NUM=64, GAMMA_SHIFT=5, THR_SRC=1, CONF_CNT=2)),
]
D6 = ROOT / 'model/out/dual_mc/snr6'
D20 = ROOT / 'model/out/dual_mc/snr20'
DN = ROOT / 'model/out/dual_mc/noise_only'

def build(name, ps):
    simv = SB / f'simv_pd_{name}'
    cmd = [f"{R.VCS_ENV['VCS_HOME']}/bin/vcs", "-full64", "-sverilog", "-timescale=1ns/1ps",
           "-o", str(simv), "+incdir+" + str(ROOT / "rtl" / "rx"), "-pvalue+dual_mc_tb.RSTEN=1"]
    for k, v in ps.items():
        cmd.append(f"-pvalue+dual_mc_tb.dut.u_fe.g_scan.u_pd.{k}={v}")
    cmd += [str(ROOT / s) for s in R.SOURCES]
    r = subprocess.run(cmd, cwd=SB / 'csrc_scan', env=R.VCS_ENV, capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stdout[-1200:]); raise SystemExit('build FAIL')
    return simv

def run(simv, d, out):
    meta = json.load(open(d / 'meta.json'))
    fs = np.load(d / 'frames.npz')['frame_start'].astype(int)
    cmd = [str(simv), f"+MEM={d/'mem.bin'}", f"+NSMP={meta['n_smp']}", f"+OUT={out}",
           f"+INCA={R.inc_from_cfo(100e3)&0xFFFFFF}", f"+INCB={R.inc_from_cfo(-100e3)&0xFFFFFF}",
           "+PH=0", "+NORMT=90"]
    r = subprocess.run(cmd, cwd=d, env=R.VCS_ENV, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-400:]
    ok = set(); junk = 0; cnt = collections.Counter()
    for l in open(out):
        p = l.split()
        cnt[p[0]] += 1
        if p[0] == 'FRMA':
            k = int(p[1]); f = int(np.searchsorted(fs, k, 'right')) - 1
            if not (0 <= f < len(fs)): continue
            if int(p[3]) == 1 and int(p[2]) == 20: ok.add(f)
            elif int(p[3]) == 0: junk += 1
    return len(ok), junk, cnt

print(f'{"组合":<12} | {"6dB":>6} {"junk":>4} | {"20dB":>6} {"junk":>4} | noise PDR/DET/PHCH/假帧')
for name, ps in COMBOS:
    simv = build(name, ps)
    o6, j6, _ = run(simv, D6, f'/tmp/pdm_{name}_6.txt')
    o20, j20, _ = run(simv, D20, f'/tmp/pdm_{name}_20.txt')
    _, _, cn = run(simv, DN, f'/tmp/pdm_{name}_n.txt')
    print(f'{name:<12} | {o6:>4}/60 {j6:>4} | {o20:>4}/60 {j20:>4} | '
          f'{cn.get("PDR",0):>6}/{cn.get("DET",0):>5}/{cn.get("PHCH",0)}/{cn.get("FRMA",0)}')
