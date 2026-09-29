"""RTL 联合实验: 跑一组 CFO, dump cfo_est/cfo_rot 的实际输出供出图。

产物: tb/cfo_corr/rtl_cfo_dump.npz  (由 analyze_rtl.py 读取消费)
  · cfo[N]        真值
  · p_hat[N]      RTL 锁定的相位候选
  · phase_inc[N]  RTL 给出的每采样相位增量
  · i_raw/q_raw   某案例的消旋前 MF 输出
  · i_fix/q_fix   同案例的消旋后 MF 输出
  · pk_raw/pk_fix 各案例的前导相关峰 (修前/修后)
  · ber_raw/ber_fix 各案例的 BER (解扩判决, 码片采样规则与 RTL 一致)
"""
import sys
from pathlib import Path

import cocotb
import numpy as np
from cocotb.triggers import FallingEdge

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "model"))
import phy_802154 as phy
from test_cfo_corr import build_frame, drive, reset, sgn, N_PRE_SMP

SPS = phy.SPS
P24 = 1 << 24
CFO_LIST = [0.0, 5e3, 20e3, 50e3, 96e3, 150e3, 300e3, 450e3]
SHOW = 96e3          # 画星座/波形用的案例


def chips_from(mfi, mfq, align, n_syms):
    """按 RTL 的码片峰值规则采样并归位 (与 phy.sample_chips 等价)。"""
    m = np.arange(n_syms * 32)
    idx = align + m * SPS + np.where(m % 2, SPS // 2, 0) + (SPS - 1)
    idx = np.clip(idx, 0, len(mfi) - 1)
    v = np.asarray(mfi, dtype=float)[idx] + 1j * np.asarray(mfq, dtype=float)[idx]
    return np.where(m % 2 == 0, v, v * (-1j))


def frame_fixed(syms, cfo_hz=0.0, scale=8, chip_snr_db=None, seed=7):
    """用给定符号序列造定点 MF 输出 (与 ber_of 的参照序列保持自洽)。

    注意不能用 test_cfo_corr.build_frame: 它内部用 tx_symbols(含 PHR/白化),
    而解扩参照用 ppdu_symbols —— 两者不是同一序列 (32 vs 28 符号), BER 会假高。
    """
    i_f, q_f = phy.modulate_oqpsk_fixed(np.asarray(syms).tolist())
    n = max(len(i_f), len(q_f))
    i_f = np.pad(np.array(i_f, dtype=float), (0, n - len(i_f))) * scale
    q_f = np.pad(np.array(q_f, dtype=float), (0, n - len(q_f))) * scale
    if chip_snr_db is not None:
        # 802.15.4 惯用口径: 使**匹配滤波输出端码片 SNR** = chip_snr_db。
        # 这里必须用**定点**脉冲的能量: modulate_oqpsk_fixed 的 h 是 Q6 定点
        # (系数 12..63, Σh_d² = 16436), 而不是浮点半正弦的 Σh² = 4 —— 两者差 2^6,
        # 用错会让噪声小 64 倍, 眼图假性干净。
        es_h_fixed = float(np.sum(np.array(phy.fixed_half_sine(), dtype=float) ** 2))
        sigma_d = np.sqrt(es_h_fixed / 10 ** (chip_snr_db / 10.0)) * scale
        rng = np.random.default_rng(seed)
        i_f = np.round(i_f + rng.standard_normal(n) * sigma_d)
        q_f = np.round(q_f + rng.standard_normal(n) * sigma_d)
    if cfo_hz:
        k = np.arange(n)
        ph = 2 * np.pi * cfo_hz * k / (SPS * phy.CHIP_RATE)
        i_f, q_f = i_f * np.cos(ph) - q_f * np.sin(ph), q_f * np.cos(ph) + i_f * np.sin(ph)
    return np.convolve(i_f, np.array(phy.fixed_half_sine())), \
           np.convolve(q_f, np.array(phy.fixed_half_sine()))


def ber_of(mfi, mfq, align, syms):
    got = phy.despread_chips(chips_from(mfi, mfq, align, len(syms)))
    return sum(bin(int(v)).count("1") for v in (got ^ syms)) / (4 * len(syms))


def corr_peak(xi, xq):
    a = np.asarray(xi, dtype=float) + 1j * np.asarray(xq, dtype=float)
    t = np.conj(phy.matched_filter(phy.modulate_oqpsk(np.tile(phy.CHIP[0], 8))))
    return float(np.abs(np.convolve(a, t))[:2048].max())


@cocotb.test()
async def rtl_lab_dump(dut):
    await reset(dut)
    rng = np.random.default_rng(2026)
    psdu = bytes(rng.integers(0, 256, size=8).tolist())
    syms = phy.ppdu_symbols(psdu)

    out = {k: [] for k in ("cfo", "p_hat", "phase_inc", "pk_raw", "pk_fix",
                           "ber_raw", "ber_fix", "est_err")}
    wave = {}

    for cfo in CFO_LIST:
        i_mf, q_mf = frame_fixed(syms, cfo)      # 与解扩参照同一符号序列
        n = len(i_mf)
        # ---- 估计 ----
        dut.est_start.value = 1
        await FallingEdge(dut.clk)
        dut.est_start.value = 0
        await drive(dut, i_mf, q_mf, N_PRE_SMP)
        for _ in range(80):
            await FallingEdge(dut.clk)
            if dut.est_done.value == 1:
                break
        assert dut.est_done.value == 1, f"CFO={cfo} 未出 done"
        p_hat = int(dut.p_hat.value)
        pi = sgn(dut.phase_inc.value, 24)
        f_est = pi / P24 * (SPS * phy.CHIP_RATE)
        # ---- 消旋 ----
        dut.rot_load.value = 1
        await FallingEdge(dut.clk)
        dut.rot_load.value = 0
        oi, oq = await drive(dut, i_mf, q_mf)
        dut.dv_in.value = 0

        align = max(0, p_hat - (SPS - 1))      # RTL 相位候选 -> 模板对齐点
        out["cfo"].append(cfo)
        out["p_hat"].append(p_hat)
        out["phase_inc"].append(pi)
        out["est_err"].append(f_est - cfo)
        out["pk_raw"].append(corr_peak(i_mf, q_mf))
        out["pk_fix"].append(corr_peak(oi, oq))
        out["ber_raw"].append(max(ber_of(i_mf, q_mf, align, syms), 1e-9))
        out["ber_fix"].append(max(ber_of(oi, oq, align, syms), 1e-9))
        if cfo == SHOW:
            wave = {"i_raw": np.array(i_mf[:4096]), "q_raw": np.array(q_mf[:4096]),
                    "i_fix": np.array(oi[:4096]), "q_fix": np.array(oq[:4096]),
                    "syms": np.array(syms), "align": align}
        print(f"  CFO={cfo/1e3:7.1f}k  p_hat={p_hat}  f_est={f_est/1e3:8.2f}k "
              f"err={f_est-cfo:8.1f}Hz  BER {out['ber_raw'][-1]:.3e} -> {out['ber_fix'][-1]:.3e}")

    np.savez(HERE / "rtl_cfo_dump.npz",
             **{k: np.array(v) for k, v in out.items()}, **wave)
    print("  dump 已保存: rtl_cfo_dump.npz")
