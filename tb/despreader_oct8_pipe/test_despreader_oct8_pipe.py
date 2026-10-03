"""despreader_oct8_pipe 对照验证: 流水判决树 vs 组合版。

断言: 判决序列**完全一致**（流水版整体延迟若干拍——打印实测延迟, 不做固定值断言）。
节奏: 每片 8 拍; 激励: 无噪 12 符号 + 带噪 2 档 × 32 符号。
"""
import sys
from pathlib import Path

import cocotb
import numpy as np
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "model"))
import phy_802154 as phy            # noqa: E402


async def _reset(dut):
    dut.chip_i.value = 0
    dut.chip_q.value = 0
    dut.chip_dv.value = 0
    dut.frame_start.value = 0
    dut.rst_n.value = 0
    await Timer(200, unit="ns")
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1
    await FallingEdge(dut.clk)


async def _drive(dut, c):
    ref_seq, pipe_seq = [], []
    ref_at, pipe_at = [], []
    tick = 0
    for m in range(len(c)):
        dut.chip_i.value = int(c[m].real)
        dut.chip_q.value = int(c[m].imag)
        dut.chip_dv.value = 1
        dut.frame_start.value = 1 if (m % 32) == 0 else 0
        await FallingEdge(dut.clk)
        dut.chip_dv.value = 0
        dut.frame_start.value = 0
        for t in range(8):
            if int(dut.sym_dv_ref.value) == 1:
                ref_seq.append(int(dut.sym_ref.value))
                ref_at.append(tick)
            if int(dut.sym_dv_pipe.value) == 1:
                pipe_seq.append(int(dut.sym_pipe.value))
                pipe_at.append(tick)
            tick += 1
            if t < 7:
                await FallingEdge(dut.clk)
    for _ in range(32):
        if int(dut.sym_dv_ref.value) == 1:
            ref_seq.append(int(dut.sym_ref.value))
            ref_at.append(tick)
        if int(dut.sym_dv_pipe.value) == 1:
            pipe_seq.append(int(dut.sym_pipe.value))
            pipe_at.append(tick)
        tick += 1
        await FallingEdge(dut.clk)
    return ref_seq, pipe_seq, ref_at, pipe_at


def _report(ref_seq, pipe_seq, ref_at, pipe_at, tag):
    print(f'[{tag}] ref({len(ref_seq)}): {ref_seq}')
    print(f'[{tag}] pipe({len(pipe_seq)}): {pipe_seq}')
    if ref_at and pipe_at and len(ref_at) == len(pipe_at):
        delays = [p - r for r, p in zip(ref_at, pipe_at)]
        print(f'[{tag}] 实测延迟(拍): min={min(delays)} max={max(delays)}')
    assert len(ref_seq) == len(pipe_seq), (len(ref_seq), len(pipe_seq))
    assert ref_seq == pipe_seq, '流水判决序列与组合版不一致!'


@cocotb.test()
async def clean_matches(dut):
    cocotb.start_soon(Clock(dut.clk, 62.5, unit="ns").start())
    await _reset(dut)
    rng = np.random.default_rng(13)
    n_sym = 12
    syms = rng.integers(0, 16, size=n_sym)
    chips = phy.symbols_to_chips(syms)
    c = np.round(chips * 64.0)
    ref_seq, pipe_seq, ref_at, pipe_at = await _drive(dut, c)
    _report(ref_seq, pipe_seq, ref_at, pipe_at, '无噪')
    assert ref_seq == list(syms), 'ref 序列与激励不符'
    print('=== 无噪: 流水判决序列与组合版完全一致 ===')


@cocotb.test()
async def noisy_matches(dut):
    cocotb.start_soon(Clock(dut.clk, 62.5, unit="ns").start())
    await _reset(dut)
    rng = np.random.default_rng(29)
    for sig in (24.0, 96.0):
        n_sym = 32
        syms = rng.integers(0, 16, size=n_sym)
        chips = phy.symbols_to_chips(syms) * 64.0
        c = np.round(chips + rng.normal(0, sig, len(chips))
                     + 1j * rng.normal(0, sig, len(chips)))
        ref_seq, pipe_seq, ref_at, pipe_at = await _drive(dut, c)
        _report(ref_seq, pipe_seq, ref_at, pipe_at, f'带噪 sigma={sig:g}')
    print('=== 带噪: 流水判决序列与组合版完全一致 ===')
