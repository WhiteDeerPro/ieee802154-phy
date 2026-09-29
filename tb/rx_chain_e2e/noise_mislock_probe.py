"""纯噪声误锁实验: 无帧激励下统计 preamble_sync 的误锁定次数 vs 噪声水平。

用途: 量化「绝对门限 + 极值统计」在低 SNR 下的误锁率 (见 docs/07 §3)。
原理: 只在纯噪声下, 扫描器的 bestR 一旦越过 ph_thresh 即误锁; 每次误锁后
      卡在 ST_LOCK 直到 STUCK_TIMEOUT (2^18 采样), 故 DET 间隔 ≈ 2^18。
典型结果 (400k 采样/点): 10dB 以上 0 次; 4dB 1 次; 2dB/0dB 各 2 次
      (受超时限制, 非误锁率本身)。

用法: python tb/rx_chain_e2e/noise_mislock_probe.py   (在仓库根跑)
"""
import json, subprocess, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, 'model'); sys.path.insert(0, 'tb/rx_chain_e2e')
import mc_gen, run_mc

N = 400_000   # 采样 (≈28 帧时长)
simv = run_mc.build()
print(f"{'SNR(dB)':>8} {'σ_samp':>8} {'DET数':>6} {'误锁/10万采样':>12}")
for snr in (24.66, 16, 10, 4, 2, 0):
    pdir = Path(f'model/out/rtl_ber/data_noise/snr{snr:g}').resolve()  # 绝对路径: simv cwd 是本点目录
    pdir.mkdir(parents=True, exist_ok=True)
    sigma = mc_gen.noise_sigma(8.0, snr)
    rng = np.random.default_rng(7)
    i = mc_gen.quantize(rng.standard_normal(N) * sigma)[0]
    q = mc_gen.quantize(rng.standard_normal(N) * sigma)[0]
    packed = ((i & 0xFFF).astype(np.uint32) | ((q & 0xFFF).astype(np.uint32) << 12))
    packed.astype('>u4').tofile(pdir / 'mem.bin')
    cks = mc_gen.mem_checksum(packed)
    r = subprocess.run([str(simv), f"+MEM={pdir/'mem.bin'}", f"+NSMP={N}",
                        f"+OUT={pdir/'events.txt'}", f"+CKS={cks:08x}"],
                       cwd=pdir, env=run_mc.VCS_ENV, capture_output=True, text=True)
    ev = open(pdir / 'events.txt').read().splitlines()
    n_det = sum(1 for l in ev if l.startswith('DET'))
    print(f"{snr:>8.1f} {sigma:>8.1f} {n_det:>6} {n_det/(N/1e5):>12.1f}")
