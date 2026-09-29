# -*- coding: utf-8 -*-
"""
test_migrated_scripts —— 迁移后的 run 脚本 与 原逻辑参照 的等价性
==================================================================

``test_chains.py`` 验证的是**链路库**（chains/measure 与迁移前逻辑等价）；
本文件验证的是**迁移后的 run 脚本**：把脚本内部的仿真函数直接与
``test_chains`` 里的原逻辑参照实现（``ref_*``，逐字复制自迁移前脚本）对比。

被验证的对象:
  run_impairments.run_config   vs  test_chains.ref_impairment
  run_multipath_eq.run_point   vs  test_chains.ref_multipath_eq   (静态信道维度)

**Rayleigh 逐帧随机信道路径不做逐比特对比**: 迁移前的实现细节在重构中不可
恢复（原文件已覆盖、无 git 历史、无 __pycache__ 残留），且 ``rayleigh_taps``
在不显式传 ``rng`` 时使用无种子的系统随机源 —— 该维度在迁移前本身就不保证可
复现。迁移后的实现显式传入 ``rng``（见 run_multipath_eq 的 sweep3），因此
**可复现**，由本文件的可复现性检查覆盖。

运行: python model/tests/test_migrated_scripts.py   (需在 model/ 目录下)
"""

import sys
from pathlib import Path

_MODEL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_MODEL))                    # model/ (库模块)
sys.path.insert(0, str(_MODEL / "experiments"))    # model/experiments/ (被验证的实验脚本)

import numpy as np

import chains
import run_impairments as ri
import run_multipath_eq as rme
import test_chains as tc
from baseband import impairments as imp

N = 200


def main():
    tc.N_FRAMES = N
    ri.N_FRAMES = N
    rme.N_FRAMES = N

    results = []

    def check(tag, a, b):
        ok = a == b
        results.append(ok)
        print(f"  {'MATCH' if ok else 'DIFF <<<'}  {tag:<26} 参照={a}  迁移后={b}")

    print("== run_impairments.run_config vs 原逻辑 ref_impairment ==")
    for name, kw in (
        ("genie + CFO 50kHz", dict(cfo_hz=50e3, sync="genie")),
        ("honest + CFO 20kHz", dict(cfo_hz=20e3, sync="honest")),
        ("assist + eps 0.3chip", dict(eps=0.3, sync="assist")),
        ("assist + eps 0.5chip", dict(eps=0.5, sync="assist")),
        ("assist + los_2tap", dict(mp=imp.SCENARIOS["los_2tap"], sync="assist")),
        ("assist + indoor_3tap", dict(mp=imp.SCENARIOS["indoor_3tap"], sync="assist")),
        ("assist + bad_3tap", dict(mp=imp.SCENARIOS["bad_3tap"], sync="assist")),
        ("assist + awgn only", dict(sync="assist")),
    ):
        er, tr = tc.ref_impairment(-1.0, np.random.default_rng(77), **kw)
        ber, _, tn = ri.run_config(-1.0, np.random.default_rng(77), **kw)
        check(name, (er, tr), (round(ber * tn), tn))

    print("== run_multipath_eq.run_point vs 原逻辑 ref_multipath_eq (静态信道) ==")
    for mode in ("base", "base_coh", "eq"):
        for nm in ("los_2tap", "indoor_3tap"):
            er, tr = tc.ref_multipath_eq(-1.0, np.random.default_rng(31),
                                         imp.SCENARIOS[nm], mode)
            ber = rme.run_point(-1.0, np.random.default_rng(31),
                                imp.SCENARIOS[nm], mode)
            check(f"{nm} / {mode}", (er, tr), (round(ber * tr), tr))

    print("== run_multipath_eq Rayleigh 维度: 可复现性 (同种子须同结果) ==")

    def ray(dmax, seed, frames=40):
        rme.N_FRAMES = frames
        rng = np.random.default_rng(seed)

        def getter():
            g, d = imp.rayleigh_taps(5, dmax, rms_chips=max(dmax / 3, 0.15), rng=rng)
            return dict(gains=g, delays=d)

        return rme.run_point(-1.0, rng, None, "base", mp_getter=getter)

    for dmax in (0.5, 2.0):
        a, b = ray(dmax, 777), ray(dmax, 777)
        results.append(a == b)
        print(f"  {'可复现 OK' if a == b else '不可复现 <<<'}  dmax={dmax}: "
              f"{a:.6f} vs {b:.6f}")

    print()
    n_pass = sum(results)
    print(f"RESULT: {n_pass}/{len(results)} PASS")
    if n_pass != len(results):
        print("FAIL: 迁移改变了数值行为")
        sys.exit(1)
    print("PASS: 迁移后的 run 脚本与其原逻辑参照逐比特一致")


if __name__ == "__main__":
    main()
