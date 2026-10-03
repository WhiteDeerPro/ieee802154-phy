#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""run_bit_profile.py —— 位翻转剖面: "量化省电"来源的位段级/表示级实测。

背景:
  · 省电因果链 = 数值量化 → [机制] → 物理活动↓; 机制含: 时间门控(模块级) /
    位段门控 / 表示编码 / 电压。
  · 本实验补"位段 + 表示"两级的硬数据: 把 chips / mf 插点数据 16 位定点化
    （帧自适应定标 = AGC 好时）, 统计
      a) 补码表示的**每位翻转率剖面** + "每样本期望翻转位数";
      b) 符号-幅度表示（b15=sign, b14..b0=|x|）同量——检验"表示形态"对
         翻转活动的影响（用户浮点格式 sig1+exp4+mag 天然是符号-幅度系）。
  · 关键预期（诊断已见): 码片流是 PN 伪随机 → 补码下"全段同步翻"（符号扩展
    段随符号位每码片全翻）→ 全 16 位 ~50% 平原; 符号-幅度应显著更低。

场景: snr20 / -1dB / -8dB; 插点: mf 与 chips; 40 帧。
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'model'))
import chains                               # noqa: E402
import measure                              # noqa: E402

N_FRAMES = 40
W = 16
FULL = 2 ** (W - 1) - 1


def _prof(c):
    bits = ((c[:, None] >> np.arange(W)[None, :]) & 1).astype(bool)
    return (bits[:-1] != bits[1:]).mean(axis=0)              # [0]=b0


def profiles(x):
    """复数序列 → (补码剖面, 符号-幅度剖面); 帧自适应定标; I/Q 平均。"""
    def one(v):
        v = np.asarray(v)
        fs = float(np.abs(v).max())
        if fs <= 0:
            return np.zeros(W), np.zeros(W)
        q = np.round(v / fs * FULL).astype(np.int64)
        q = np.clip(q, -FULL - 1, FULL)
        twos = q & (2 ** W - 1)
        sm = ((q < 0).astype(np.int64) << (W - 1)) | (np.abs(q) & FULL)
        return _prof(twos), _prof(sm)
    ti, si = one(np.asarray(x).real)
    tq, sq = one(np.asarray(x).imag)
    return (ti + tq) / 2.0, (si + sq) / 2.0


def collect(where, snr_db, n_frames=N_FRAMES, seed=7):
    rng = np.random.default_rng(seed)
    tw = np.zeros(W)
    sm = np.zeros(W)
    for _ in range(n_frames):
        payload = bytes(rng.integers(0, 256, size=20).tolist())
        rx = [chains.stage_matched_filter(), chains.stage_sync('honest'),
              chains.stage_sample_chips(), chains.stage_despread(), chains.stage_deframe()]
        c = chains.Chain(name=f'tog:{where}')
        c.then_tx(*chains.tx_stages())
        c.then_channel(chains.awgn(snr_db, rng))
        c.then_rx(*rx)
        sig = c.run(payload=payload)
        x = sig.meta['mf'] if where == 'mf' else sig.rx_chips
        a, b = profiles(x)
        tw += a
        sm += b
    return tw / n_frames, sm / n_frames


def main():
    scenes = [('snr20', 20.0), ('-1dB', -1.0), ('-8dB', -8.0)]
    data = {(w, t): collect(w, s) for w in ['mf', 'chips'] for t, s in scenes}
    print('=== 位翻转率(%) 剖面（16 位定点化, 帧自适应定标; 40 帧; I/Q 平均）===')
    hdr = '        ' + ''.join(f'{("b" + str(b)):>7}' for b in range(W - 1, -1, -1))
    print(hdr)
    for which, idx in [('补码', 0), ('符号幅度', 1)]:
        for where in ['mf', 'chips']:
            for tag, _ in scenes:
                t = data[(where, tag)][idx]
                vals = ''.join(f'{v * 100:>7.1f}' for v in t[::-1])
                print(f'{which:<4}{where:<6}{tag:>6}' + vals)
        print()
    print('每样本期望翻转位数（16 位全宽; 位数之和）与表示对比:')
    for where in ['mf', 'chips']:
        for tag, _ in scenes:
            tw, sm = data[(where, tag)]
            print(f'  {where:<6}{tag:>6}: 补码 {tw.sum():5.2f} | 符号-幅度 {sm.sum():5.2f} '
                  f'| 降幅 {(1 - sm.sum() / max(tw.sum(), 1e-9)) * 100:4.0f}%')
    print()
    print('低 8 位（b7..b0）占全 16 位翻转比例（补码; = 关低 8 位的动态上限）:')
    for where in ['mf', 'chips']:
        for tag, _ in scenes:
            tw, _ = data[(where, tag)]
            frac = tw[:8].sum() / max(tw.sum(), 1e-9)
            print(f'  {where:<6}{tag:>6}: {frac * 100:.0f}%')


if __name__ == '__main__':
    main()
