"""preamble_sync: 含噪突发帧下与黄金镜像逐位比对。

验证内容: detect 标志、locked_phase、锁后码片流、frame_start 码片序号。
RTL 与 phy_802154.preamble_sync_mirror 实现同一整数算法, 结果必须完全一致。
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
PH_THRESH = 2 * 10 ** 11         # 块间自相关 r 门限 (标定: 前导 ~5e11, 噪声 ~1e8)
SFD_THRESH = 3 * 10 ** 13        # SFD 全窗能量门限 (标定: SFD 峰 7.2e13, 前导区最大 1.3e13)
TAIL = 600
SHR_SYMBOLS = 10          # SHR = 5 字节 = 10 符号


def s21(v):
    v = int(v) & 0x1FFFFF
    return v - (1 << 21) if v >= (1 << 20) else v


@cocotb.test()
async def preamble_sync_bit_true(dut):
    cocotb.start_soon(Clock(dut.clk, 62.5, unit="ns").start())
    for sig, v in (("i_in", 0), ("q_in", 0), ("dv_in", 0), ("frame_done", 0),
                   ("ph_thresh", PH_THRESH), ("sfd_thresh", SFD_THRESH),
                   ("ext_lock_en", 0), ("ext_lock_phase", 0)):
        getattr(dut, sig).value = v
    dut.rst_n.value = 0
    await Timer(200, unit="ns")
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1

    # ---- 构造含噪突发帧 (确定性) ----
    # 链路: TX 成形信号 + 噪 → 匹配滤波(整数卷积, 同 rx_matched_filter)
    # MF 把 I/Q 半码片偏移重新集中到统一峰值相位 (g=h*h, 峰 = chip·Σh²·SCALE)
    rng = np.random.default_rng(21)
    psdu = bytes(rng.integers(0, 256, size=8).tolist())
    i_f, q_f = phy.modulate_oqpsk_fixed(phy.tx_symbols(psdu).tolist())
    coef = np.array(phy.fixed_half_sine())
    raw_i = [int(round(NOISE * rng.standard_normal())) for _ in range(GAP)]
    raw_q = [int(round(NOISE * rng.standard_normal())) for _ in range(GAP)]
    for k in range(len(i_f)):
        raw_i.append(int(i_f[k]) * SCALE + int(round(NOISE * rng.standard_normal())))
        raw_q.append(int(q_f[k]) * SCALE + int(round(NOISE * rng.standard_normal())))
    tail0 = len(raw_i)
    raw_i += [int(round(NOISE * rng.standard_normal())) for _ in range(TAIL)]
    raw_q += [int(round(NOISE * rng.standard_normal())) for _ in range(TAIL)]
    i_seq = np.convolve(np.array(raw_i), coef).astype(int).tolist()
    q_seq = np.convolve(np.array(raw_q), coef).astype(int).tolist()
    total = len(i_seq)

    # ---- 黄金镜像 ----
    ref = phy.preamble_sync_mirror(i_seq, q_seq, PH_THRESH, SFD_THRESH)
    assert ref["detect"], "镜像未检出 (测试构造问题)"
    assert ref["frame_start_idx"] is not None, "镜像未找到 SFD"

    # ---- RTL ----
    rtl_chips, rtl_fs, rtl_detect = [], None, 0
    chip_cnt = 0
    fs_seen = False
    for k in range(total):
        dut.i_in.value = i_seq[k]
        dut.q_in.value = q_seq[k]
        dut.dv_in.value = 1
        await FallingEdge(dut.clk)
        if dut.chip_dv.value == 1:
            rtl_chips.append((s21(dut.chip_i.value), s21(dut.chip_q.value)))
            if dut.frame_start.value == 1:
                rtl_fs = chip_cnt
                fs_seen = True
            chip_cnt += 1
    rtl_detect = int(dut.detect.value)
    dut.dv_in.value = 0

    assert rtl_detect == 1, "RTL 未检出"
    assert int(dut.locked_phase.value) == ref["locked_phase"], (
        f"锁定相位: RTL {int(dut.locked_phase.value)} != 镜像 {ref['locked_phase']}")
    assert rtl_chips == ref["chips"], (
        f"码片流不匹配: RTL {len(rtl_chips)} 片 vs 镜像 {len(ref['chips'])} 片, "
        f"前 3 差异 {[ (i,a,b) for i,(a,b) in enumerate(zip(rtl_chips,ref['chips'])) if a!=b ][:3]}")
    assert rtl_fs == ref["frame_start_idx"], (
        f"frame_start: RTL 码片#{rtl_fs} != 镜像 码片#{ref['frame_start_idx']}")

    # ---- 功能断言: 同步位置必须真的对 ----
    # 镜像码片流从 frame_start 起的前 len(want_syms) 符号按 32 片/符号解扩,
    # 应解出完整符号流的后半 (跳过 SHR)。注意锁后码片流尾部带有噪尾, 只取帧内部分。
    want_syms = phy.tx_symbols(psdu)[SHR_SYMBOLS:]
    tail = ref["chips"][ref["frame_start_idx"]:]
    payload_chips = np.array([complex(c[0], c[1]) for c in tail])  # (di,dq) → 复数码片
    n_sym = len(payload_chips) // 32
    assert n_sym >= len(want_syms), (
        f"帧内符号数不足: {n_sym} < {len(want_syms)} (frame_start 位置可疑)")
    got_syms = phy.despread_chips(payload_chips[: len(want_syms) * 32])
    assert (got_syms == want_syms).all(), (
        f"解扩符号不匹配: got {got_syms.tolist()} != want {want_syms.tolist()}")
    dut._log.info(f"功能断言通过: frame_start 起 {len(want_syms)} 符号解扩 == TX 符号流[SHR:]")
    dut._log.info(
        f"preamble_sync PASS: 相位{ref['locked_phase']} 锁定, "
        f"{len(rtl_chips)} 码片一致, frame_start@码片{rtl_fs}")
