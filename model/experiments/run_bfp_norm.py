#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""run_bfp_norm.py —— "模块浮点化"第一块样板: 非相干算子入口的块归一 (BFP) vs 固定定标。

背景（讨论结论）: 非相干解扩对全局幅度透明（argmax 不变），故"把 despreader
浮点化" = 在其入口做"块归一 + 窄尾数"（BFP），算子本身无需内浮点。
本实验量化两问:
  A) 静态信道: BFP8 与固定定标 8/12 位有无差别（浮点化是否白伤精度）;
  B) 帧内幅度动态（AGC 跟不上的慢漂移）: 固定定标失效程度 vs BFP 跟随的收益。

方案（chips 插点, sig.rx_chips）:
  ideal   不量化
  fixed12 首符号 max 定标整帧 + 12 位   (= 现状"固定窗口"等价物)
  fixed8  同上 + 8 位
  bfp8    块归一（块=1 符号=32 片; 指数=上一块 max, 滞后 1 块）+ 8 位尾数
  bfp6    同上 + 6 位尾数

场景: static（纯 AWGN, -1dB）/ dyn6（符号级随机游走 ±6dB）/ dyn12（±12dB）
指标: 错帧数/60。dyn 为"AGC 完全跟不上"的上界标定（合成动态 > 现实帧内慢衰落）。
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'model'))
import chains                               # noqa: E402
import measure                              # noqa: E402
from baseband import impairments as imp     # noqa: E402

N_FRAMES = 60
SYM_CHIPS = 32


def q_c(x, bits, fs):
    """复数逐分量量化（I/Q 分开; 定标 = 块/帧尺度）。"""
    x = np.asarray(x)
    return (imp.quantize(x.real, bits, full_scale=fs)
            + 1j * imp.quantize(x.imag, bits, full_scale=fs))


def amp_walk_stage(range_db, step_db, rng, sps=8):
    """帧内符号级幅度随机游走（模拟 AGC 跟不上的慢漂移/远近变化）。"""
    def apply(sig):
        x = sig.rx
        ss = SYM_CHIPS * sps
        g = np.ones(len(x))
        cur = 0.0
        for k in range(0, len(x), ss):
            cur = float(np.clip(cur + rng.normal(0.0, step_db), -range_db, range_db))
            g[k:k + ss] = 10.0 ** (cur / 20.0)
        sig.rx = x * g
    apply.__name__ = f'ampwalk({range_db}dB)'
    return apply


def chips_stage(scheme):
    """chips 插点处理: 固定定标 / 块归一（BFP）。"""
    def apply(sig):
        x = np.asarray(sig.rx_chips)
        if scheme == 'ideal':
            return
        if scheme.startswith('fixed'):
            bits = 12 if scheme == 'fixed12' else (4 if scheme == 'fixed4' else 8)
            n0 = min(SYM_CHIPS, len(x))
            fs = float(np.abs(x[:n0]).max()) or 1.0
            sig.rx_chips = q_c(x, bits, fs)
        else:
            bits = 8 if scheme == 'bfp8' else (4 if scheme == 'bfp4' else 6)
            out = np.empty_like(x)
            prev = None
            for k in range(0, len(x), SYM_CHIPS):
                blk = x[k:k + SYM_CHIPS]
                fs = prev if prev else (float(np.abs(blk).max()) or 1.0)
                out[k:k + SYM_CHIPS] = q_c(blk, bits, fs)
                prev = float(np.abs(blk).max()) or 1.0
            sig.rx_chips = out
    apply.__name__ = f'chips:{scheme}'
    return apply


def run_point(scheme, dyn, snr_db=-1.0, n_frames=N_FRAMES, seed=0):
    rng = np.random.default_rng(seed)
    fails = 0
    for _ in range(n_frames):
        payload = bytes(rng.integers(0, 256, size=20).tolist())
        rx = []
        if dyn is not None:
            rx.append(amp_walk_stage(dyn[0], dyn[1], rng))
        rx.append(chains.stage_matched_filter())
        rx.append(chains.stage_sync('honest'))
        rx.append(chains.stage_sample_chips())
        rx.append(chips_stage(scheme))
        rx.append(chains.stage_despread())
        rx.append(chains.stage_deframe())
        c = chains.Chain(name=f'bfp:{scheme}')
        c.then_tx(*chains.tx_stages())
        c.then_channel(chains.awgn(snr_db, rng))
        c.then_rx(*rx)
        sig = c.run(payload=payload)
        n_err, _ = measure.frame_bit_errors(sig)
        if n_err > 0:
            fails += 1
    return fails


def main():
    scenes = [('static', None), ('dyn6', (6.0, 1.2)), ('dyn12', (12.0, 2.4))]
    schemes = ['ideal', 'fixed12', 'fixed8', 'bfp8', 'bfp6']
    print('=== BFP 归一化 vs 固定定标（chips 插点; AWGN -1dB; 错帧数/60）===')
    print(f'{"方案":<10}' + ''.join(f'{s:>9}' for s, _ in scenes))
    for sch in schemes:
        row = [str(run_point(sch, d)) for _, d in scenes]
        print(f'{sch:<10}' + ''.join(f'{v:>9}' for v in row))
    print()
    print('（fixed = 首符号 max 定标整帧; bfp = 上符号 max 定标每符号（滞后 1 块）; 尾数 8/6 位）')


def main_diag():
    """诊断: 把条件推到"量化真的咬人"区间——4 位尾数 / ±24dB 动态 / 高 SNR。"""
    scenes = [('static', None), ('dyn24', (24.0, 4.8))]
    schemes = ['ideal', 'fixed4', 'bfp4', 'bfp8']
    for snr, tag in [(-1.0, 'snr-1dB'), (10.0, 'snr+10dB')]:
        print(f'=== 诊断: {tag}（错帧数/60）===')
        print(f'{"方案":<10}' + ''.join(f'{s:>9}' for s, _ in scenes))
        for sch in schemes:
            row = [str(run_point(sch, d, snr_db=snr)) for _, d in scenes]
            print(f'{sch:<10}' + ''.join(f'{v:>9}' for v in row))
        print()


if __name__ == '__main__':
    if '--diag' in sys.argv:
        main_diag()
    else:
        main()
