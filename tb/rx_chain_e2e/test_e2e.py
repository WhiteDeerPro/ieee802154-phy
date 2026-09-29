"""RX 全链端到端: ADC(12bit 量化) → MF → preamble_sync → despreader → rx_deframer
激励: TX 波形 ×8 + AWGN → clamp 12bit (模拟 ADC) → RTL 整链 → PSDU 字节流
期望: 黄金 phy.rx_deframe_symbols (PSDU 字节 / len / fcs_ok 全一致)
"""
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
SHR_SYMBOLS = 10
ADC_W = 12
ADC_MAX = (1 << (ADC_W - 1)) - 1
ADC_MIN = -(1 << (ADC_W - 1))


def adc_q(v):
    """模拟 ADC: 四舍五入 + 饱和到 12bit 有符号"""
    v = int(round(v))
    return max(ADC_MIN, min(ADC_MAX, v))


@cocotb.test()
async def e2e_adc_to_bits(dut):
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

    # TX 波形 ×8 + AWGN → ADC 量化 (与 test_rx_chain 同 SNR 构造, 但量化在 RTL 入口)
    raw_i = [adc_q(NOISE * rng.standard_normal()) for _ in range(GAP)]
    raw_q = [adc_q(NOISE * rng.standard_normal()) for _ in range(GAP)]
    for k in range(len(i_f)):
        raw_i.append(adc_q(i_f[k] * SCALE + NOISE * rng.standard_normal()))
        raw_q.append(adc_q(q_f[k] * SCALE + NOISE * rng.standard_normal()))
    raw_i += [adc_q(NOISE * rng.standard_normal()) for _ in range(TAIL)]
    raw_q += [adc_q(NOISE * rng.standard_normal()) for _ in range(TAIL)]

    exp_psdu, exp_ok, exp_len = phy.rx_deframe_symbols(phy.tx_symbols(psdu)[SHR_SYMBOLS:])
    assert exp_ok and exp_psdu == psdu and exp_len == 8, "黄金镜像自检失败"

    data, lens, fcs, dones = [], [], [], 0
    detect_seen = 0
    for k in range(len(raw_i)):
        dut.i_in.value = raw_i[k]
        dut.q_in.value = raw_q[k]
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

    # detect 随帧尾回扫描态拉低 (帧期间为高): 断言“曾检出”而非“帧尾仍为高”
    assert detect_seen == 1, "RTL 未检出前导"
    assert dones == 1, f"frame_done 次数 {dones} != 1"
    assert lens == [exp_len], f"psdu_len {lens} != [{exp_len}]"
    assert fcs == [1], f"fcs_ok {fcs} != [1]"
    assert bytes(data) == exp_psdu, (
        f"PSDU 不匹配: got {bytes(data).hex()} != want {exp_psdu.hex()}")
    dut._log.info(
        f"e2e PASS: ADC(12bit) → MF → sync → despread → deframe, "
        f"PSDU {len(data)} 字节还原, fcs_ok=1, 相位{int(dut.locked_phase.value)} 锁定")
