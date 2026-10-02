# -*- coding: utf-8 -*-
"""test_rx_dual.py —— 双通道并行接收（共享定时恢复版）的 cocotb 验证

测试 1：同一份 IQ 含 CFO=100 kHz，两通道各带不同旋性假设（**码片级**参数）
  通道 A：phase_inc = 正确值（100 kHz 对应, ×8 到码片级）→ 解出
  通道 B：不消旋 → 失败

测试 2：定时恢复共享 + 精简路径等价性
  第 1 遍：frontend 走**扫描**（16 候选）量出相位
  第 2 遍：复位后给 ext_lock_phase, frontend 走**直锁**（跳过扫描）→ 两通道都应解出
  注：定时是共享的, 所以 ext_lock 对两通道同时生效。

每次 _drive 结束附**完整性检查**（定位"帧解不出"的分层证据）：
  · 片流 r   = RTL 消旋片流与期望码片序列的归一化滑动相关（前端/消旋是否完好）
  · 离线解扩 = 用 RTL 片流自己解扩并全偏移对齐（片流是否可解出符号）
  · 符号     = RTL despreader 实际输出 vs mc_gen 期望符号（执行段是否对齐）
"""
import sys
from pathlib import Path

import cocotb
from cocotb.triggers import RisingEdge, Timer

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tb" / "rx_chain_e2e"))

import numpy as np          # noqa: E402
import mc_gen               # noqa: E402
import phy_802154 as phy    # noqa: E402

FS = 16e6
SPS = 8
PHASE_FS = 2 ** 24
CFO_HZ = 100e3
INC_SMP = int(round(CFO_HZ * PHASE_FS / FS))     # 采样级（=104858）
INC_CHIP = INC_SMP * SPS                         # **码片级**（共享定时版的新语义）
GEN_DIR = Path("/tmp/rx_dual_gen")

# 16 个 802.15.4 扩频码 (与 rtl/rx/backend/despreader.sv 的 pn() 同序, m=0 → bit31)
_PN = [0b11011001110000110101001000101110,
       0b11101101100111000011010100100010,
       0b00101110110110011100001101010010,
       0b00100010111011011001110000110101,
       0b01010010001011101101100111000011,
       0b00110101001000101110110110011100,
       0b11000011010100100010111011011001,
       0b10011100001101010010001011101101,
       0b10001100100101100000011101111011,
       0b10111000110010010110000001110111,
       0b01111011100011001001011000000111,
       0b01110111101110001100100101100000,
       0b00000111011110111000110010010110,
       0b01100000011101111011100011001001,
       0b10010110000001110111101110001100,
       0b11001001011000000111011110111000]
_REF = np.array([[1 if (v >> (31 - m)) & 1 else -1 for m in range(32)]
                 for v in _PN], dtype=float)


def _despread(chips):
    """复现 RTL despreader: 每 32 片与 16 个 PN 复相关, argmax|·|² (自由滚动)。"""
    n = len(chips) // 32
    if n == 0:
        return np.zeros(0, dtype=int)
    corr = chips[:n * 32].reshape(n, 32) @ _REF.T
    return np.argmax(np.abs(corr) ** 2, axis=1)


def _best_align(got, exp):
    """符号级最佳对齐: 返回 (off, n_err, n_cmp)，got[j] ↔ exp[j-off]。"""
    best = None
    for off in range(-len(exp), len(got) - 1):
        lo, hi = max(0, off), min(len(got), len(exp) + off)
        if hi - lo < 20:
            continue
        n_err = int(np.count_nonzero(got[lo:hi] != exp[lo - off:hi - off]))
        if best is None or n_err < best[1]:
            best = (off, n_err, hi - lo)
    return best if best is not None else (0, 999, 0)


def load_adc(cfo_hz=CFO_HZ, snr_db=20.0, seed=1, gap_jitter=16):
    """用 mc_gen 生成一帧激励, 读回 (i_q, q_q) 12bit 有符号。"""
    mc_gen.gen_point(snr_db, 1, 20, 8.0, seed, 400, 600, GEN_DIR,
                     cfo_hz=cfo_hz, gap_jitter=gap_jitter)
    raw = np.fromfile(GEN_DIR / "mem.bin", dtype=">u4")
    i_s = (raw & 0xFFF).astype(int)
    q_s = ((raw >> 12) & 0xFFF).astype(int)
    i_s = np.where(i_s >= 2048, i_s - 4096, i_s)
    q_s = np.where(q_s >= 2048, q_s - 4096, q_s)
    return i_s, q_s


async def _drive(dut, i_s, q_s):
    ok_a = ok_b = False
    n_chip = n_fs_a = n_fs_b = 0
    fs_idx = -1
    syms_a = []
    rot_i_a, rot_q_a = [], []
    for i, q in zip(i_s, q_s):
        dut.adc_i.value = int(i)
        dut.adc_q.value = int(q)
        dut.adc_dv.value = 1
        await RisingEdge(dut.clk)
        ok_a = ok_a or int(dut.fcs_ok_a.value) == 1
        ok_b = ok_b or int(dut.fcs_ok_b.value) == 1
        try:
            n_chip += int(dut.dut.u_fe.chip_dv.value)
            if int(dut.dut.u_be_a.fs_ch.value) and fs_idx < 0:
                fs_idx = n_chip
            n_fs_a += int(dut.dut.u_be_a.fs_ch.value)
            n_fs_b += int(dut.dut.u_be_b.fs_ch.value)
            if int(dut.dut.u_be_a.u_desp.sym_dv.value) == 1:
                syms_a.append(int(dut.dut.u_be_a.u_desp.sym.value))
            if int(dut.rot_a_dv.value) == 1:
                rot_i_a.append(int(dut.rot_a_i.value))
                rot_q_a.append(int(dut.rot_a_q.value))
        except Exception:                                    # noqa: BLE001
            pass
    dut.adc_dv.value = 0
    for _ in range(2000):
        await RisingEdge(dut.clk)
        ok_a = ok_a or int(dut.fcs_ok_a.value) == 1
        ok_b = ok_b or int(dut.fcs_ok_b.value) == 1
    dut._log.info(f"  [检查] 片={n_chip} fsA={n_fs_a}@{fs_idx} fsB={n_fs_b}")

    # ---- 完整性检查（分层证据; 全部失败只提示、不使测试失败）----
    try:
        gt = np.load(GEN_DIR / "frames.npz")
        full = np.asarray(phy.tx_symbols(bytes(gt["psdu"][0])), dtype=int)
        chips_tx = np.asarray(phy.symbols_to_chips(full), dtype=float)
        ref_err = int(np.count_nonzero(_despread(chips_tx) != full))
        ca = (np.array(rot_i_a, dtype=float)
              + 1j * np.array(rot_q_a, dtype=float)) / 128.0
        r_best, lag = -1.0, None
        for lg in range(-64, len(ca) - len(chips_tx) + 1):
            seg = ca[lg:lg + len(chips_tx)]
            if len(seg) != len(chips_tx):
                continue
            r = abs(np.vdot(chips_tx, seg)) / (np.linalg.norm(seg) + 1e-9)
            if r > r_best:
                r_best, lag = r, lg
        # 离线解扩: 扫片级偏移, 找 RTL 片流能解出期望符号的窗口
        bp = None
        for p in range(32):
            b = _best_align(_despread(ca[p:]), full)
            if bp is None or b[1] < bp[1][1]:
                bp = (p, b)
        # RTL 执行段实际输出
        got = np.array(syms_a, dtype=int)
        al = _best_align(got, full)
        dut._log.info(
            f"  [检查] 参考表自检={ref_err} | 片流 r={r_best:.1f}/"
            f"{np.linalg.norm(chips_tx):.1f} lag={lag} | 离线解扩 p={bp[0]} "
            f"错={bp[1][1]}/{bp[1][2]} | RTL 符号 错={al[1]}/{al[2]}")
    except Exception as e:                                   # noqa: BLE001
        dut._log.info(f"  [检查] 跳过: {e}")
    return ok_a, ok_b


async def _config(dut, inc_a=0, inc_b=0, lock_en=0, lock_ph=0):
    dut.phase_inc_chip_a.value = inc_a
    dut.phase_inc_chip_b.value = inc_b
    dut.ext_lock_en.value = lock_en
    dut.ext_lock_phase.value = lock_ph


@cocotb.test()
async def dual_channel_with_distinct_hypotheses(dut):
    """通道 A 用正确旋性解出；通道 B 不消旋失败。"""
    cocotb.start_soon(_clock(dut))
    await _reset(dut)
    dut.ph_thresh.value = 2 * 10 ** 11
    dut.sfd_thresh.value = 3 * 10 ** 13
    await _config(dut, inc_a=INC_CHIP, inc_b=0)

    i_s, q_s = load_adc()
    ok_a, ok_b = await _drive(dut, i_s, q_s)
    dut._log.info(f"通道A(正确旋性) fcs_ok={int(ok_a)}   "
                  f"通道B(不消旋) fcs_ok={int(ok_b)}")
    assert ok_a, "通道 A 用正确消旋参数应当解出帧"
    assert not ok_b, "通道 B 未消旋（CFO=100kHz）不应解出帧"
    assert int(dut.any_fcs_ok.value) == 1


@cocotb.test()
async def shared_timing_direct_lock(dut):
    """定时共享：扫描量相位 → 直锁复用, 两通道都应解出。"""
    cocotb.start_soon(_clock(dut))
    await _reset(dut)
    dut.ph_thresh.value = 2 * 10 ** 11
    dut.sfd_thresh.value = 3 * 10 ** 13

    i_s, q_s = load_adc(gap_jitter=1)        # 相位固定
    dut._log.info(f"INC_CHIP={INC_CHIP}")

    # 第 1 遍: 扫描（ext_lock 关闭）
    await _config(dut, inc_a=INC_CHIP, inc_b=INC_CHIP, lock_en=0)
    ok1, _ = await _drive(dut, i_s, q_s)
    ph = int(dut.phase_out.value)
    dut._log.info(f"第 1 遍(扫描): 锁定相位={ph}, A 解出={int(ok1)}")
    assert ok1, "扫描路径应当解出帧"

    # 第 2 遍: 直锁（ext_lock 打开, 复用相位）
    await _reset(dut)
    dut.ph_thresh.value = 2 * 10 ** 11
    dut.sfd_thresh.value = 3 * 10 ** 13
    await _config(dut, inc_a=INC_CHIP, inc_b=INC_CHIP, lock_en=1, lock_ph=ph)
    ok_a2, ok_b2 = await _drive(dut, i_s, q_s)
    dut._log.info(f"第 2 遍(直锁 phase={ph}): A={int(ok_a2)} B={int(ok_b2)}")
    assert ok_a2 and ok_b2, "直锁路径下两通道都应解出（定时共享）"


async def _clock(dut):
    while True:
        dut.clk.value = 0
        await Timer(31, unit="ns")
        dut.clk.value = 1
        await Timer(31, unit="ns")


async def _reset(dut):
    for sig, v in (("adc_i", 0), ("adc_q", 0), ("adc_dv", 0),
                   ("phase_inc_chip_a", 0), ("phase_off_a", 0),
                   ("phase_inc_chip_b", 0), ("phase_off_b", 0),
                   ("ext_lock_en", 0), ("ext_lock_phase", 0),
                   ("rot_load", 0), ("ph_thresh", 0), ("sfd_thresh", 0)):
        getattr(dut, sig).value = v
    dut.rst_n.value = 0
    for _ in range(10):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)
