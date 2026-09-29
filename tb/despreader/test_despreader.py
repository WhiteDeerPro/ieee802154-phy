"""despreader: 复数码片软值 → argmax|相关| 与黄金模型 despread_chips 符号级比对。

构造: 已知符号 → 码片(±1) → 相位旋转 θ + 噪声 → ×64 定点 → 送 RTL。
RTL 算法 = despread_chips 算法 (16 路相关取 |·|² 最大), 旋转/反相由幅值检测天然容忍。
"""
import sys
from pathlib import Path

import cocotb
import numpy as np
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "model"))
import phy_802154 as phy


def to_s12(v):
    v = int(v) & 0xFFF
    return v - 4096 if v >= 2048 else v


async def feed_symbol(dut, c):
    """送 32 个复数码片 (c: complex list, 已 ×64 定点)"""
    for m in range(32):
        dut.chip_i.value = int(round(c[m].real))
        dut.chip_q.value = int(round(c[m].imag))
        dut.chip_dv.value = 1
        await FallingEdge(dut.clk)
    dut.chip_dv.value = 0
    # 等 sym_dv
    for _ in range(80):
        if dut.sym_dv.value == 1:
            return int(dut.sym.value)
        await FallingEdge(dut.clk)
    raise RuntimeError("sym_dv 超时")


@cocotb.test()
async def despreader_matches_golden(dut):
    cocotb.start_soon(Clock(dut.clk, 62.5, unit="ns").start())
    dut.chip_i.value = 0
    dut.chip_q.value = 0
    dut.chip_dv.value = 0
    dut.frame_start.value = 0
    dut.rst_n.value = 0
    await Timer(200, unit="ns")
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1
    await FallingEdge(dut.clk)

    rng = np.random.default_rng(9)
    n_sym = 12
    syms = rng.integers(0, 16, size=n_sym)

    for case, (theta, sigma) in enumerate([(0.0, 0.0), (0.6, 8.0)]):
        chips = phy.symbols_to_chips(syms)                    # (n_sym*32,) ±1
        c = chips * np.exp(1j * theta) * 64.0
        if sigma > 0:
            c = c + sigma * (rng.standard_normal(len(c))
                             + 1j * rng.standard_normal(len(c)))
        c = c.reshape(n_sym, 32)
        expected = phy.despread_chips(c.reshape(-1))
        got = []
        for s in range(n_sym):
            got.append(await feed_symbol(dut, c[s]))
        assert got == expected.tolist(), (
            f"case {case} (θ={theta}, σ={sigma}): "
            f"{[(i, a, b) for i, (a, b) in enumerate(zip(got, expected)) if a != b][:5]}"
        )
        dut._log.info(f"case {case} (θ={theta}, σ={sigma}): {n_sym} 符号全匹配")
    dut._log.info("despreader PASS")
