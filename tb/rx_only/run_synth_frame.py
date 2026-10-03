#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""run_synth_frame.py —— RX 全程（三段式，正式交付）。

  ①tb 决策码流: gen_point 以 seed 生成 PSDU（可复现; 从 frames.npz 读回作为期望）
  ②信道合成:    mc_gen.gen_point —— 与主回归场景完全同口径
                （gap 400±16 / tail 600 / +100kHz CFO / 12bit 饱和量化）
  ③ADC 接入:    真 tb（simv_ps_csq, rx_dual 双路 A16/Csq）以 $fread 播放 mem.bin，
                +CKS 激励完整性校验; 驱动路径与主回归 RB.run 逐字相同。

接入形态说明: 采用"合成→mem→tb 播放"（真实系统同为采样流驱动），
不用 cocotb 实时注入——两台行为不一致（未解, 见 notes §69），mem 路已验证。

断言: 帧 0 在 FRMA/FRMB 中 fcs=1，且 BYTA 字节流逐字节 == 决策 PSDU。
用法: cd tb/rx_only && ../../.venv/bin/python run_synth_frame.py [--frames 3] [--seed 21]
产出: model/out/dual_mc/synth_frame/{mem.bin, meta.json, frames.npz, report.md}
      + model/out/dual_mc/boundary_reg/ev_synth_frame_simv_ps_csq_synth.txt
"""
import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tb' / 'rx_dual_mc'))
sys.path.insert(0, str(ROOT / 'tb' / 'rx_chain_e2e'))
import run_boundary_regression as RB      # noqa: E402
import mc_gen                             # noqa: E402

DMC = ROOT / 'model/out/dual_mc'
OUTD = DMC / 'synth_frame'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--frames', type=int, default=3)
    ap.add_argument('--seed', type=int, default=21,
                    help='默认 21 = snr20 场景同 seed（首帧 PSDU 已知可收）')
    ap.add_argument('--snr', type=float, default=20.0)
    args = ap.parse_args()

    # ---- ① 决策码流 + ② 信道合成（同回归口径） ----
    OUTD.mkdir(parents=True, exist_ok=True)
    meta = mc_gen.gen_point(args.snr, args.frames, 20, 8.0, args.seed,
                            400, 600, OUTD, gap_jitter=16, cfo_list=[100e3])
    print(f"[①+②] 决策码流×{args.frames} → 合成 {meta['n_smp']} 采样  "
          f"CKS={meta['mem_cks']}  clip={meta['clip_frac']:.1e}", flush=True)

    # ---- ③ 真 tb 播放（RB.run: +MEM/+NSMP/+CKS/+INCA/+INCB/+PH/+NORMT） ----
    okA, okB, fs = RB.run('synth_frame', 'simv_ps_csq', 'synth')
    ok = okA | okB
    print(f"[③] FRMA {sorted(okA)} | FRMB {sorted(okB)} | 帧起点 {fs.tolist()}",
          flush=True)

    # ---- payload 校验: 帧 0 窗内 BYTA 字节流 == 决策 PSDU ----
    ev = DMC / 'boundary_reg' / 'ev_synth_frame_simv_ps_csq_synth.txt'
    psdu0 = np.load(OUTD / 'frames.npz')['psdu'][0].astype(np.uint8).tobytes()
    f0 = int(fs[0])
    f1 = int(fs[1]) if len(fs) > 1 else 1 << 60
    got = []
    n_det = 0
    for line in open(ev):
        p = line.split()
        if len(p) >= 3 and p[0] == 'BYTA' and f0 <= int(p[1]) < f1:
            got.append(int(p[2]) & 0xFF)
        elif p and p[0] == 'DET':
            n_det += 1
    payload_ok = bytes(got[:len(psdu0)]) == psdu0
    print(f"[payload] 首帧窗 BYTA {len(got)}B / 期望 {len(psdu0)}B → "
          f"{'一致 ✓' if payload_ok else '不一致 ✗'}（DET 事件 {n_det} 个）",
          flush=True)
    if not payload_ok:
        print(f"  expect={psdu0.hex()}")
        print(f"  got   ={bytes(got[:20]).hex()}")

    passed = 0 in ok and payload_ok
    rep = [f"# synth_frame 报告（RX 全程三段式）", "",
           f"- ①决策码流: seed={args.seed}, {args.frames} 帧 × PSDU 20B",
           f"- ②合成: n_smp={meta['n_smp']}, CKS={meta['mem_cks']}, "
           f"clip_frac={meta['clip_frac']:.1e}",
           f"- ③接入: simv_ps_csq（双路）; FRMA={sorted(okA)} FRMB={sorted(okB)}",
           f"- payload: BYTA {len(got)}B vs PSDU {len(psdu0)}B "
           f"{'一致' if payload_ok else '不一致'}",
           f"- **{'PASS' if passed else 'FAIL'}**"]
    (OUTD / 'report.md').write_text("\n".join(rep))
    print(f"[总判] {'PASS' if passed else 'FAIL'}", flush=True)
    sys.exit(0 if passed else 1)


if __name__ == '__main__':
    main()
