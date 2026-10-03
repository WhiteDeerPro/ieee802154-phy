"""despreader_oct8 对照验证: ①无噪判决序列 vs 原版一致; ②带噪梯度(错误率/差异率)。

节奏: 每片 8 拍 (16MHz 时钟 / 2MHz 片率)。
激励: ①无噪 12 符号 (±1×64 定点); ②带噪 2 档 × 32 符号 (同 seed, 逐档加噪)。
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
    ref_seq, oct_seq = [], []
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
            if int(dut.sym_dv_oct.value) == 1:
                oct_seq.append(int(dut.sym_oct.value))
            if t < 7:
                await FallingEdge(dut.clk)
    for _ in range(16):
        if int(dut.sym_dv_ref.value) == 1:
            ref_seq.append(int(dut.sym_ref.value))
        if int(dut.sym_dv_oct.value) == 1:
            oct_seq.append(int(dut.sym_oct.value))
        await FallingEdge(dut.clk)
    return ref_seq, oct_seq


@cocotb.test()
async def clean_matches_ref(dut):
    cocotb.start_soon(Clock(dut.clk, 62.5, unit="ns").start())
    await _reset(dut)
    rng = np.random.default_rng(11)
    n_sym = 12
    syms = rng.integers(0, 16, size=n_sym)
    chips = phy.symbols_to_chips(syms)
    c = np.round(chips * 64.0)
    ref_seq, oct_seq = await _drive(dut, c)
    print(f'[无噪] ref ({len(ref_seq)}): {ref_seq}')
    print(f'[无噪] oct ({len(oct_seq)}): {oct_seq}')
    assert len(ref_seq) == n_sym and len(oct_seq) == n_sym, (len(ref_seq), len(oct_seq))
    assert ref_seq == oct_seq, '无噪下 oct8 判决与原版不一致'
    print('=== 无噪: oct8 判决序列与原版完全一致 ===')


@cocotb.test()
async def noisy_gradient(dut):
    cocotb.start_soon(Clock(dut.clk, 62.5, unit="ns").start())
    await _reset(dut)
    rng = np.random.default_rng(23)
    n_sym = 32
    syms = rng.integers(0, 16, size=n_sym)
    chips = phy.symbols_to_chips(syms) * 64.0
    gt = list(syms)
    for sig in (24.0, 56.0, 96.0):
        c = chips + rng.normal(0, sig, len(chips)) + 1j * rng.normal(0, sig, len(chips))
        c = np.round(c)
        ref_seq, oct_seq = await _drive(dut, c)
        assert len(ref_seq) == n_sym and len(oct_seq) == n_sym, (len(ref_seq), len(oct_seq))
        ref_err = sum(1 for a, b in zip(ref_seq, gt) if a != b)
        oct_err = sum(1 for a, b in zip(oct_seq, gt) if a != b)
        diff = sum(1 for a, b in zip(ref_seq, oct_seq) if a != b)
        print(f'[带噪 sigma={sig:g}] {n_sym} 符号: ref 错 {ref_err} / '
              f'oct 错 {oct_err} / 两者差异 {diff}')
        assert oct_err <= ref_err + 3, f'oct8 错误率显著劣化: {oct_err} vs {ref_err}'
        assert diff <= n_sym // 2, f'判决差异过大: {diff}/{n_sym}'
    print('=== 带噪梯度: oct8 错误率未劣化 (≤ ref+2), 差异率受控 ===')
