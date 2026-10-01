# -*- coding: utf-8 -*-
"""test_rx_dual.py —— 双通道并行接收的 cocotb 验证（每通道自带消旋器）

场景：一帧含 CFO = 100 kHz 的信号，**同一份 IQ 同时喂给两个通道**：
  通道 A：ext_inc = 100 kHz 对应的定点值  → 期望解出（fcs_ok_a = 1）
  通道 B：ext_inc = 0（不消旋）            → 期望失败（fcs_ok_b = 0）

这验证 docs/16 §8.3 的形态：**假设殊异 ⇒ 每通道必须持有自己的消旋器**。
激励复用 mc_gen（与 monte-carlo 平台同一份生成器，含 CFO 与 AWGN）。
"""
import sys
from pathlib import Path

import cocotb
from cocotb.triggers import RisingEdge, Timer

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tb" / "rx_chain_e2e"))

import numpy as np          # noqa: E402
import mc_gen               # noqa: E402

FS = 16e6
PHASE_FS = 2 ** 24
CFO_HZ = 100e3
GEN_DIR = Path("/tmp/rx_dual_gen")


def load_adc(cfo_hz=CFO_HZ, snr_db=20.0, seed=1):
    """用 mc_gen 生成一帧激励, 读回 (i_q, q_q) 12bit 有符号。"""
    mc_gen.gen_point(snr_db, 1, 20, 8.0, seed, 400, 600, GEN_DIR, cfo_hz=cfo_hz)
    raw = np.fromfile(GEN_DIR / "mem.bin", dtype=">u4")
    i_s = (raw & 0xFFF).astype(int)
    q_s = ((raw >> 12) & 0xFFF).astype(int)
    i_s = np.where(i_s >= 2048, i_s - 4096, i_s)
    q_s = np.where(q_s >= 2048, q_s - 4096, q_s)
    return i_s, q_s


@cocotb.test()
async def dual_channel_with_distinct_hypotheses(dut):
    cocotb.start_soon(_clock(dut))
    await _reset(dut)

    # 通道 A: 正确消旋；通道 B: 不消旋
    inc_ok = int(round(CFO_HZ * PHASE_FS / FS))
    dut.phase_inc_a.value = inc_ok
    dut.phase_inc_b.value = 0
    dut.phase_off_a.value = 0
    dut.phase_off_b.value = 0
    dut.ext_lock_en_a.value = 0
    dut.ext_lock_en_b.value = 0
    dut.ph_thresh.value = 2 * 10 ** 11
    dut.sfd_thresh.value = 3 * 10 ** 13

    i_s, q_s = load_adc()
    ok_a = ok_b = False
    for i, q in zip(i_s, q_s):
        dut.adc_i.value = int(i)
        dut.adc_q.value = int(q)
        dut.adc_dv.value = 1
        await RisingEdge(dut.clk)
        if int(dut.frame_done_a.value):
            ok_a = ok_a or int(dut.fcs_ok_a.value) == 1
        if int(dut.frame_done_b.value):
            ok_b = ok_b or int(dut.fcs_ok_b.value) == 1
    dut.adc_dv.value = 0
    for _ in range(2000):
        await RisingEdge(dut.clk)
        ok_a = ok_a or int(dut.fcs_ok_a.value) == 1
        ok_b = ok_b or int(dut.fcs_ok_b.value) == 1

    dut._log.info(f"通道 A (正确消旋): fcs_ok={int(ok_a)}   "
                  f"通道 B (不消旋): fcs_ok={int(ok_b)}")
    assert ok_a, "通道 A 用正确消旋参数应当解出帧"
    assert not ok_b, "通道 B 未消旋（CFO=100kHz）不应解出帧"
    assert int(dut.any_fcs_ok.value) == 1, "汇总信号应为 1（至少一路成功）"


async def _clock(dut):
    while True:
        dut.clk.value = 0
        await Timer(31, unit="ns")
        dut.clk.value = 1
        await Timer(31, unit="ns")


async def _reset(dut):
    for sig, v in (("adc_i", 0), ("adc_q", 0), ("adc_dv", 0),
                   ("phase_inc_a", 0), ("phase_off_a", 0),
                   ("ext_lock_en_a", 0), ("ext_lock_phase_a", 0),
                   ("phase_inc_b", 0), ("phase_off_b", 0),
                   ("ext_lock_en_b", 0), ("ext_lock_phase_b", 0),
                   ("rot_load", 0), ("ph_thresh", 0), ("sfd_thresh", 0)):
        getattr(dut, sig).value = v
    dut.rst_n.value = 0
    for _ in range(10):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)
