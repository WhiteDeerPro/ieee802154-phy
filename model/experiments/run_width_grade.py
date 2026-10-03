#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""run_width_grade.py —— 逐级精度需求分析（"重标定/窄管线"的设计输入）。

动机: "重标定"的目标是让管线中的运算单元更窄地工作。窄到多少？本实验在接收链
的两个关键级间插入 **N 位有效量化**（按信号幅度自适应定标 = "重标定后窄到 N 位"），
扫 N 求每级的"有效位门槛"（低于它性能开始掉）：

  A) MF 输出后（meta['mf']）—— 对应 notes §8 "21→16 截断" 的细粒度版;
  B) 采样片后（rx_chips）—— 对进入解扩的数据窄化。

口径: 误帧（frame_bit_errors > 0）@ 码片 SNR = −1dB（工作点）与 6dB（边缘）。
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'model'))
import chains                               # noqa: E402
import measure                              # noqa: E402
from baseband import impairments as imp     # noqa: E402

N_FRAMES = 80


def q_c(x, bits, fs):
    """复数逐分量量化（I/Q 分开; 定标 = 幅度自适应）。"""
    x = np.asarray(x)
    return (imp.quantize(x.real, bits, full_scale=fs)
            + 1j * imp.quantize(x.imag, bits, full_scale=fs))


def quant_stage(where, bits):
    def apply(sig):
        if where == 'mf':
            x = sig.meta['mf']
        else:
            x = sig.rx_chips
        fs = float(np.abs(x).max())
        fs = fs if fs > 0 else 1.0
        q = q_c(x, bits, fs)
        if where == 'mf':
            sig.meta['mf'] = q
        else:
            sig.rx_chips = q
    apply.__name__ = f"quant[{where}]({bits}b)"
    return apply


def run_point(where, bits, snr_db, n_frames=N_FRAMES, seed=0):
    rng = np.random.default_rng(seed)
    fails = 0
    for _ in range(n_frames):
        payload = bytes(rng.integers(0, 256, size=20).tolist())
        rx = [chains.stage_matched_filter()]
        if where == 'mf' and bits is not None:
            rx.append(quant_stage('mf', bits))
        rx.append(chains.stage_sync('honest'))
        rx.append(chains.stage_sample_chips())
        if where == 'chips' and bits is not None:
            rx.append(quant_stage('chips', bits))
        rx.append(chains.stage_despread())
        rx.append(chains.stage_deframe())
        c = chains.Chain(name=f'grade[{where}:{bits}]')
        c.then_tx(*chains.tx_stages())
        c.then_channel(chains.awgn(snr_db, rng))
        c.then_rx(*rx)
        sig = c.run(payload=payload)
        n_err, _ = measure.frame_bit_errors(sig)
        if n_err > 0:
            fails += 1
    return fails, n_frames


def main():
    BITS = [None, 16, 12, 8, 6, 5, 4, 3, 2]
    for snr in [-1.0, 6.0]:
        print(f'=== 码片 SNR = {snr} dB（{N_FRAMES} 帧/点; 误帧数）===')
        hdr = ''.join(f'{"无损" if b is None else str(b) + "b":>7}' for b in BITS)
        print(f'{"插点":<8}{hdr}')
        for where in ['mf', 'chips']:
            row = []
            for b in BITS:
                f_, n = run_point(where, b, snr)
                row.append(f'{f_:>3}/{n:<3}')
            print(f'{where:<8}' + ''.join(f'{v:>7}' for v in row))
        print()


if __name__ == '__main__':
    main()
