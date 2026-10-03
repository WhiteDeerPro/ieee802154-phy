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




def probe_m(where='chips', snr_db=-1.0, seed=99):
    """量出信号在插点处的典型满幅 M（作为"AGC 锁定的窗口"）。"""
    rng = np.random.default_rng(seed)
    payload = bytes(rng.integers(0, 256, size=20).tolist())
    rx = [chains.stage_matched_filter(), chains.stage_sync('honest'),
          chains.stage_sample_chips(), chains.stage_despread(), chains.stage_deframe()]
    c = chains.Chain(name='probe')
    c.then_tx(*chains.tx_stages())
    c.then_channel(chains.awgn(snr_db, rng))
    c.then_rx(*rx)
    sig = c.run(payload=payload)
    x = sig.meta['mf'] if where == 'mf' else sig.rx_chips
    return float(np.abs(x).max())


def run_point_misaligned(where, bits, att_db, snr_db, m_ref, n_frames=N_FRAMES, seed=0):
    """AGC 未对齐模拟（修正量纲）: 窗口固定为 M（AGC 满幅），信号只占 1/att 窗
    ——即按 fs = M·10^(att/20) 量化（信号相对窗口衰减 att dB ≈ 损失 att/6 个有效位）。"""
    rng = np.random.default_rng(seed)
    fs = m_ref * (10.0 ** (att_db / 20.0))
    fails = 0
    for _ in range(n_frames):
        payload = bytes(rng.integers(0, 256, size=20).tolist())
        rx = [chains.stage_matched_filter()]
        def apply(sig, _fs=fs, _b=bits, _w=where):
            x = sig.meta['mf'] if _w == 'mf' else sig.rx_chips
            q = q_c(x, _b, _fs)             # 固定窗口（AGC 满幅）
            if _w == 'mf':
                sig.meta['mf'] = q
            else:
                sig.rx_chips = q
        apply.__name__ = f'misalign[{where}]({bits}b,{att_db}dB)'
        if where == 'mf':
            rx.append(apply)
        rx.append(chains.stage_sync('honest'))
        rx.append(chains.stage_sample_chips())
        if where == 'chips':
            rx.append(apply)
        rx.append(chains.stage_despread())
        rx.append(chains.stage_deframe())
        c = chains.Chain(name='misalign')
        c.then_tx(*chains.tx_stages())
        c.then_channel(chains.awgn(snr_db, rng))
        c.then_rx(*rx)
        sig = c.run(payload=payload)
        n_err, _ = measure.frame_bit_errors(sig)
        if n_err > 0:
            fails += 1
    return fails


def main_misaligned():
    print('=== AGC 未对齐验证: 信号占窗不足时的"需要窗口位"（码片 SNR=-1dB, 80 帧/点）===')
    print('（att = 信号相对满窗的衰减; 同列: 误帧数/80——固定满窗量化）\n')
    atts = [0.0, 6.0, 12.0, 18.0]
    bits = [16, 8, 6, 5, 4]
    print(f'{"att(dB)":>8}' + ''.join(f'{str(b)+"b":>8}' for b in bits))
    m_chips = probe_m('chips')
    print(f'（窗口参考: 该插点典型满幅 M = {m_chips:.0f}）\n')
    for att in atts:
        row = [str(run_point_misaligned('chips', b, att, -1.0, m_chips)) for b in bits]
        print(f'{att:>8.0f}' + ''.join(f'{v:>8}' for v in row))
    print('\n（预期: 需要窗口位 ≈ 有效位(3-4) + 对齐损失(att/6dB)）')


def main_snr_width():
    """低 SNR × 位宽扫描（chips 插点; 自适应定标每帧打满窗 = "AGC 锁总能量" 语义）。
    检验假说: 低 SNR 时信号只占总能量一部分 → 信号占窗份额缩小 → 需要更多总位宽
    保住"信号有效位 ≥ 3-4"。预期: 需要位宽 ≈ 3-4 + 10log10((S+N)/S)/6。"""
    print('=== 低 SNR × 位宽（chips 插点; 自适应定标=AGC 打满窗; 80 帧/点; 误帧数）===')
    snrs = [20.0, 8.0, 0.0, -4.0, -8.0]
    bits = [16, 8, 6, 5, 4, 3, 2]
    print(f'{"snr(dB)":>8}' + ''.join(f'{str(b) + "b":>7}' for b in bits) + f'{"无损":>8}')
    for snr in snrs:
        row = [run_point('chips', b, snr)[0] for b in bits]
        f0, _ = run_point('chips', None, snr)
        print(f'{snr:>8.0f}' + ''.join(f'{v:>7}' for v in row) + f'{f0:>8}')
    print('\n（份额损失 10log10((S+N)/S): 0dB→3.0, -4dB→5.5, -8dB→8.6——预期位宽需求同向增长）')


if __name__ == '__main__':
    if '--snr-width' in sys.argv:
        main_snr_width()
    else:
        main()
        main_misaligned()
