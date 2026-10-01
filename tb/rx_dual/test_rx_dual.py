# -*- coding: utf-8 -*-
"""test_rx_dual.py —— 双通道并行接收的 cocotb 验证

测试 1：同一份 IQ 含 CFO=100 kHz，两通道各带不同旋性假设
  通道 A（完整同步器, 16 候选扫描）：phase_inc = 正确值 → 解出
  通道 B（精简同步器, 定时外置）：不消旋 → 失败
  → 验证多通道形态与"每通道自带消旋器"

测试 2：精简同步器（preamble_lock, 无扫描）与完整版功能等价
  先用通道 A（扫描）量出本帧相位 → 复位 → 把相位交给通道 B（精简）
  → 通道 B 应同样解出帧
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
INC_OK = int(round(CFO_HZ * PHASE_FS / FS))
GEN_DIR = Path("/tmp/rx_dual_gen")


def load_adc(cfo_hz=CFO_HZ, snr_db=20.0, seed=1, gap_jitter=16):
    """用 mc_gen 生成一帧激励, 读回 (i_q, q_q) 12bit 有符号。

    gap_jitter=1 时帧到达相位固定 —— 测试 2 需要（相位由第一遍量出后复用）。
    """
    mc_gen.gen_point(snr_db, 1, 20, 8.0, seed, 400, 600, GEN_DIR,
                     cfo_hz=cfo_hz, gap_jitter=gap_jitter)
    raw = np.fromfile(GEN_DIR / "mem.bin", dtype=">u4")
    i_s = (raw & 0xFFF).astype(int)
    q_s = ((raw >> 12) & 0xFFF).astype(int)
    i_s = np.where(i_s >= 2048, i_s - 4096, i_s)
    q_s = np.where(q_s >= 2048, q_s - 4096, q_s)
    return i_s, q_s


async def _drive(dut, i_s, q_s):
    """驱动 ADC, 返回 (A 是否解出, B 是否解出)。"""
    ok_a = ok_b = False
    for i, q in zip(i_s, q_s):
        dut.adc_i.value = int(i)
        dut.adc_q.value = int(q)
        dut.adc_dv.value = 1
        await RisingEdge(dut.clk)
        ok_a = ok_a or int(dut.fcs_ok_a.value) == 1
        ok_b = ok_b or int(dut.fcs_ok_b.value) == 1
    dut.adc_dv.value = 0
    for _ in range(2000):                    # 帧尾排空
        await RisingEdge(dut.clk)
        ok_a = ok_a or int(dut.fcs_ok_a.value) == 1
        ok_b = ok_b or int(dut.fcs_ok_b.value) == 1
    return ok_a, ok_b


async def _config(dut, inc_a=0, inc_b=0, lock_a=0, lock_b=0,
                  ph_a=0, ph_b=0):
    dut.phase_inc_a.value = inc_a
    dut.phase_inc_b.value = inc_b
    dut.ext_lock_en_a.value = lock_a
    dut.ext_lock_en_b.value = lock_b
    dut.ext_lock_phase_a.value = ph_a
    dut.ext_lock_phase_b.value = ph_b


@cocotb.test()
async def dual_channel_with_distinct_hypotheses(dut):
    """通道 A 正确消旋解出；通道 B 不消旋失败。"""
    cocotb.start_soon(_clock(dut))
    await _reset(dut)

    await _config(dut, inc_a=INC_OK, inc_b=0, lock_a=0, lock_b=0)
    dut.ph_thresh.value = 2 * 10 ** 11
    dut.sfd_thresh.value = 3 * 10 ** 13

    i_s, q_s = load_adc()
    ok_a, ok_b = await _drive(dut, i_s, q_s)
    dut._log.info(f"通道A(正确消旋) fcs_ok={int(ok_a)}   "
                  f"通道B(不消旋) fcs_ok={int(ok_b)}")
    assert ok_a, "通道 A 用正确消旋参数应当解出帧"
    assert not ok_b, "通道 B 未消旋（CFO=100kHz）不应解出帧"
    assert int(dut.any_fcs_ok.value) == 1


@cocotb.test()
async def direct_lock_equivalent_to_scan(dut):
    """精简同步器（无扫描, 相位外置）与完整版等价。"""
    cocotb.start_soon(_clock(dut))
    await _reset(dut)
    dut.ph_thresh.value = 2 * 10 ** 11
    dut.sfd_thresh.value = 3 * 10 ** 13

    i_s, q_s = load_adc(gap_jitter=1)        # 相位固定, 两遍一致

    # 第 1 遍: 通道 A（扫描）量出相位
    await _config(dut, inc_a=INC_OK, inc_b=0)
    ok_a1, _ = await _drive(dut, i_s, q_s)
    ph = int(dut.locked_phase_a.value)
    dut._log.info(f"第 1 遍: 扫描锁定相位 = {ph}, A 解出={int(ok_a1)}")
    assert ok_a1, "扫描路径应当解出帧"

    # 复位后第 2 遍: 通道 B 用精简同步器 + 该相位
    await _reset(dut)
    dut.ph_thresh.value = 2 * 10 ** 11      # 复位会清门限, 必须重设
    dut.sfd_thresh.value = 3 * 10 ** 13
    await _config(dut, inc_a=INC_OK, inc_b=INC_OK, lock_b=1, ph_b=ph)
    ok_a2, ok_b2 = await _drive(dut, i_s, q_s)
    dut._log.info(f"第 2 遍: A(扫描) fcs_ok={int(ok_a2)}   "
                  f"B(精简, phase={ph}) fcs_ok={int(ok_b2)}")
    # 诊断: 精简同步器内部状态
    try:
        dut._log.info(f"  诊断 B: sfd_n={int(dut.wav_lkB_n.value)}, "
                      f"sfd_found={int(dut.wav_lkB_found.value)}, "
                      f"fs={int(dut.wav_lkB_fs.value)}")
    except Exception as e:                                   # noqa: BLE001
        dut._log.info(f"  诊断访问失败: {e}")
    assert ok_b2, "精简同步器（preamble_lock + 外部相位）应当解出帧"


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
