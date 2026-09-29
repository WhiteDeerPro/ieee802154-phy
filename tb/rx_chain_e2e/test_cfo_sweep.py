"""CFO 扫描: 无消旋 RTL 全链 (e2e) 的频偏容忍实测
对每个 CFO 发一帧 (TX×8+噪 → 旋转 → 12bit ADC), 记录 frame_done/fcs_ok。
无 cfo_corr 的 RTL 链容忍 = preamble_sync 块间自相关 + SFD 相关 + 解扩的联合容限。
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
ADC_W = 12
ADC_MAX = (1 << (ADC_W - 1)) - 1
ADC_MIN = -(1 << (ADC_W - 1))
FS = 16e6


def adc_q(v):
    return max(ADC_MIN, min(ADC_MAX, int(round(v))))


@cocotb.test()
async def cfo_sweep(dut):
    cocotb.start_soon(Clock(dut.clk, 62.5, unit="ns").start())
    for sig, v in (("i_in", 0), ("q_in", 0), ("dv_in", 0),
                   ("ph_thresh", PH_THRESH), ("sfd_thresh", SFD_THRESH)):
        getattr(dut, sig).value = v

    rng = np.random.default_rng(21)
    psdu = bytes(rng.integers(0, 256, size=8).tolist())
    i_f, q_f = phy.modulate_oqpsk_fixed(phy.tx_symbols(psdu).tolist())

    results = []
    for cfo_hz in (0.0, 3e3, 5e3, 8e3, 10e3, 12e3, 15e3, 20e3, 25e3, 30e3, 40e3):
        dut.rst_n.value = 0
        await Timer(200, unit="ns")
        await FallingEdge(dut.clk)
        dut.rst_n.value = 1

        raw_i = [adc_q(NOISE * rng.standard_normal()) for _ in range(GAP)]
        raw_q = [adc_q(NOISE * rng.standard_normal()) for _ in range(GAP)]
        for k in range(len(i_f)):
            raw_i.append(adc_q(i_f[k] * SCALE + NOISE * rng.standard_normal()))
            raw_q.append(adc_q(q_f[k] * SCALE + NOISE * rng.standard_normal()))
        raw_i += [adc_q(NOISE * rng.standard_normal()) for _ in range(TAIL)]
        raw_q += [adc_q(NOISE * rng.standard_normal()) for _ in range(TAIL)]

        if cfo_hz:
            # 复基带旋转: 作用于整个采样序列 (信号+噪, 噪声旋转不变)
            n = np.arange(len(raw_i))
            ri = np.array(raw_i, dtype=float)
            rq = np.array(raw_q, dtype=float)
            c = np.cos(2 * np.pi * cfo_hz * n / FS)
            s = np.sin(2 * np.pi * cfo_hz * n / FS)
            ri2 = ri * c - rq * s
            rq2 = ri * s + rq * c
            raw_i = [adc_q(v) for v in ri2]
            raw_q = [adc_q(v) for v in rq2]

        dones, fcs, detect = 0, 0, 0
        for k in range(len(raw_i)):
            dut.i_in.value = raw_i[k]
            dut.q_in.value = raw_q[k]
            dut.dv_in.value = 1
            await FallingEdge(dut.clk)
            detect |= int(dut.detect.value)   # 帧期间为高, 帧尾回扫描态拉低
            if dut.frame_done.value == 1:
                dones += 1
                fcs = int(dut.fcs_ok.value)
        dut.dv_in.value = 0
        for _ in range(50):
            await FallingEdge(dut.clk)
        ok = (dones == 1 and fcs == 1 and detect == 1)
        results.append((cfo_hz, detect, dones, fcs, ok))
        dut._log.info(f"CFO {cfo_hz/1e3:5.1f} kHz: detect={detect} done={dones} "
                      f"fcs_ok={fcs} -> {'PASS' if ok else 'FAIL'}")

    print("=== RTL e2e (无消旋) CFO 容限扫描 ===")
    for cfo_hz, det, dn, fc, ok in results:
        print(f"  {cfo_hz/1e3:5.1f} kHz: {'OK ' if ok else 'FAIL'}")
    assert results[0][4], "0 kHz 基准失败"
