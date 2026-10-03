#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ablate_despreader.py —— despreader 门数归属消融（V1 干净; V2/V3 为连锁消融, 仅参考）。

V1 去平方  : pwr = 拼接（其余全保留）→ 差值 = 平方器贡献（**信息流保持, 可作论据**）
V2 去引擎  : nxt 直通 → **连锁**: 16 路相同 → argmax 退化 → 平方被优化（★不作论据,
             它展示"信息退化后全链可折叠到 ~3.6K"）
V3 去argmax: pwr 变死逻辑 → 连锁（★不作论据）
口径: yosys 0.9, techmap 后 cells; base = 单读原版。
用法: python tb/rtl_lab/ablate_despreader.py [full]
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'rtl/rx/backend/despreader.sv'
tmp = Path('/tmp')


def mk_variants():
    src = SRC.read_text()
    v1 = src.replace(
        "            pwr_n[kk] = nxt_i[kk]*nxt_i[kk] + nxt_q[kk]*nxt_q[kk];",
        "            pwr_n[kk] = {1'b0, nxt_i[kk], nxt_q[kk]};")
    assert v1 != src
    (tmp / 'desp_v1.sv').write_text(v1)
    v2 = re.sub(
        r"            if \(frame_start \|\| chip_cnt == 6'd0\) begin\n"
        r"                nxt_i\[kk\] = .*?\n                nxt_q\[kk\] = .*?\n"
        r"            end else begin\n"
        r"                nxt_i\[kk\] = .*?\n                          \? .*?\n"
        r"                          : .*?;\n"
        r"                nxt_q\[kk\] = .*?\n                          \? .*?\n"
        r"                          : .*?;\n"
        r"            end",
        "            nxt_i[kk] = {{(ACC_W-W){chip_i[W-1]}}, chip_i};\n"
        "            nxt_q[kk] = {{(ACC_W-W){chip_q[W-1]}}, chip_q};",
        src, flags=re.S)
    assert v2 != src
    (tmp / 'desp_v2.sv').write_text(v2)
    v3 = re.sub(
        r"        best_n  = 4'd0;\n        bestp_n = pwr_n\[0\];\n"
        r"        for \(kk = 1; kk < 16; kk = kk \+ 1\)\n"
        r"            if \(pwr_n\[kk\] > bestp_n\) begin\n"
        r"                bestp_n = pwr_n\[kk\];\n"
        r"                best_n  = kk\[3:0\];\n"
        r"            end",
        "        best_n  = 4'd0;\n        bestp_n = pwr_n[0];",
        src, flags=re.S)
    assert v3 != src
    (tmp / 'desp_v3.sv').write_text(v3)


def synth(path, tag):
    r = subprocess.run(['yosys', '-p',
        f"read_verilog -sv {path}; hierarchy -top despreader; proc; opt; techmap; opt; stat"],
        capture_output=True, text=True, timeout=900)
    m = re.search(r'Number of cells:\s+(\d+)', r.stdout)
    n = int(m.group(1)) if m else None
    print(f'{tag:28s} cells = {n}', flush=True)
    return n


def main():
    mk_variants()
    base = synth(str(SRC), 'base 原版（单读）')
    c1 = synth('/tmp/desp_v1.sv', 'V1 去平方 [干净]')
    if base and c1:
        print(f'\n**平方器贡献 = {base - c1} cells ({(base - c1) / base * 100:.0f}%)**')
    if 'full' in sys.argv:
        synth('/tmp/desp_v2.sv', 'V2 去引擎 [连锁]')
        synth('/tmp/desp_v3.sv', 'V3 去 argmax [连锁]')


if __name__ == '__main__':
    main()
