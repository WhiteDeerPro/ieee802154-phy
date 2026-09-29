# -*- coding: utf-8 -*-
"""
run_quant.py —— ADC/DAC 量化位宽扫描: BER vs 码片 SNR (4/6/8/12 bit vs 浮点)
==============================================================================
验证规格书 §6 的「ADC 4 bit」假设: 量化噪声功率 Δ²/12 在解调工作点
(码片 SNR ≈ -1 dB 需求, 采样域噪声远大于量化步长) 下是否可忽略。

链路 (由 chains 库组装, 见 model/chains.py):
  载荷 → 组帧(完整链: SHR+白化+FCS) → 扩频 → O-QPSK 成形
       → 量化 → AWGN → 匹配滤波 → 前导同步(窗口) → 解扩 → 判决
  量化排在 AWGN **之前**: 信道段入口 rx = tx, 因此 quantize_stage 作用于发射波形,
  等价于原脚本的「DAC 后回环」注入 (w = imp.quantize(w, bits))。
  本脚本只负责「挑配方 + 扫参 + 喂 measure + 出图」。

运行: python model/experiments/run_quant.py  →  out/ber/ber_quant.png + quant_results.csv
"""
import csv
import sys
from pathlib import Path

# 本脚本位于 model/experiments/ —— 库模块 (chains / measure / phy_802154 / visualize)
# 在上一级的 model/, 而 Python 只自动把脚本自身目录加入 sys.path, 故显式引导。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import chains
import measure
import phy_802154 as phy
from baseband import impairments as imp

OUT = Path(__file__).resolve().parent.parent / "out" / "ber"
OUT.mkdir(parents=True, exist_ok=True)

PSDU_LEN = 20                        # 字节
N_FRAMES = 200                       # 每个扫参点最多帧数
SNR_GRID = np.arange(-3.0, 6.5, 1.5)
BITS = [4, 6, 8, 12, None]
SPS = phy.SPS
SYNC_WIN = 8 * SPS - 1               # 帧起点必在前 8 采样窗内
# (前导 8 符号周期重复 → 相关次峰在 256 采样处, 低 SNR 下 argmax 可能选错)
EARLY_STOP_ERRS = 60                 # 误码数超此值即停 (与迁移前同触发时机)
rng = np.random.default_rng(2026)


def build_chain(bits, snr_db):
    """单个扫参点的链路 (链路对象在扫参点内复用; rng 由闭包持有保证可复现)。"""
    return chains.link([imp.quantize_stage(bits), chains.awgn(snr_db, rng)],
                       full=True, sync="window", win=SYNC_WIN)


def sim_bits(bits, snr_db):
    """200 帧 BER (assisted 同步, 无 CFO/DC/IQ —— 纯量化+AWGN)

    统计口径见 measure.frame_bit_errors: 同步失败帧按整帧计错。
    """
    chain = build_chain(bits, snr_db)
    err = tot = 0
    for _ in range(N_FRAMES):
        psdu = bytes(rng.integers(0, 256, size=PSDU_LEN).tolist())
        sig = chain.run(payload=psdu)
        e, t = measure.frame_bit_errors(sig)
        err += e
        tot += t
        if err > EARLY_STOP_ERRS:
            break
    return err / tot


def theory_ber(snr_db):
    """浮点 16 进制正交 DSSS BER (经验拟合: 与 run_ber 曲线一致)"""
    # 用实测浮点曲线作基准即可; 这里给解析近似: 正交信号符号错误率
    sym_snr = snr_db + 10 * np.log10(32) - 1.0   # 非相干 |·|² 近似
    pe_sym = np.exp(-10 ** (sym_snr / 10) / 4) / 2 * 16 / 15
    return 8 / 15 * pe_sym


def main():
    # ---- 扫参: bits × SNR (顺序 = 原脚本: bits 外层, SNR 内层) ----
    print(f"{'bits':>5} {'SNR':>6} {'BER':>10}")
    rows = []
    for bits in BITS:
        lab = "float" if bits is None else f"{bits}b"
        for snr in SNR_GRID:
            ber = sim_bits(bits, snr)
            rows.append((lab, snr, ber))
            print(f"{lab:>5} {snr:>6.1f} {ber:>10.2e}")

    # 保存 CSV
    with open(OUT / "quant_results.csv", "w", newline="") as f:
        wcsv = csv.writer(f)
        wcsv.writerow(["bits", "chip_snr_db", "ber"])
        wcsv.writerows(rows)

    # 理论量化噪声账 (打印)
    print("\nQuantization noise budget (sample domain, full-scale ±1, avg signal power 0.25/dim):")
    for bits in (3, 4, 6, 8):
        sq = (2.0 / 2 ** bits) ** 2 / 12
        snrq = 10 * np.log10(0.25 / sq)
        print(f"  {bits} bit: sq={sq:.2e}, quantization SNR={snrq:.1f} dB")

    # 图
    fig, ax = plt.subplots(figsize=(10, 6))
    colors = ["tab:red", "tab:orange", "tab:blue", "tab:cyan", "k"]
    for (bits, c) in zip(BITS, colors):
        lab = "float (no quantization)" if bits is None else f"ADC {bits} bit"
        xs = [r[1] for r in rows if r[0] == ("float" if bits is None else f"{bits}b")]
        ys = [r[2] for r in rows if r[0] == ("float" if bits is None else f"{bits}b")]
        ax.semilogy(xs, ys, "o-", ms=5, lw=1.2, color=c, label=lab)
    ax.axhline(1e-3, color="gray", ls=":", lw=1)
    ax.text(5.6, 1.4e-3, "BER=1e-3 requirement line", fontsize=8, ha="right")
    ax.set_xlabel("Chip SNR (dB)")
    ax.set_ylabel("BER")
    ax.set_title("ADC quantization bit-width sweep: 4-bit assumption check (spec §6)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=9)
    out = OUT / "ber_quant.png"
    fig.savefig(out, dpi=130)
    print(f"\nsaved: {out}")


if __name__ == "__main__":
    main()
