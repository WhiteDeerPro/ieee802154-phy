"""眼图观测: dump cfo_rot 消旋前/后的整帧波形, 供 analyze_eye.py 叠眼图。

数据链路: 定点 TX -> CFO 96 kHz -> 定点 MF 卷积 -> [cfo_est] -> [cfo_rot] -> dump
三路对比: 无 CFO 参照 / 有 CFO 未修 / 有 CFO 已修
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
from test_cfo_corr import drive, reset, sgn
from test_rtl_lab import frame_fixed, ber_of, N_PRE_SMP

CFO = 96e3
# MF 后码片 SNR (802.15.4 惯用口径, 非"波形功率"口径)。两档:
#   lo=8 dB  眼开点有效 SNR ~11 dB, 与单载波波形 SNR 16 dB 相当 —— "稀碎"的那档
#   hi=24 dB 眼开点有效 SNR ~27 dB, 噪声不盖过 CFO, 能看清修正的效果
SNR_SET = [("lo", 8.0), ("hi", 24.0)]
PSDU_LEN = 20           # 20 B -> 51 符号 -> 13060 采样, 够 500 条迹线 x 16
N_LEN = 20000           # 上限 (实际被帧长截断)


@cocotb.test()
async def eye_dump(dut):
    rng = np.random.default_rng(2026)
    psdu = bytes(rng.integers(0, 256, size=PSDU_LEN).tolist())
    syms = phy.ppdu_symbols(psdu)

    out = {"cfo": CFO, "period": 2 * phy.SPS}
    for tag, csnr in SNR_SET:
        await reset(dut)
        # 三路都加 AWGN: 无 CFO 参照 / 有 CFO 未修 / 有 CFO 已修
        i_ref, q_ref = frame_fixed(syms, 0.0, chip_snr_db=csnr, seed=11)
        i_cfo, q_cfo = frame_fixed(syms, CFO, chip_snr_db=csnr, seed=11)
        n = min(len(i_cfo), N_LEN, len(i_ref))

        # ---- 估计 ----
        dut.est_start.value = 1
        await FallingEdge(dut.clk)
        dut.est_start.value = 0
        await drive(dut, i_cfo, q_cfo, N_PRE_SMP)
        for _ in range(80):
            await FallingEdge(dut.clk)
            if dut.est_done.value == 1:
                break
        assert dut.est_done.value == 1, f"{tag}: est 未出 done"
        p_hat = int(dut.p_hat.value)
        pi = sgn(dut.phase_inc.value, 24)
        print(f"  [eye] {tag} chip SNR={csnr:+.0f} dB -> p_hat={p_hat} "
              f"f_est={pi/(1<<24)*16e6/1e3:.3f} kHz")

        # ---- 消旋 ----
        dut.rot_load.value = 1
        await FallingEdge(dut.clk)
        dut.rot_load.value = 0
        oi, oq = await drive(dut, i_cfo, q_cfo, n)
        dut.dv_in.value = 0

        out[f"{tag}_chip_snr_db"] = csnr
        out[f"{tag}_align"] = max(0, p_hat - (phy.SPS - 1))
        out[f"{tag}_i_ref"] = np.array(i_ref[:n])
        out[f"{tag}_q_ref"] = np.array(q_ref[:n])
        out[f"{tag}_i_cfo"] = np.array(i_cfo[:n])
        out[f"{tag}_q_cfo"] = np.array(q_cfo[:n])
        out[f"{tag}_i_fix"] = np.array(oi[:n])
        out[f"{tag}_q_fix"] = np.array(oq[:n])
        # 同一段波形上的 BER (RTL 消旋前/后) —— 眼图看不出差别时, 这里能
        align = out[f"{tag}_align"]
        b_raw = ber_of(i_cfo, q_cfo, align, syms)
        b_fix = ber_of(oi, oq, align, syms)
        out[f"{tag}_ber_raw"] = b_raw
        out[f"{tag}_ber_fix"] = b_fix
        print(f"  [eye] {tag} BER: 未修 {b_raw:.3e} -> 已修 {b_fix:.3e}")

    np.savez(HERE / "cfo_eye.npz", **out)
    print(f"  [eye] dump 两档 SNR x {n} 采样 -> cfo_eye.npz")
