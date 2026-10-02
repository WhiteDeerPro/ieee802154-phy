# -*- coding: utf-8 -*-
"""test_multipath —— 多径行为回归（模型侧, 秒级）。

三条断言（chip SNR=-1 dB, genie 同步）:
  ① 温和多径 [1,.5]@0.5chip（陷波带外）→ 扩频硬扛: BER == 0（上界 1e-3）;
  ② 深陷波 [1,-.9]@0.25chip（陷波压码片能量带）→ 破坏可复现: BER ∈ [0.1, 0.6];
  ③ 深陷波 + MMSE 均衡（前导 LS）→ 显著改善: BER_eq < 0.75 × BER_deep。

运行: python model/tests/test_multipath.py
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chains
import measure
from baseband import impairments as imp

SNR = -1.0
FRAMES = 40


def run(gains, delays, eq=False, seed=3, frames=FRAMES):
    rng = np.random.default_rng(seed)
    seg = [] if gains is None else [imp.multipath_stage(gains, delays)]
    seg = seg + [chains.awgn(SNR, rng)]
    if eq:
        ch = chains.link(seg, full=False, sync="genie", n_taps=6,
                         noise_var=chains.eq_noise_var(SNR), coherent=True)
    else:
        ch = chains.link(seg, full=False, sync="genie")
    e = t = 0
    for _ in range(frames):
        psdu = bytes(rng.integers(0, 256, size=20).tolist())
        sig = ch.run(payload=psdu)
        ee, tt = measure.frame_bit_errors(sig)
        e += ee
        t += tt
    return e / max(t, 1)


def main():
    b_clean = run(None, None)
    b_soft = run([1, .5], [0, .5])
    b_deep = run([1, -.9], [0, .25])
    b_eq = run([1, -.9], [0, .25], eq=True)
    print(f"clean={b_clean:.3e}  soft(0.5chip)={b_soft:.3e}  "
          f"deep(-.9@0.25)={b_deep:.3e}  eq={b_eq:.3e}")
    ok = True
    ok &= b_clean < 1e-3
    ok &= b_soft < 1e-3                       # 硬扛
    ok &= 0.10 < b_deep < 0.60                # 破坏可复现
    ok &= b_eq < 0.75 * b_deep                # 均衡改善
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
