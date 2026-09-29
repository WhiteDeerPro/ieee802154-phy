"""chip_lut 与黄金模型逐码片比对。"""
import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "model"))
import phy_802154 as phy


async def drive_symbol(dut, sym):
    """在 ce 拍下载入符号并采 C0, 随后收 31 个码片"""
    await FallingEdge(dut.clk)
    dut.load.value = 1
    dut.sym.value = sym
    dut.ce.value = 1
    await FallingEdge(dut.clk)
    dut.load.value = 0
    dut.ce.value = 0
    chips = [1 if dut.chip.value == 1 else -1] if dut.chip_ce.value == 1 else []
    for _ in range(31):
        await FallingEdge(dut.clk)
        dut.ce.value = 1
        await FallingEdge(dut.clk)
        dut.ce.value = 0
        if dut.chip_ce.value == 1:
            chips.append(1 if dut.chip.value == 1 else -1)
    return chips


@cocotb.test()
async def chip_lut_matches_golden(dut):
    cocotb.start_soon(Clock(dut.clk, 62.5, unit="ns").start())
    dut.ce.value = 0
    dut.load.value = 0
    dut.sym.value = 0
    dut.rst_n.value = 0
    await Timer(200, unit="ns")
    dut.rst_n.value = 1
    await Timer(200, unit="ns")

    rng_syms = [0, 1, 2, 3, 15, 8, 14, 7, 0, 6]
    expected = phy.symbols_to_chips(rng_syms).tolist()
    got = []
    for s in rng_syms:
        got.extend(await drive_symbol(dut, s))
    assert got == expected, (
        f"码片流不匹配: 前 8 个差异 {[ (i,a,b) for i,(a,b) in enumerate(zip(got,expected)) if a!=b ][:8]}"
    )
    dut._log.info(f"chip_lut PASS: {len(rng_syms)} 符号 × 32 码片全匹配")
