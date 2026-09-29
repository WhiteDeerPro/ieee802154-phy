"""crc16_fcs: 已知向量 "123456789"→0x31C3 + 随机字节流 vs 黄金模型。"""
import sys
from pathlib import Path

import cocotb
import numpy as np
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "model"))
import phy_802154 as phy


async def feed_byte(dut, b):
    while dut.busy.value == 1:
        await FallingEdge(dut.clk)
    await FallingEdge(dut.clk)
    dut.data_in.value = int(b)
    dut.in_valid.value = 1
    await FallingEdge(dut.clk)
    dut.in_valid.value = 0
    while dut.busy.value == 1:   # 等 8bit 全部消化, 之后 crc_o 才有效
        await FallingEdge(dut.clk)


@cocotb.test()
async def crc16_known_vector_and_random(dut):
    cocotb.start_soon(Clock(dut.clk, 62.5, unit="ns").start())
    dut.data_in.value = 0
    dut.in_valid.value = 0
    dut.init.value = 0
    dut.rst_n.value = 0
    await Timer(200, unit="ns")
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1
    await FallingEdge(dut.clk)

    # 已知向量
    await FallingEdge(dut.clk)
    dut.init.value = 1
    await FallingEdge(dut.clk)
    dut.init.value = 0
    for b in b"123456789":
        await feed_byte(dut, b)
    crc = int(dut.crc_o.value)
    assert crc == 0x31C3, f"已知向量失败: {crc:#06x} != 0x31C3"
    dut._log.info("known vector 123456789 -> 0x31C3 OK")

    # 随机长帧 vs 黄金模型
    rng = np.random.default_rng(3)
    data = bytes(rng.integers(0, 256, size=64).tolist())
    await FallingEdge(dut.clk)
    dut.init.value = 1
    await FallingEdge(dut.clk)
    dut.init.value = 0
    for b in data:
        await feed_byte(dut, b)
    crc = int(dut.crc_o.value)
    expected = phy.crc16_fcs(data)
    assert crc == expected, f"随机帧 CRC: {crc:#06x} != {expected:#06x}"
    dut._log.info("crc16_fcs PASS: 已知向量 + 64 字节随机帧全匹配")
