"""despreader_tdm 对照验证: ①判决序列 vs 原版一致 ②平方单元数据流时间线。

节奏: 每片 8 拍（16MHz 时钟 / 2MHz 片率——TDM 的设计假设）。
激励: 12 符号 → 码片（±1×64 定点, 无噪——先验功能）。
"""
import sys
from pathlib import Path

import cocotb
import numpy as np
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "model"))
import phy_802154 as phy            # noqa: E402


def _rd(sig):
    try:
        return int(sig.value)
    except Exception:
        return -1


@cocotb.test()
async def tdm_matches_ref_and_trace(dut):
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

    rng = np.random.default_rng(7)
    n_sym = 12
    syms = rng.integers(0, 16, size=n_sym)
    chips = phy.symbols_to_chips(syms)
    c = np.round(chips * 64.0)

    ref_seq, tdm_seq = [], []
    trace_on = False

    for m in range(len(c)):
        fs = 1 if (m % 32) == 0 else 0
        if m == len(c) - 64:
            trace_on = True
            print('=== 数据流时间线（最后 2 符号窗口）===')
        dut.chip_i.value = int(c[m].real)
        dut.chip_q.value = int(c[m].imag)
        dut.chip_dv.value = 1
        dut.frame_start.value = fs
        await FallingEdge(dut.clk)
        dut.chip_dv.value = 0
        dut.frame_start.value = 0
        for t in range(8):
            if trace_on:
                sched = _rd(dut.u_tdm.sched)
                besti = _rd(dut.u_tdm.best_idx)
                mark = ''
                if sched == 0:
                    mark = '片到达: acc[0..15] 并行更新(生产 s_k)'
                elif 1 <= sched <= 6:
                    b = 3 * (sched - 1)
                    mark = f'平方单元消费 acc[{b}..{min(b + 2, 15)}] (6 乘法器)'
                elif sched == 7:
                    mark = '输出拍 (sym_dv)'
                print(f'  t+{t}: sched={sched} best_idx={besti}  {mark}')
            if _rd(dut.sym_dv_ref) == 1:
                ref_seq.append(_rd(dut.sym_ref))
            if _rd(dut.sym_dv_tdm) == 1:
                tdm_seq.append(_rd(dut.sym_tdm))
            if t < 7:
                await FallingEdge(dut.clk)

    for _ in range(16):
        if _rd(dut.sym_dv_ref) == 1:
            ref_seq.append(_rd(dut.sym_ref))
        if _rd(dut.sym_dv_tdm) == 1:
            tdm_seq.append(_rd(dut.sym_tdm))
        await FallingEdge(dut.clk)

    print(f'ref 判决序列 ({len(ref_seq)}): {ref_seq}')
    print(f'tdm 判决序列 ({len(tdm_seq)}): {tdm_seq}')
    assert len(ref_seq) == n_sym and len(tdm_seq) == n_sym, (len(ref_seq), len(tdm_seq))
    assert ref_seq == tdm_seq, 'TDM 判决序列与原版不一致!'
    print('=== TDM 判决序列与原版完全一致 ===')
