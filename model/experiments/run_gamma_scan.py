#!/usr/bin/env python
"""run_gamma_scan.py —— 前导检测判据 γ 的灵敏度/虚警折衷扫描（6dB vs 20dB）

背景: preamble_detect 判据 |acc| > γ·pwr（pwr=窗能量, 含噪声）。
低 SNR 时噪声抬高 pwr → 比值被稀释 → 检测失敏（6dB 档 49/49 失败均为「无 DET」）。

本脚本: 生成 dev1@6dB 与 dev1@20dB 两流 → 编译三档 γ → 跑 → 统计
成功帧 / 假帧（fcs=0 的 FRMA）→ 折衷曲线图。

产物: model/out/dual_mc/snr6/（流与图 gamma_tradeoff.png）
用法: python model/experiments/run_gamma_scan.py   （约 2 分钟, 含 VCS 编译与仿真）
"""
import sys, subprocess
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tb/rx_dual_mc'))
sys.path.insert(0, str(ROOT / 'tb/rx_chain_e2e'))
import run_dual_mc as R          # noqa: E402
import mc_gen                    # noqa: E402

SB = ROOT / 'tb/rx_dual_mc/sim_build'
GAMMAS = [(21, 5), (10, 5), (5, 5)]          # γ = NUM/32: 0.66 / 0.31 / 0.16

def gen(dirname, snr, n=60, seed=21):
    d = ROOT / dirname
    d.mkdir(parents=True, exist_ok=True)
    if (d / 'mem.bin').exists():
        import json
        return d, json.load(open(d / 'meta.json'))
    meta = mc_gen.gen_point(snr, n, 20, 8.0, seed, 400, 600, str(d), gap_jitter=16,
                            cfo_list=[100e3] * n, frame_specs=[(20, snr)] * n)
    (d / 'meta.json').write_text(__import__('json').dumps(meta, indent=1))
    return d, meta

def build_bin(name, gam_num, gam_shift):
    simv = SB / name
    if simv.exists():
        return simv
    cmd = [f"{R.VCS_ENV['VCS_HOME']}/bin/vcs", "-full64", "-sverilog",
           "-timescale=1ns/1ps", "-o", str(simv), "+incdir+" + str(ROOT / "rtl" / "rx"),
           "-pvalue+dual_mc_tb.RSTEN=1",
           f"-pvalue+dual_mc_tb.dut.u_fe.g_scan.u_pd.GAMMA_NUM={gam_num}",
           f"-pvalue+dual_mc_tb.dut.u_fe.g_scan.u_pd.GAMMA_SHIFT={gam_shift}"] \
          + [str(ROOT / s) for s in R.SOURCES]
    r = subprocess.run(cmd, cwd=SB / 'csrc_scan', env=R.VCS_ENV,
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stdout[-1500:]); raise SystemExit('build FAIL')
    return simv

def run(simv, d, meta, tag):
    out = f'/tmp/{tag}.txt'
    cmd = [str(simv), f"+MEM={d/'mem.bin'}", f"+NSMP={meta['n_smp']}", f"+OUT={out}",
           f"+INCA={R.inc_from_cfo(100e3)&0xFFFFFF}", f"+INCB={R.inc_from_cfo(-100e3)&0xFFFFFF}",
           "+PH=0", "+NORMT=90"]
    r = subprocess.run(cmd, cwd=d, env=R.VCS_ENV, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-300:]
    fs = np.load(d / 'frames.npz')['frame_start'].astype(int)
    ok = set(); junk = 0
    for l in open(out):
        p = l.split()
        if p and p[0] == 'FRMA':
            k = int(p[1]); f = int(np.searchsorted(fs, k, 'right')) - 1
            if not (0 <= f < len(fs)): continue
            if int(p[3]) == 1 and int(p[2]) == 20: ok.add(f)
            elif int(p[3]) == 0: junk += 1
    return len(ok), junk

d6, m6 = gen('model/out/dual_mc/snr6', 6.0)
d20, m20 = gen('model/out/dual_mc/snr20', 20.0)
rows = []
print(f'{"γ":>9} | {"6dB 成功":>8} {"假帧":>5} | {"20dB 成功":>9} {"假帧":>5}')
for gam in GAMMAS:
    simv = build_bin(f'simv_gate_g{gam[0]}_{gam[1]}', *gam)
    o6, j6 = run(simv, d6, m6, f'g{gam[0]}6')
    o20, j20 = run(simv, d20, m20, f'g{gam[0]}20')
    rows.append((gam[0] / 2 ** gam[1], o6, j6, o20, j20))
    print(f'{gam[0]}/{2**gam[1]:<4}={gam[0]/2**gam[1]:.2f} | {o6:>7}/60 {j6:>5} | {o20:>8}/60 {j20:>5}')

fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
g = [r[0] for r in rows]
axes[0].plot(g, [r[1] for r in rows], 'o-', label='6 dB 成功/60')
axes[0].plot(g, [r[3] for r in rows], 's-', label='20 dB 成功/60')
axes[0].set_xlabel('γ (GAMMA_NUM/32)'); axes[0].set_title('成功率 vs γ'); axes[0].legend(); axes[0].grid(alpha=0.3)
axes[1].plot(g, [r[2] for r in rows], 'o-', label='6 dB 假帧')
axes[1].plot(g, [r[4] for r in rows], 's-', label='20 dB 假帧')
axes[1].set_xlabel('γ'); axes[1].set_title('虚警（fcs=0 输出）vs γ'); axes[1].legend(); axes[1].grid(alpha=0.3)
fig.suptitle('前导检测判据 γ 的折衷：放松可救低 SNR，但虚警淹没高 SNR')
fig.tight_layout()
fig.savefig(d6 / 'gamma_tradeoff.png', dpi=110)
print('saved', d6 / 'gamma_tradeoff.png')
