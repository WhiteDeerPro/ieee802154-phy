# -*- coding: utf-8 -*-
"""
run_cfo_visual.py —— CFO 成因 / 影响 / 修正: 可视化专题 (ref 黄金模型)
=======================================================================
配套: run_cfo_study.py (BER 分解) / run_cfo_fix.py (修复闭环) / run_cfo_eye.py (眼图)。
本脚本补三块"看图就懂"的内容:

  图 1 cfo_mech.png      成因与数学形式
       (a) 前导码片在复平面的形态: 理想=实轴点 / CFO=旋转圆弧 (累积相位)
       (b) 相位累积 φ(t)=2π·Δf·t, 叠加三个关键尺度标注
  图 2 cfo_spectrum.png  频域: CFO 让频谱整体平移 (16 Msps 下的"小位移")
  图 3 cfo_eye.png       眼图三路: 理想 / CFO 未修 / CFO 经 ref derotate 修正
  图 4 cfo_blockcorr.png 块间自相关的 2ω 振荡 —— RTL 判据 (无共轭积) 的固有弱点:
                           r = Σ(Re(v·v')) ∝ cos(2ωt), 相位以 **2 倍**速率累积,
                           1 kHz 时振荡周期 0.5 ms < 帧长 1 ms → 检测窗口随机成败。
                           (共轭积 Σ(v·conj(v')) 的相位恒为 ω·τ, 无此问题)

运行: python model/experiments/run_cfo_visual.py   →  model/out/cfo_visual/
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import measure
import phy_802154 as phy
import visualize
from baseband import impairments as imp
import run_cfo_fix as RCF

FS = phy.SPS * phy.CHIP_RATE          # 16 MHz
SPS = phy.SPS
OUT = Path(__file__).resolve().parent.parent / "out" / "cfo_visual"
OUT.mkdir(parents=True, exist_ok=True)
visualize.use_cjk_font()

SEED = 7
PSDU_LEN = 20


def gen(cfo_hz, psdu_len=PSDU_LEN, seed=SEED, phi0=0.0):
    """一帧浮点波形 (可选 CFO; phi0 = 随机起始相位, 模拟不同的帧到达时刻)。"""
    rng = np.random.default_rng(seed)
    psdu = bytes(rng.integers(0, 256, size=psdu_len).tolist())
    syms = phy.ppdu_symbols(psdu)
    y = np.asarray(phy.modulate_oqpsk(phy.symbols_to_chips(syms)))
    if phi0:
        y = y * np.exp(1j * phi0)
    if cfo_hz:
        y = imp.add_cfo(y, cfo_hz)
    return y, syms


def preamble_chips(mf, p=0, n=256):
    """按峰值规则采前导码片 (复值, 已去交错)。"""
    return phy.sample_chips(mf, p, n, SPS)


# ===========================================================================
# 图 1: 成因与数学形式
# ===========================================================================
def fig_mechanism():
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12.6, 5.0))

    # (a) 复平面: 前导 256 码片 (CHIP[0] 重复 = ±1 实值)
    for cfo, style, col in ((0, "o", "tab:blue"),
                            (20e3, "o", "tab:orange"),
                            (96e3, "o", "tab:red")):
        y, _ = gen(cfo)
        mf = phy.matched_filter(y)
        ch = preamble_chips(mf)
        a1.plot(ch.real, ch.imag, style, ms=3, alpha=0.55, color=col,
                label=f"CFO = {cfo/1e3:.0f} kHz" if cfo else "CFO = 0 (理想)")
    a1.axhline(0, color="gray", lw=0.6); a1.axvline(0, color="gray", lw=0.6)
    a1.set_aspect("equal")
    a1.set_xlabel("I (码片峰值实部)"); a1.set_ylabel("Q")
    a1.set_title("(a) 前导码片在复平面: 无 CFO 全落在实轴;\n"
                 "CFO 让每个码片按累积相位旋转 → 摊成圆弧")
    a1.legend(fontsize=9, loc="upper right")
    a1.grid(alpha=0.25)

    # (b) 相位累积 + 三个尺度
    t_us = np.linspace(0, 13056 / FS * 1e6, 500)      # 一帧 ≈ 816 µs
    for cfo, col in ((1e3, "tab:green"), (7.8e3, "tab:blue"), (62.5e3, "tab:red")):
        ph = (2 * np.pi * cfo * t_us * 1e-6) % (2 * np.pi)
        a2.plot(t_us, np.degrees(ph), color=col, lw=1.6, label=f"{cfo/1e3:g} kHz")
    for t, lab in ((0.5, "MF 支撑 0.5 µs"), (16, "符号 16 µs"), (128, "前导 128 µs")):
        a2.axvline(t, color="gray", ls=":", lw=1.2)
        a2.text(t, 1080, lab, rotation=90, fontsize=8, color="gray", va="top")
    a2.set_xlabel("时间 (µs)"); a2.set_ylabel("累积相位 (度, 折叠到 ±360)")
    a2.set_title("(b) 相位累积: 三个尺度决定 CFO 容限\n"
                 "MF 支撑内几乎不转; 符号内 62.5 kHz 转满圈; 前导内 7.8 kHz 转满圈")
    a2.set_ylim(0, 360)
    a2.legend(fontsize=9, loc="upper left")
    a2.grid(alpha=0.25)

    fig.suptitle("CFO 的成因与数学形式: out(t) = s(t)·exp(j·2π·Δf·t)\n"
                 "物理来源: 收发晶振容差 (标准 ±40 ppm @2.45 GHz ≈ ±98 kHz) + 多普勒",
                 fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.90))
    fig.savefig(OUT / "cfo_mech.png", dpi=130)
    print("  ✓ cfo_mech.png")


# ===========================================================================
# 图 2: 频域 (频谱搬移)
# ===========================================================================
def fig_spectrum():
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12.6, 4.6))
    for cfo, col in ((0, "tab:blue"), (96e3, "tab:red"), (300e3, "tab:purple")):
        y, _ = gen(cfo)
        f, mag = measure.spectrum(y, fs=FS, n_fft=4096)
        f_khz = f / 1e3
        lab = f"Δf = {cfo/1e3:.0f} kHz" if cfo else "Δf = 0"
        a1.plot(f_khz, mag, lw=1.3, color=col, label=lab)
        a2.plot(f_khz, mag, lw=1.5, color=col, label=lab)
    a1.set_xlabel("频率 (kHz)"); a1.set_ylabel("|X|")
    a1.set_title("(a) 全带宽视角: 16 Msps 采样,\n半正弦成形旁瓣宽 → 频移 96 kHz 几乎看不出")
    a1.legend(fontsize=9); a1.grid(alpha=0.25)
    a1.set_xlim(-8000, 8000)

    a2.set_xlim(-1500, 1500)
    a2.set_xlabel("频率 (kHz)"); a2.set_ylabel("|X|")
    a2.set_title("(b) 放大主瓣: CFO = 频谱整体平移\n(96 kHz = 标准最坏容差的一半)")
    a2.legend(fontsize=9); a2.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(OUT / "cfo_spectrum.png", dpi=130)
    print("  ✓ cfo_spectrum.png")


# ===========================================================================
# 图 3: 眼图三路
# ===========================================================================
def fold_eye(y_mf, period=2 * SPS, n_traces=400, start=2048):
    """把 MF 输出的 I 路按 period 折叠 (与 run_cfo_eye 同法)。"""
    x = np.asarray(y_mf.real, dtype=float)
    seg = x[start:start + n_traces * period]
    return seg.reshape(n_traces, period)


def fig_eye():
    cfo = 96e3
    y0, _ = gen(0)
    y1, _ = gen(cfo)
    mf0 = phy.matched_filter(y0)
    mf1 = phy.matched_filter(y1)
    p_hat, f_hat, _ = RCF.estimate_cfo(mf1)
    mf1_fix = RCF.derotate(mf1, f_hat)

    fig, axes = plt.subplots(1, 3, figsize=(13.2, 4.4), sharey=True)
    for ax, (mf_use, title, col) in zip(axes, (
            (mf0, "理想 (无 CFO)", "tab:blue"),
            (mf1, f"CFO = 96 kHz 未修", "tab:red"),
            (mf1_fix, f"消旋修正后 (估计误差 {abs(f_hat-96e3):.0f} Hz)", "tab:green"))):
        eye = fold_eye(mf_use)
        ax.plot(eye.T, color=col, lw=0.4, alpha=0.25)
        ax.set_title(title, fontsize=10)
        ax.set_xlabel("采样 (折叠周期 16)")
        ax.grid(alpha=0.25)
    axes[0].set_ylabel("MF 输出 I 路")
    fig.suptitle("眼图: CFO 在波形上几乎看不出——这正是非相干 |·| 检测对相位免疫的体现;\n"
                 f"真正的伤害在同步环的相干相关 (见 cfo_blockcorr.png)。估计误差 {abs(f_hat - 96e3):.0f} Hz",
                 fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.88))
    fig.savefig(OUT / "cfo_eye.png", dpi=130)
    print(f"  ✓ cfo_eye.png  (f̂={f_hat:.1f} Hz, 误差 {abs(f_hat-96e3):.0f} Hz)")


# ===========================================================================
# 图 4: 块间自相关的 2ω 振荡 (本次调查的核心发现)
# ===========================================================================
def block_corr_series(mf, n_chips=None):
    """按 RTL 规则算块间自相关序列: 每 32 片一块, 延迟 32 片。
    返回 (块时刻(采样), 无共轭积 r, 共轭积 r_conj)。"""
    n_chips = n_chips or (len(mf) // SPS - 64)
    ch = phy.sample_chips(mf, 0, n_chips, SPS)
    nb = n_chips // 32
    V = ch[:nb * 32].reshape(nb, 32)
    r_nc = np.real(np.sum(V[1:] * V[:-1], axis=1))            # Σ(v·v')     (RTL)
    r_cj = np.real(np.sum(V[1:] * np.conj(V[:-1]), axis=1))   # Σ(v·conj(v')) (对照)
    t = (np.arange(1, nb) * 32) * SPS
    return t, r_nc, r_cj


def fig_blockcorr():
    # 基难定标: CFO=0 时前导段的块间相关满值 (浮点 ref 量级 ~450)
    y0, _ = gen(0)
    _, r0, _ = block_corr_series(phy.matched_filter(y0))
    R0 = float(r0[:7].mean())

    fig, axes = plt.subplots(1, 2, figsize=(13.0, 4.8))
    # (a) 跨帧随机性: CFO=0 时帧从符号边界开始 (φ₀=0, 恒定满值);
    #     CFO≠0 时帧到达时刻随机 → φ₀ = CFO×n_load 随机 → r = |v|²cos(2φ₀+2ωt) 随机
    a1 = axes[0]
    rng = np.random.default_rng(11)
    n_frame = 40
    for cfo, col, use_phi in ((0, "tab:blue", False), (1e3, "tab:red", True)):
        peak = []
        for k in range(n_frame):
            phi0 = rng.uniform(0, 2 * np.pi) if use_phi else 0.0
            y, _ = gen(cfo, seed=100 + k, phi0=phi0)
            t, r_nc, _ = block_corr_series(phy.matched_filter(y))
            peak.append(r_nc[:7].max() / R0)     # 前导段 8 块, 相对满值
        n_neg = sum(1 for v in peak if v < 0)
        a1.plot(range(n_frame), peak, "o-", ms=4, lw=0.8, color=col,
                label=f"Δf={cfo/1e3:g} kHz  (负值 {n_neg}/{n_frame})")
    a1.axhline(0, color="k", lw=0.9)
    a1.axhline(0.45, color="gray", ls=":", lw=1.2)
    a1.text(0.4, 0.48, "RTL 锁定门限 (以满值折算的示意)", fontsize=8, color="gray")
    a1.set_xlabel("帧号 (CFO≠0 时到达时刻随机)")
    a1.set_ylabel("前导段块间相关峰值 / 无 CFO 满值")
    a1.set_title("(a) 跨帧看: CFO 让帧起点相位 φ₀ = Δf·n_load 变成随机\n"
                 "r = |v|²cos(2φ₀+2ωt) → 一半的帧锁不上 (CFO=0 恒定满值)")
    a1.legend(fontsize=9, loc="lower right"); a1.grid(alpha=0.25)

    # (b) 帧内时间序列: 2ω 振荡形态
    a2 = axes[1]
    for cfo, col in ((1e3, "tab:green"), (2e3, "tab:orange"), (4e3, "tab:red")):
        y, _ = gen(cfo)
        t, r_nc, _ = block_corr_series(phy.matched_filter(y))
        a2.plot(t / FS * 1e6, r_nc / R0, lw=1.4, color=col,
                label=f"无共轭积: Δf={cfo/1e3:g} kHz")
    y2, _ = gen(2e3)
    t2, _, r_cj2 = block_corr_series(phy.matched_filter(y2))
    _, r_cj_ref, _ = block_corr_series(phy.matched_filter(y0))
    a2.plot(t2 / FS * 1e6, r_cj2[:len(t2)] / r_cj_ref[:7].mean(), "--", lw=1.2,
            color="tab:blue", label="共轭积 (对照): Δf=2 kHz")
    a2.axhline(0, color="k", lw=0.8)
    a2.axvspan(0, 128, color="gray", alpha=0.18)
    a2.set_xlabel("时间 (µs)"); a2.set_ylabel("块间自相关 r / 无 CFO 满值")
    a2.set_title("(b) 帧内看: 无共轭积 ∝ cos(2ωt), 相位以 2ω 累积\n"
                 "(1 kHz 时周期 500 µs, 短于一帧; 共轭积无此振荡)")
    a2.legend(fontsize=8.5, loc="lower right"); a2.grid(alpha=0.25)
    a2.text(300, 0.15, "数据段 (随机符号)", fontsize=8, color="dimgray")
    a2.text(64, 0.82, "前导 128 µs", fontsize=8, ha="center", color="dimgray")

    fig.suptitle("为什么低 CFO 就会崩: 块间自相关的 2ω 相位累积 (ref 复现)\n"
                 "这也正是 cfo_corr 必须**前置到同步之前**的原因", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.86))
    fig.savefig(OUT / "cfo_blockcorr.png", dpi=130)
    print(f"  ✓ cfo_blockcorr.png  (满值基准 R0={R0:.0f})")


if __name__ == "__main__":
    print("[run_cfo_visual] 生成 CFO 可视化专题 →", OUT)
    fig_mechanism()
    fig_spectrum()
    fig_eye()
    fig_blockcorr()
    print("done.")
