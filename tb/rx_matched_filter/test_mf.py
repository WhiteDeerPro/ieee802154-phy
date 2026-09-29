"""rx_matched_filter: TX 固定点输出过匹配滤波 vs numpy 整数卷积。"""
import sys
from pathlib import Path

import cocotb
import numpy as np
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "model"))
import phy_802154 as phy

H = phy.fixed_half_sine().tolist()
N = 400


def s21(v):
    v = int(v) & 0x1FFFFF
    return v - (1 << 21) if v >= (1 << 20) else v


@cocotb.test()
async def rx_mf_bit_true(dut):
    cocotb.start_soon(Clock(dut.clk, 62.5, unit="ns").start())
    dut.i_in.value = 0
    dut.q_in.value = 0
    dut.dv_in.value = 0
    dut.rst_n.value = 0
    await Timer(200, unit="ns")
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1

    rng = np.random.default_rng(5)
    i_x = rng.integers(-2000, 2000, size=N).tolist()
    q_x = rng.integers(-2000, 2000, size=N).tolist()
    i_exp = np.convolve(np.array(i_x), np.array(H)).astype(int).tolist()
    q_exp = np.convolve(np.array(q_x), np.array(H)).astype(int).tolist()

    i_got, q_got = [], []

    async def monitor():
        for k in range(2, N + 10):
            i_got.append(s21(dut.i_out.value))
            q_got.append(s21(dut.q_out.value))
            await FallingEdge(dut.clk)

    mon = cocotb.start_soon(monitor())
    for a, b in zip(i_x, q_x):
        dut.i_in.value = int(a)
        dut.q_in.value = int(b)
        dut.dv_in.value = 1
        await FallingEdge(dut.clk)
    dut.dv_in.value = 0
    await mon

    n = N + 7  # convolve 全长
    di = [(k, a, b) for k, (a, b) in enumerate(zip(i_got[2:2 + n], i_exp[:n])) if a != b]
    assert not di, f"I MF {len(di)} 处不匹配, 前 5: {di[:5]}"
    dq = [(k, a, b) for k, (a, b) in enumerate(zip(q_got[2:2 + n], q_exp[:n])) if a != b]
    assert not dq, f"Q MF {len(dq)} 处不匹配, 前 5: {dq[:5]}"
    dut._log.info(f"rx_matched_filter PASS: {n} 采样 I/Q 全匹配")
