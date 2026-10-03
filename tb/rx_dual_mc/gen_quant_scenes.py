#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""gen_quant_scenes.py —— ADC 位宽量化场景生成（"ADC 量化调节"第一步）。

从现有场景（如 snr20/snr6）的 mem.bin 生成 "ADC 再量化到 N bit" 变体：
    v12 = I/Q（signed 12bit，现役 ADC 输出口径）
    q   = round(v12 / LSB_N) * LSB_N,   LSB_N = 2^(12-N)   （同一满量程，更少位）
    → 重打包 mem.bin（>u4: [11:0]=I, [23:12]=Q）+ 重算 mem_cks + 复制 frames.* + 写 meta
输出: model/out/dual_mc/<src>_q<N>/
用法: python tb/rx_dual_mc/gen_quant_scenes.py            # 默认矩阵
      python tb/rx_dual_mc/gen_quant_scenes.py snr20 8 6   # 指定 src + bits
"""
import json
import shutil
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tb' / 'rx_chain_e2e'))
from mc_gen import mem_checksum    # noqa: E402

DMC = ROOT / 'model' / 'out' / 'dual_mc'


def requant(dst: Path, src: Path, bits: int, round_mode: str = 'round') -> int:
    """按 N-bit ADC 口径重量化 mem.bin；返回新 mem_cks。"""
    p = np.fromfile(src / 'mem.bin', dtype='>u4')
    i = (p & 0xFFF).astype(np.int32)
    q = ((p >> 12) & 0xFFF).astype(np.int32)
    i = np.where(i >= 2048, i - 4096, i)          # 12bit 补码 → signed
    q = np.where(q >= 2048, q - 4096, q)

    lsb = 1 << (12 - bits)
    if round_mode == 'round':
        i = np.round(i / lsb) * lsb
        q = np.round(q / lsb) * lsb
    else:                                          # 截断（floor 语义，SAR 保守模型）
        i = np.floor(i / lsb) * lsb
        q = np.floor(q / lsb) * lsb
    i = np.clip(i, -2048, 2047).astype(np.int32)   # 12bit 补码值域
    q = np.clip(q, -2048, 2047).astype(np.int32)

    packed = ((i.astype(np.uint32) & 0xFFF)
              | ((q.astype(np.uint32) & 0xFFF) << 12))
    dst.mkdir(parents=True, exist_ok=True)
    packed.astype('>u4').tofile(dst / 'mem.bin')
    cks = int(mem_checksum(packed))

    for f in ('frames.npz', 'frames.txt'):
        shutil.copy(src / f, dst / f)
    meta = json.load(open(src / 'meta.json'))
    meta['mem_cks'] = f'{cks:08x}'
    meta['quant_bits'] = bits
    meta['quant_round'] = round_mode
    meta['quant_src'] = src.name
    json.dump(meta, open(dst / 'meta.json', 'w'), indent=1)
    return cks


def main():
    argv = sys.argv[1:]
    if argv:
        srcs = [argv[0]]
        bits_list = [int(b) for b in argv[1:]] or [8, 6, 4]
    else:
        srcs = ['snr20', 'snr6']
        bits_list = [10, 8, 6, 5, 4]
    for s in srcs:
        for b in bits_list:
            if s == 'snr6' and b == 10:
                continue                           # snr6 基线本来就低, 10bit 无意义
            dst = DMC / f'{s}_q{b}'
            cks = requant(dst, DMC / s, b)
            print(f'{dst.name}: bits={b}  cks={cks:08x}')


if __name__ == '__main__':
    main()
