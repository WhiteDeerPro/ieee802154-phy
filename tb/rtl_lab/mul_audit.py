#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""mul_audit.py —— 全设计乘法器/除法器/存储审计（yosys RTL 级 stat）。

对每个模块单独 `hierarchy -top` 后统计 $mul / $div / $mod / $mem / $dff——
用于回答"设计里还剩哪些乘法器、在谁身上"。
口径: 模块默认参数（现役 W=16 变体见 synth_stats.py）; 变体单独列出。
用法: python tb/rtl_lab/mul_audit.py
"""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

SRC = [
    'rtl/common/chip_lut.sv', 'rtl/common/half_sine_fir.sv',
    'rtl/common/pn9_whiten.sv', 'rtl/common/crc16_fcs.sv',
    'rtl/common/awgn_gen.sv',
    'rtl/rx/frontend/rx_matched_filter.sv', 'rtl/rx/frontend/preamble_sync_csq.sv',
    'rtl/rx/frontend/preamble_detect.sv', 'rtl/rx/frontend/preamble_buf.sv',
    'rtl/rx/frontend/deinterleave.sv', 'rtl/rx/frontend/rx_frontend.sv',
    'rtl/rx/backend/cfo_rot.sv', 'rtl/rx/backend/sfd_detect.sv',
    'rtl/rx/backend/despreader.sv', 'rtl/rx/backend/rx_deframer.sv',
    'rtl/rx/backend/rx_chip_backend.sv', 'rtl/rx/top/rx_dual.sv',
    'rtl/tx/tx_framer.sv', 'rtl/tx/oqpsk_modulator.sv',
    'rtl/rx/variants/despreader_oct8.sv',
    'rtl/rx/variants/despreader_oct8_pipe.sv',
    'rtl/rx/variants/despreader_oct8_tdm.sv',
    'rtl/rx/variants/despreader_tdm.sv',
]

MODS = ['half_sine_fir', 'rx_matched_filter', 'preamble_detect', 'preamble_sync',
        'preamble_buf', 'deinterleave', 'cfo_rot', 'sfd_detect', 'despreader',
        'rx_deframer', 'chip_lut', 'pn9_whiten', 'crc16_fcs', 'awgn_gen',
        'rx_frontend', 'rx_chip_backend', 'rx_dual',
        'tx_framer', 'oqpsk_modulator',
        'despreader_oct8', 'despreader_oct8_pipe', 'despreader_oct8_tdm',
        'despreader_tdm']


def stat_of(mod):
    script = ("read_verilog -sv -I tb/rtl_lab/yosys_shim -I rtl/rx "
              + ' '.join(SRC) + f'; hierarchy -top {mod}; proc; opt; stat')
    r = subprocess.run(['yosys', '-p', script], cwd=ROOT,
                       capture_output=True, text=True, timeout=600)
    out = r.stdout
    if r.returncode != 0:
        return None
    # 取最后一个 stat 段（hierarchy 后的）
    secs = re.findall(r'=== (.*?) ===\n(.*?)(?=\n=== |\Z)', out, re.S)
    cells = {}
    for name, body in secs:
        if name.strip().startswith(mod):
            for m in re.finditer(r'\$(\w+)\s+(\d+)', body):
                cells[m.group(1)] = cells.get(m.group(1), 0) + int(m.group(2))
            break
    return cells


def main():
    print(f"{'module':26s} {'$mul':>5s} {'$div':>5s} {'$mod':>5s} {'$mem':>5s} {'$dff':>6s}")
    rows = []
    for mod in MODS:
        c = stat_of(mod)
        if c is None:
            print(f'{mod:26s}  -- read/elaborate 失败 --')
            continue
        row = (mod, c.get('mul', 0), c.get('div', 0), c.get('mod', 0),
               c.get('mem', 0), c.get('dff', 0))
        rows.append(row)
        print(f'{row[0]:26s} {row[1]:5d} {row[2]:5d} {row[3]:5d} {row[4]:5d} {row[5]:6d}')


if __name__ == '__main__':
    main()
