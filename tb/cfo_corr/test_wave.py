"""波形观测: 跑一个 96 kHz CFO 案例, dump VCD 供 GTKWave 查看。

只跑估计(2048 拍) + 消旋(4096 拍) 两段, 顶层 $dumpvars(1) 仅 13 个端口,
VCD 体积控制在 MB 级 (全层次 dump 会到几百 MB, GTKWave 打不开)。
"""
import sys
from pathlib import Path

import cocotb
import numpy as np
from cocotb.triggers import FallingEdge

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "model"))
import phy_802154 as phy
from test_cfo_corr import drive, reset, sgn
from test_rtl_lab import frame_fixed, N_PRE_SMP

CFO = 96e3


@cocotb.test()
async def wave_case(dut):
    await reset(dut)
    rng = np.random.default_rng(7)
    psdu = bytes(rng.integers(0, 256, size=8).tolist())
    syms = phy.ppdu_symbols(psdu)
    i_mf, q_mf = frame_fixed(syms, CFO)

    # 段 1: 估计 (前导 2048 拍)
    dut.est_start.value = 1
    await FallingEdge(dut.clk)
    dut.est_start.value = 0
    await drive(dut, i_mf, q_mf, N_PRE_SMP)
    for _ in range(80):
        await FallingEdge(dut.clk)
        if dut.est_done.value == 1:
            break
    pi = sgn(dut.phase_inc.value, 24)
    print(f"  [wave] CFO={CFO/1e3:.0f}k -> p_hat={int(dut.p_hat.value)} "
          f"phase_inc={pi} (f={pi/(1<<24)*16e6/1e3:.3f} kHz)")

    # 段 2: 消旋 (4096 拍)
    dut.rot_load.value = 1
    await FallingEdge(dut.clk)
    dut.rot_load.value = 0
    await drive(dut, i_mf, q_mf, 4096)
    dut.dv_in.value = 0
    for _ in range(20):
        await FallingEdge(dut.clk)
    print("  [wave] VCD 段结束")
