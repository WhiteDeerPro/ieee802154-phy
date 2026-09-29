"""RX 链级闭环: preamble_sync → despreader → rx_deframer, 含噪突发帧整链还原 PSDU。

复用 preamble_sync tb 的构造: 噪 + TX 帧 ×8 + 噪尾 → fixed_half_sine 卷积 →
preamble_sync(21bit, 阈值标定值) → >>7 → despreader(12bit) → rx_deframer。
黄金: phy.rx_deframe_symbols (自 PHR 符号起), PSDU 字节 / fcs_ok / len 必须一致。
"""
import sys
from pathlib import Path

import cocotb
import numpy as np
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "model"))
import phy_802154 as phy

GAP = 400
SCALE = 8
NOISE = 60.0
PH_THRESH = 2 * 10 ** 11
SFD_THRESH = 3 * 10 ** 13
TAIL = 600
SHR_SYMBOLS = 10


@cocotb.test()
async def rx_chain_closed_loop(dut):
    cocotb.start_soon(Clock(dut.clk, 62.5, unit="ns").start())
    for sig, v in (("i_in", 0), ("q_in", 0), ("dv_in", 0),
                   ("ph_thresh", PH_THRESH), ("sfd_thresh", SFD_THRESH)):
        getattr(dut, sig).value = v
    dut.rst_n.value = 0
    await Timer(200, unit="ns")
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1

    # ---- 含噪突发帧 (确定性, 与 preamble_sync tb 同构) ----
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

    exp_psdu, exp_ok, exp_len = phy.rx_deframe_symbols(phy.tx_symbols(psdu)[SHR_SYMBOLS:])
    assert exp_ok and exp_psdu == psdu and exp_len == 8, "黄金镜像自检失败"

    # ---- RTL 整链 ----
    data, lens, fcs, dones = [], [], [], 0
    detect_seen = 0
    for k in range(len(i_seq)):
        dut.i_in.value = i_seq[k]
        dut.q_in.value = q_seq[k]
        dut.dv_in.value = 1
        await FallingEdge(dut.clk)
        detect_seen |= int(dut.detect.value)
        if dut.data_valid.value == 1:
            data.append(int(dut.data_out.value))
        if dut.frame_done.value == 1:
            dones += 1
            lens.append(int(dut.psdu_len.value))
            fcs.append(int(dut.fcs_ok.value))
    dut.dv_in.value = 0
    for _ in range(200):
        await FallingEdge(dut.clk)

    # detect 随帧尾回扫描态拉低 (帧期间为高): 断言“曾检出”
    assert detect_seen == 1, "RTL 未检出前导"
    assert dones == 1, f"frame_done 次数 {dones} != 1 (尾部噪声不应成帧)"
    assert lens == [exp_len], f"psdu_len {lens} != [{exp_len}]"
    assert fcs == [1], f"fcs_ok {fcs} != [1]"
    assert bytes(data) == exp_psdu, (
        f"PSDU 不匹配: got {bytes(data).hex()} != want {exp_psdu.hex()}")
    dut._log.info(
        f"rx_chain PASS: 含噪整链还原 PSDU {len(data)} 字节, fcs_ok=1, "
        f"相位{int(dut.locked_phase.value)} 锁定")
