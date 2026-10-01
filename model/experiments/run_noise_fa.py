#!/usr/bin/env python
"""run_noise_fa.py —— 纯噪声虚警率直接测量（"虚警概率可算可测"）

动机: 级联虚警的总概率 = 各级概率相乘（可解析）；本脚本用**纯噪声流**直接实测
      各级触发率（比解析更直接，是我们设备的真实参数）。

输入: 生成 4,000,000 采样（250 ms @16MHz）的纯 AWGN（σ = 20dB 档噪声底）
输出: model/out/dual_mc/noise_only/（mem.bin / events.txt / 事件计数）

结论（2026-10-02 实测）:
  PDR 24 (0.10/ms), DET 119 (0.48/ms), SYSC 237 (0.95/ms),
  PHCH 0, FSTA 0, FRMA/FRMB 0
  → L1 稀疏命中（DET 多为扫描状态机重扫节拍）；L2 段确认（SEG_TH=512）挡住全部虚警；
    "完整流程虚警" < 1/250 ms（未观测到）。

用法: python model/experiments/run_noise_fa.py   （约 1 分钟）
"""
import sys, json, subprocess, time, collections
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tb/rx_dual_mc'))
sys.path.insert(0, str(ROOT / 'tb/rx_chain_e2e'))
import run_dual_mc as R          # noqa: E402
import mc_gen                    # noqa: E402

d = ROOT / 'model/out/dual_mc/noise_only'
d.mkdir(parents=True, exist_ok=True)
N = 4_000_000
rng = np.random.default_rng(5)
sigma = mc_gen.noise_sigma(8.0, 20.0)
i_q, _ = mc_gen.quantize(rng.standard_normal(N) * sigma)
q_q, _ = mc_gen.quantize(rng.standard_normal(N) * sigma)
packed = ((i_q.astype(np.uint32) & 0xFFF) | ((q_q.astype(np.uint32) & 0xFFF) << 12))
packed.astype('>u4').tofile(d / 'mem.bin')
(d / 'meta.json').write_text(json.dumps({'n_smp': N, 'sigma': float(sigma), 'seed': 5}, indent=1))

simv = ROOT / 'tb/rx_dual_mc/sim_build/simv_dual_mc_gate'
cmd = [str(simv), f"+MEM={d/'mem.bin'}", f"+NSMP={N}", f"+OUT={d/'events.txt'}",
       f"+INCA={R.inc_from_cfo(100e3)&0xFFFFFF}", f"+INCB={R.inc_from_cfo(-100e3)&0xFFFFFF}",
       "+PH=0", "+NORMT=90"]
t0 = time.time()
r = subprocess.run(cmd, cwd=d, env=R.VCS_ENV, capture_output=True, text=True)
print('noise run:', 'ok' if r.returncode == 0 else 'FAIL', f'{time.time()-t0:.0f}s')
assert r.returncode == 0, r.stdout[-400:]

cnt = collections.Counter()
for l in open(d / 'events.txt'):
    cnt[l.split()[0]] += 1
ms = N / 16e6 * 1000
print(f'观察 {N} 采样 = {ms:.0f} ms（σ={sigma:.1f}）')
for t in ['PDR', 'DET', 'SYSC', 'PHCH', 'FSTA', 'FRMA', 'FRMB']:
    print(f'  {t:<5} {cnt.get(t,0):>8}  ({cnt.get(t,0)/ms:.2f}/ms)')
json.dump(dict(cnt), open(d / 'event_counts.json', 'w'), indent=1)
