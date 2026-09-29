"""half_sine_fir: 单脉冲冲击响应 + 随机流 vs numpy 整数卷积。"""
import sys
from pathlib import Path

import cocotb
import numpy as np
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "model"))
import phy_802154 as phy

H = phy.fixed_half_sine().tolist()


async def run_stream(dut, x):
    """注入整数序列 (0 表示 dv=0), 返回 y 序列 (与 x 对齐, RTL 2 拍延迟已在内部对齐)"""
    y = []
    for v in x:
        await FallingEdge(dut.clk)
        dut.x.value = int(v)
        dut.dv_in.value = 1 if v != 0 else 0
    # 尾部拍零, 收完 FIR 拖尾
    for _ in range(10):
        await FallingEdge(dut.clk)
        dut.x.value = 0
        dut.dv_in.value = 0
    return None  # 采样在 monitor 中做


@cocotb.test()
async def fir_impulse_and_random(dut):
    cocotb.start_soon(Clock(dut.clk, 62.5, unit="ns").start())
    dut.x.value = 0
    dut.dv_in.value = 0
    dut.rst_n.value = 0
    await Timer(200, unit="ns")
    dut.rst_n.value = 1
    await FallingEdge(dut.clk)

    # 统一测试: 随机 ±1 脉冲流 (相邻脉冲间隔 ≥8, 模拟码片注入)
    rng = np.random.default_rng(7)
    impulses = rng.choice([-1, 1], size=40)
    stream = []
    for v in impulses:
        stream.append(v)
        stream.extend([0] * 7)
    expected = np.convolve(np.array(stream), np.array(H)).astype(int).tolist()

    samples = []

    async def monitor():
        # y 相对 x 有 2 拍延迟 (pipe 寄存 + acc 寄存)
        await Timer(1, unit="ns")
        for i in range(len(stream) + 10):
            if i >= 2:
                v = int(dut.y.value)
                samples.append(v - 4096 if v >= 2048 else v)   # 12bit 有符号解读
            await FallingEdge(dut.clk)

    mon = cocotb.start_soon(monitor())
    for v in stream:
        dut.x.value = int(v)
        dut.dv_in.value = 1
        await FallingEdge(dut.clk)
    dut.x.value = 0
    dut.dv_in.value = 0
    await mon

    got = samples[: len(expected)]
    assert got == expected, (
        f"FIR 输出不匹配, 差异 {[ (i,a,b) for i,(a,b) in enumerate(zip(got,expected)) if a!=b ][:8]}"
    )
    dut._log.info(f"half_sine_fir PASS: {len(expected)} 采样全匹配")
