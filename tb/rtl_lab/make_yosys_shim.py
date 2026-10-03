#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""make_yosys_shim.py —— 为老版 yosys(0.9) 生成 rot_lut.svh 兼容 shim。

原因: rtl/rx/rot_lut.svh 使用 `localparam int ARR[0:255] = '{...}`（SV 数组 pattern），
yosys 0.9 不支持; shim 改写为 `reg signed [15:0] ARR[0:255] + initial` （yosys 识别为
memory）。用法: python tb/rtl_lab/make_yosys_shim.py（synth_stats.py 依赖其产物）。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def topy(s):
    s = s.strip()
    m = re.match(r"16's([hd])([0-9A-Fa-f]+)", s)
    if m:
        return int(m.group(2), 16 if m.group(1) == 'h' else 10)
    return int(s)


def grab(src, name):
    m = re.search(name + r"\s*\[0:255\]\s*=\s*'?\{(.*?)\};", src, re.S)
    assert m, name
    nums = [topy(s) for s in m.group(1).split(',') if s.strip()]
    assert len(nums) == 256, (name, len(nums))
    return nums


def main():
    src = (ROOT / 'rtl/rx/rot_lut.svh').read_text()
    cos, sin = grab(src, 'COS_LUT'), grab(src, 'SIN_LUT')
    out = ["// rot_lut.svh —— Yosys shim（自动生成 by tb/rtl_lab/make_yosys_shim.py;",
           "// 源: rtl/rx/rot_lut.svh —— localparam 数组 pattern 是老 yosys(0.9) 不支持的 SV 语法）",
           "reg signed [15:0] COS_LUT [0:255];",
           "reg signed [15:0] SIN_LUT [0:255];",
           "initial begin"]
    for i, v in enumerate(cos):
        vv = v - (1 << 16) if v > 32767 else v
        out.append(f"    COS_LUT[{i}] = 16'sd{vv};")
    for i, v in enumerate(sin):
        vv = v - (1 << 16) if v > 32767 else v
        out.append(f"    SIN_LUT[{i}] = 16'sd{vv};")
    out.append("end")
    dst = ROOT / 'tb/rtl_lab/yosys_shim/rot_lut.svh'
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text('\n'.join(out) + '\n')
    print(f'shim -> {dst.relative_to(ROOT)} ({len(cos)}+{len(sin)} 项)')


if __name__ == '__main__':
    main()
