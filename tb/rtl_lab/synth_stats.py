#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""synth_stats.py —— Yosys 无映射综合统计（v2: 现役参数口径, 单次 rx_dual 层次）。

方法: 一次综合（read 全部 + `hierarchy -top rx_dual` + proc/opt/techmap/opt）——
`$paramod` 段即为**参数绑定后的现役配置**（W=16 / VSHIFT=8 等）。
  · stat: 各模块变体 local cells + `design hierarchy` 总 cells;
  · ltp -noff <module>: 各模块最长拓扑路径（现役参数）。
不做工艺映射（无 abc/liberty）。深度为拓扑深度（悲观上界, 模块间可比）。
用法: python tb/rtl_lab/synth_stats.py → model/out/synth_stats.txt
"""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = [
    'rtl/common/chip_lut.sv', 'rtl/common/half_sine_fir.sv',
    'rtl/common/pn9_whiten.sv', 'rtl/common/crc16_fcs.sv',
    'rtl/rx/frontend/rx_matched_filter.sv', 'rtl/rx/frontend/preamble_sync_csq.sv',
    'rtl/rx/frontend/preamble_detect.sv', 'rtl/rx/frontend/preamble_buf.sv',
    'rtl/rx/frontend/deinterleave.sv', 'rtl/rx/frontend/rx_frontend.sv',
    'rtl/rx/backend/cfo_rot.sv', 'rtl/rx/backend/sfd_detect.sv',
    'rtl/rx/backend/despreader.sv', 'rtl/rx/backend/rx_deframer.sv',
    'rtl/rx/backend/rx_chip_backend.sv', 'rtl/rx/top/rx_dual.sv',
]
TARGETS = ['despreader', 'sfd_detect', 'preamble_detect', 'preamble_sync',
           'cfo_rot', 'preamble_buf', 'half_sine_fir', 'rx_deframer',
           'deinterleave']


def main():
    ltp_cmds = '; '.join(f'ltp -noff *{t}*' for t in TARGETS)
    script = ("read_verilog -sv -I tb/rtl_lab/yosys_shim -I rtl/rx "
              + ' '.join(SRC) + '; '
              "hierarchy -top rx_dual; proc; opt; techmap; opt; stat; " + ltp_cmds)
    r = subprocess.run(['yosys', '-p', script], cwd=ROOT,
                       capture_output=True, text=True, timeout=1800)
    out = r.stdout
    lines = [f'# Yosys 无映射综合（v2; 现役参数; rc={r.returncode}）', '']
    print(lines[0], flush=True)

    # ---- stat: 段级 cells ----
    secs = re.findall(r'=== (.+?) ===\n(.*?)(?=\n=== |\Z)', out, re.S)
    lines.append('## stat（$paramod = 参数绑定后的现役配置）')
    print('## stat', flush=True)
    for name, body in secs:
        m = re.search(r'Number of cells:\s+(\d+)', body)
        if m:
            row = f'{name.strip():60s} cells = {m.group(1):>8s}'
            lines.append(row)
            print(row, flush=True)

    # ---- ltp ----
    lines.append('')
    lines.append('## ltp -noff（最长拓扑路径 = 组合深度, 悲观上界）')
    print('## ltp', flush=True)
    for m in re.finditer(r'Longest topological path in (\S+) \(length=(\d+)\)', out):
        row = f'{m.group(1):60s} depth = {m.group(2)}'
        lines.append(row)
        print(row, flush=True)

    (ROOT / 'model/out/synth_stats.txt').write_text('\n'.join(lines) + '\n')


if __name__ == '__main__':
    main()
