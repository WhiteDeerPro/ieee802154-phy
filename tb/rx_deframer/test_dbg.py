"""临时调试: 抓取 despreader 实际输入码片与输出符号, 落盘供离线比对"""
import sys
from pathlib import Path

import cocotb
import numpy as np
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "model"))
import phy_802154 as phy

GAP, SCALE, NOISE, TAIL = 400, 8, 60.0, 600
PH_THRESH, SFD_THRESH = 2 * 10 ** 11, 3 * 10 ** 13
OUT = Path(__file__).resolve().parent / "dbg_chips.npy"


@cocotb.test()
async def dbg(dut):
    cocotb.start_soon(Clock(dut.clk, 62.5, unit="ns").start())
    for sig, v in (("i_in", 0), ("q_in", 0), ("dv_in", 0),
                   ("ph_thresh", PH_THRESH), ("sfd_thresh", SFD_THRESH)):
        getattr(dut, sig).value = v
    dut.rst_n.value = 0
    await Timer(200, unit="ns")
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1

    rng = np.random.default_rng(21)
    psdu = bytes(rng.integers(0, 256, size=8).tolist())
    i_f, q_f = phy.modulate_oqpsk_fixed(phy.tx_symbols(psdu).tolist())
    coef = np.array(phy.fixed_half_sine())
    raw_i = [int(round(NOISE * rng.standard_normal())) for _ in range(GAP)]
    raw_q = [int(round(NOISE * rng.standard_normal())) for _ in range(GAP)]
    for k in range(len(i_f)):
        raw_i.append(int(i_f[k]) * SCALE + int(round(NOISE * rng.standard_normal())))
        raw_q.append(int(q_f[k]) * SCALE + int(round(NOISE * rng.standard_normal())))
    raw_i += [int(round(NOISE * rng.standard_normal())) for _ in range(TAIL)]
    raw_q += [int(round(NOISE * rng.standard_normal())) for _ in range(TAIL)]
    i_seq = np.convolve(np.array(raw_i), coef).astype(int).tolist()
    q_seq = np.convolve(np.array(raw_q), coef).astype(int).tolist()

    rec_chips = []   # despreader 输入 (frame_start 起)
    rec_syms = []    # despreader 输出符号
    armed = False
    for k in range(len(i_seq)):
        dut.i_in.value = i_seq[k]
        dut.q_in.value = q_seq[k]
        dut.dv_in.value = 1
        await FallingEdge(dut.clk)
        if dut.u_sync.frame_start.value == 1:
            armed = True
        if armed and dut.u_desp.chip_dv.value == 1 and len(rec_chips) < 22 * 32:
            vi = int(dut.u_desp.chip_i.value)
            vq = int(dut.u_desp.chip_q.value)
            vi = vi - 4096 if vi >= 2048 else vi
            vq = vq - 4096 if vq >= 2048 else vq
            rec_chips.append((vi, vq))
        if dut.u_desp.sym_dv.value == 1:
            rec_syms.append(int(dut.u_desp.sym.value))
    np.save(OUT, {
        "chips": np.array(rec_chips), "syms": np.array(rec_syms),
        "want": phy.tx_symbols(psdu)[10:].astype(int),
        "psdu": np.frombuffer(psdu, dtype=np.uint8),
    })
    print("saved:", OUT, "chips:", len(rec_chips), "syms:", len(rec_syms))
