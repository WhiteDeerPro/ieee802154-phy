#!/usr/bin/env python
"""regen_scenes.py —— 重建边界回归所需场景（清理产物后的一键恢复）。

重建 snr20 / snr6 / mixdev1 / edge 的 mem.bin + frames.npz（同参数 ⇒ 与回归基线一致）。
参数来源（权威调用处）:
  · snr20/snr6: run_dual_mc 口径（cfo_list=[100k,-100k], frames=60, seed=21）
  · mixdev1   : run_mixed_dev.build_stream（四设备流：±100k / 长帧弱信号 / 漂移）
  · edge      : run_edge_scan 口径（6~12dB 网格 × 140 帧, seed=123）
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))   # run_mixed_dev 等
sys.path.insert(0, str(ROOT / 'tb' / 'rx_chain_e2e'))       # mc_gen
import mc_gen                       # noqa: E402
import run_mixed_dev as MD          # noqa: E402

DMC = ROOT / 'model/out/dual_mc'


def gen_dual(tag, snr, frames=60, seed=21):
    # 单设备流（全部 +100 kHz）——与回归判据一致（FRMA 全走 A 通道）;
    # 证据: 基线 snr20 60/60 / snr6 17/60; 若 ±100k 交替则 A 只剩一半（30 / 7）。
    out = DMC / tag
    out.mkdir(parents=True, exist_ok=True)
    meta = mc_gen.gen_point(snr, frames, 20, 8.0, seed, 400, 600, out,
                            gap_jitter=16, cfo_list=[100e3])
    print(f"{tag}: {meta['n_smp']} 采样")


def gen_mixdev(frames=200, seed=1):
    out = DMC / 'mixdev1'
    out.mkdir(parents=True, exist_ok=True)
    cfo, spec = MD.build_stream(frames)
    meta = mc_gen.gen_point(20, frames, 20, 8.0, seed, 400, 600, out,
                            gap_jitter=16, cfo_list=cfo, frame_specs=spec)
    print(f"mixdev1: {meta['n_smp']} 采样")


def gen_mp1(frames=60, seed=21):
    """温和多径 [1,.5]@0.5chip(=4 采样): 模型侧"扩频硬扛"档（BER=0）——
    作为 RTL 的"信道鲁棒性"回归场景。"""
    out = DMC / 'mp1'
    out.mkdir(parents=True, exist_ok=True)
    meta = mc_gen.gen_point(20, frames, 20, 8.0, seed, 400, 600, out,
                            gap_jitter=16, cfo_list=[100e3],
                            mp=dict(gains=[1.0, 0.5], delays=[0, 4]))
    print(f"mp1: {meta['n_smp']} 采样")


def gen_edge():
    out = DMC / 'edge'
    out.mkdir(parents=True, exist_ok=True)
    meta = mc_gen.gen_point(20, 140, 20, 8.0, 123, 400, 600, out, gap_jitter=16,
                            cfo_list=[100e3, -100e3],
                            frame_specs=[(20, s) for s in (6, 7, 8, 9, 10, 11, 12)])
    print(f"edge: {meta['n_smp']} 采样")


if __name__ == '__main__':
    gen_dual('snr20', 20)
    gen_dual('snr6', 6)
    gen_mixdev()
    gen_edge()
    gen_mp1()
    print("场景重建完成（snr20 / snr6 / mixdev1 / edge / mp1）")
