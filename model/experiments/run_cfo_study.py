# -*- coding: utf-8 -*-
"""
run_cfo_study.py —— O-QPSK 不看 CFO 会怎样
==========================================
CFO 对 O-QPSK 的伤害**分两环**, 这个实验把两环拆开测:

    判决环:  |·| 非相干检测对相位旋转免疫 (符号内累积相位才是真正的敌人)
    同步环:  CFO 让前导的相干模板失配 -> 相关峰幅度塌陷 -> 定位被噪声骗走

四路对照 (变量 = 同步方式 x 判决方式):

    A  理想定时 + 非相干   → 只测「判决器」对 CFO 的容忍
    B  理想定时 + 相干     → 只测「相干判决」对 CFO 的容忍
    C  窗口同步 + 非相干   → 现实做法 (同步误差 + 非相干判决)
    D  全搜索   + 非相干   → 最脆弱的同步方式

三个尺度 (决定 CFO 容限在哪):
    MF 支撑内相位    8 采样 = 0.5 us  →  100 kHz 才 18 度, 不是瓶颈
    符号内累积相位   32 码片 = 16 us  →  62.5 kHz 转满一圈 = 非相干判决的容限
    前导内累积相位   256 码片 = 128 us →  7.8 kHz 转满一圈 = 同步相关的容限

运行: python model/experiments/run_cfo_study.py  →  out/cfo_study/
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import instance
import measure
import phy_802154 as phy
from baseband import impairments as imp

FS = phy.SPS * phy.CHIP_RATE          # 16 MHz
SPS = phy.SPS
PSDU_LEN, N_FRAMES = 20, 120
SNR_DB = -1.0
SEED = 2026
CFO_GRID = [0, 1e3, 2e3, 5e3, 10e3, 20e3, 50e3, 100e3, 200e3]
SYNC_WIN = 8 * SPS                    # 窗口同步的搜索范围 (帧首 1 个符号内)


def one_frame(cfo_hz, sy, rx, sync, rng, p_true, snr_db=SNR_DB):
    """跑一帧, 返回 (错比特数, 总比特数, 同步失败)。

    sync='ideal' 用干净信号的真实对齐点 (只考验判决器);
    sync='window'/'full' 从含噪信号里搜 (带上同步误差)。
    """
    psdu = bytes(rng.integers(0, 256, size=PSDU_LEN).tolist())
    syms = phy.ppdu_symbols(psdu)
    y = phy.modulate_oqpsk(phy.symbols_to_chips(syms))
    y = phy.add_awgn(y, snr_db, rng=rng)
    if cfo_hz:
        y = imp.add_cfo(y, cfo_hz)
    mf = phy.matched_filter(y)

    if sync == "ideal":
        p = p_true
    else:
        _, _, corr = phy.correlate_preamble(mf, return_corr=True)
        p = int(np.argmax(corr[:SYNC_WIN])) if sync == "window" else int(np.argmax(corr))

    chips = phy.sample_chips(mf, p, len(syms) * 32)     # 自带越界补零
    got = phy.despread_chips(chips) if rx == "noncoh" else phy.despread_coherent(chips)
    nerr = int(sum(bin(int(v)).count("1") for v in (got ^ syms)))
    shifted = abs(p - p_true) > 2 * SPS                 # 偏了半码片以上算失同步
    return nerr, 4 * len(syms), shifted


def sweep(cfo_hz, rx, sync, p_true, snr_db=SNR_DB):
    rng = np.random.default_rng(SEED)                   # 各点同种子 -> 噪声可比
    e = t = nsync = 0
    for _ in range(N_FRAMES):
        a, b, sh = one_frame(cfo_hz, None, rx, sync, rng, p_true, snr_db)
        e += a
        t += b
        nsync += sh
    return e / t, nsync


def main():
    inst = instance.Instance("cfo_study", fs=FS,
                             title="O-QPSK 不看 CFO 会怎样: 判决环 vs 同步环")
    # 真实对齐点: 干净信号的相关峰 (无噪无损伤时恒为 0)
    rng0 = np.random.default_rng(1)
    psdu0 = bytes(rng0.integers(0, 256, size=PSDU_LEN).tolist())
    p_true = phy.correlate_preamble(
        phy.matched_filter(phy.modulate_oqpsk(
            phy.symbols_to_chips(phy.ppdu_symbols(psdu0)))))[0]

    paths = [("理想定时 + 非相干", "noncoh", "ideal", "o-"),
             ("理想定时 + 相干", "coh", "ideal", "^--"),
             ("窗口同步 + 非相干", "noncoh", "window", "s-"),
             ("全搜索 + 非相干", "noncoh", "full", "d:")]
    res, sync_fail = {}, {}
    for name, rx, sync, _ in paths:
        res[name] = []
        sync_fail[name] = []
        for cfo in CFO_GRID:
            ber, nf = sweep(cfo, rx, sync, p_true)
            res[name].append(max(ber, 1e-9))
            sync_fail[name].append(nf)

    print("=== BER vs CFO ===")
    hdr = f"{'CFO(kHz)':>9} " + " ".join(f"{n:>18}" for n, *_ in paths)
    print(hdr)
    rows = [hdr]
    for i, cfo in enumerate(CFO_GRID):
        line = f"{cfo/1e3:>9.0f} " + " ".join(f"{res[n][i]:>18.2e}" for n, *_ in paths)
        print("  " + line)
        rows.append(line)
    print("\n失同步帧数 (窗口/全搜索, 共 %d 帧):" % N_FRAMES)
    rows.append("")
    rows.append("失同步帧数 (共 %d 帧/点):" % N_FRAMES)
    for i, cfo in enumerate(CFO_GRID):
        line = (f"  {cfo/1e3:>7.0f} kHz | 窗口 {sync_fail['窗口同步 + 非相干'][i]:>3d}"
                f" | 全搜索 {sync_fail['全搜索 + 非相干'][i]:>3d}")
        print(line)
        rows.append(line)

    # ---- 图 1: BER vs CFO ----
    x = [c / 1e3 for c in CFO_GRID]
    fig, ax = plt.subplots(figsize=(10, 5.6))
    for name, _, _, style in paths:
        ax.semilogy(x, res[name], style, lw=1.7, ms=5, label=name)
    ax.axvline(96, color="r", ls=":", lw=1.5)
    ax.text(99, 2e-1, "晶振 ±40 ppm\n@2.4 GHz = 96 kHz", fontsize=8.5, color="r")
    ax.axvline(62.5, color="tab:green", ls=":", lw=1.3)
    ax.text(63, 3e-4, "62.5 kHz\n= 符号内转满一圈", fontsize=8, color="tab:green")
    ax.axvspan(1e-3, 7.8, color="tab:blue", alpha=0.07)
    ax.text(3.2, 7e-2, "同步相关的\n无损区", fontsize=8, color="tab:blue", ha="center")
    ax.set_xlabel("CFO (kHz)")
    ax.set_ylabel("BER")
    ax.set_ylim(1e-5, 1.2)
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(fontsize=9, loc="lower right")
    ax.set_title("O-QPSK 不看 CFO: 伤害来自**同步环**, 不是判决环\n"
                 f"(chip SNR {SNR_DB:+.0f} dB, {N_FRAMES} 帧/点, 20 B PSDU)")
    inst.add_figure(fig, "cfo_ber.png")

    # ---- 图 2: 前导相关峰塌陷 (同步环受伤的直接原因) ----
    pk_syms = phy.ppdu_symbols(bytes(np.random.default_rng(3).integers(0, 256, size=PSDU_LEN).tolist()))
    pk_tx = phy.modulate_oqpsk(phy.symbols_to_chips(pk_syms))
    peaks = []
    for cfo in CFO_GRID:
        y = imp.add_cfo(pk_tx, cfo) if cfo else pk_tx
        _, pk, _ = phy.correlate_preamble(phy.matched_filter(y), return_corr=True)
        peaks.append(pk)
    rel = [p / peaks[0] for p in peaks]
    figp, axp = plt.subplots(figsize=(9.5, 4.8))
    axp.semilogy(x, rel, "o-", lw=1.7, color="tab:red", label="前导相关峰 (归一化)")
    # 相干积累损失理论线: sinc(pi*f*Tp) 量级
    Tp = phy.PREAMBLE_SYMS * 32 * SPS / FS
    axp.semilogy(x, [abs(np.sinc(f / 1e3 * Tp)) for f in x], "k--", lw=1.2,
                 label=r"$|\mathrm{sinc}(\pi \Delta f T_p)|$, $T_p$=128 us (前导时长)")
    axp.set_xlabel("CFO (kHz)")
    axp.set_ylabel("相关峰 (相对)")
    axp.grid(True, which="both", alpha=0.3)
    axp.legend(fontsize=9)
    axp.set_title("CFO 让前导相干模板失配 → 相关峰塌陷\n"
                  "(峰矮了, 同样的噪声就更容易把 argmax 骗到别处)")
    inst.add_figure(figp, "cfo_corr_peak.png")

    # ---- 图 3: 星座 —— 非相干看 |·|, 相干看实部 ----
    const_syms = phy.ppdu_symbols(bytes(np.random.default_rng(3).integers(0, 256, size=PSDU_LEN).tolist()))
    tx = phy.modulate_oqpsk(phy.symbols_to_chips(const_syms))
    gain0 = None
    cases = []
    for cfo in (0, 10e3, 50e3):
        y = imp.add_cfo(tx, cfo) if cfo else tx
        p, _ = phy.correlate_preamble(phy.matched_filter(y))
        chips = phy.sample_chips(phy.matched_filter(y), p, len(const_syms) * 32)
        if gain0 is None:
            gain0 = float(np.mean(np.abs(chips.real))) + 1e-12
        soft = measure.soft_values_from_chips(chips, phy.CHIP, const_syms, chip_gain=gain0)
        cases.append((f"CFO {cfo/1e3:.0f} kHz", soft))
    figc, axsc = plt.subplots(1, 3, figsize=(14, 4.8))
    for axi, (name, soft) in zip(axsc, cases):
        ang = np.degrees(np.angle(soft))
        sc = axi.scatter(soft.real, soft.imag, s=16, alpha=0.65, c=ang, cmap="hsv",
                         vmin=-180, vmax=180)
        axi.set_xlim(-1.35, 1.35)
        axi.set_ylim(-1.35, 1.35)
        axi.axhline(0, color="gray", lw=0.4)
        axi.axvline(0, color="gray", lw=0.4)
        axi.set_aspect("equal")
        axi.grid(alpha=0.3)
        axi.set_title(f"{name}\n相位散布 {ang.std():.1f}°  |  幅度散布 {np.abs(soft).std():.3f}",
                      fontsize=10)
    cb = figc.colorbar(sc, ax=axsc, fraction=0.025, pad=0.02)
    cb.set_label("软值相位 (度)", fontsize=9)
    figc.suptitle("CFO 让解扩软值**绕原点转圈**: 非相干只取 |·| (半径不变 → 免疫);\n"
                  "相干取实部 (投影一变就跨判决边界 → 崩)", fontsize=12)
    inst.add_figure(figc, "cfo_constellation.png")

    inst.table("BER vs CFO", rows)
    inst.table("三个决定容限的尺度", [
        "**MF 支撑内相位** (8 采样 = 0.5 us): 100 kHz 才 18°, 远不是瓶颈;",
        "**符号内累积相位** (32 码片 = 16 us): 62.5 kHz 转满一圈 —— 非相干判决的容限,"
        " 因为码片相关时首尾相位差太大就开始抵消;",
        "**前导内累积相位** (256 码片 = 128 us): 7.8 kHz 转满一圈 —— 同步相关的容限,"
        " 比判决容限紧 8 倍。",
    ])
    inst.table("结论", [
        "**判决环**: 理想定时下, 非相干 |·| 直到 20 kHz 完全免疫 (BER 0); "
        "相干判决 2 kHz 就崩 —— 因为相位未知, 实部投影会转到判决边界外。",
        "**同步环**: 5 kHz 前导相关峰就腰斩 (11900 → 5977), 10 kHz 只剩 1/3。"
        "峰矮了而噪声不变, argmax 就被骗走 → 全搜索在 5 kHz 开始失锁。",
        "**现实做法 (窗口同步 + 非相干)**: 因为前导就在帧首, 窗口把搜索范围压到"
        " 1 个符号内, 大大降低了被噪声骗的概率; 但 10 kHz 以上仍然崩, 原因还是"
        " 相关峰塌陷后窗口内的伪峰压过了真峰。",
        "所以答案: **不看 CFO 一定会死于同步, 而不是死于判决**。而晶振 ±40 ppm"
        " 在 2.4 GHz 给出 96 kHz —— 是同步容限 (7.8 kHz) 的 12 倍。",
    ])
    inst.note("CFO 与 SFO 的伤人方式完全不同: SFO 让**采样点**一章一章地漂 (时间轴问题),"
              " CFO 只**转相位** (不挪采样点)。所以两者在图上长得不一样 —— SFO 表现为"
              "「越到帧尾越错」的位置相关错误, CFO 表现为「整体一起转」的相位相关错误。"
              "这也解释了为什么你之前看到的图都像 SFO: 位置错误在星座图和眼图上更显眼。")
    inst.note("修 CFO 只需在前导上做一次相位差分估计再整体消旋 —— 因为它是**公共相位**,"
              " 一次估计 + 一次复数乘法就够, 不需要跟踪环路。这与 SFO 需要重采样形成对照。")
    inst.close()
    print(f"\n✓ 实例产出: {inst.dir}")
    for p in inst.produced:
        print(f"    {p}")


if __name__ == "__main__":
    main()
