# -*- coding: utf-8 -*-
"""
run_visuals.py —— 机制型可视化包
==================================
5 张图，每张回答一个"为什么/长什么样"的问题:
  1. vis_constellation.png  损伤在星座图上是什么样? 校正后回到什么状态?
  2. vis_sync_scan.png      前导模板相关器内部长什么样? CFO 为什么免疫?
  3. vis_chip_corr.png      16 个 PN 序列为什么能区分开?
  4. vis_iq_irr.png         给定 IQ 不平衡指标 → 能压到多少镜像?
  5. vis_ber_scenarios.png  各损伤相对基线的 BER 代价是多少 dB?
"""

import sys
from pathlib import Path

# 本脚本位于 model/experiments/ —— 库模块 (chains / measure / phy_802154 / visualize)
# 在上一级的 model/, 而 Python 只自动把脚本自身目录加入 sys.path, 故显式引导。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib   # 下面 matplotlib.colors.LogNorm 仍直接用本模块
import numpy as np

import _common
import phy_802154 as phy
from baseband import impairments as imp
import chains
import measure
import visualize


# Windows GBK 控制台无法编码 ✓/× 等字符，统一以 UTF-8 输出，避免 UnicodeEncodeError
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 中文字形统一交给 visualize (幂等, 找不到中文字体时静默回退), 不再逐图配置
visualize.use_cjk_font()

plt = _common.init()                       # Agg 后端 + 中文字体
OUT = _common.out_dir("vis")               # model/out/vis/（已创建）


# ---------- 固定一帧, 供所有静态可视化复用 ----------
_rng = np.random.default_rng(2026)
PSDU = bytes(_rng.integers(0, 256, size=20).tolist())
SYMS = phy.ppdu_symbols(PSDU)
TX = phy.modulate_oqpsk(phy.symbols_to_chips(SYMS))
N_SYM = len(SYMS)


def symbol_soft_values(y, max_syms=None):
    """提取每符号的复相关值 (星座点); 委托 measure.soft_values_from_chips"""
    mf = phy.matched_filter(y)
    p, _ = phy.correlate_preamble(mf)
    n_use = max_syms if max_syms is not None else N_SYM
    chips = phy.sample_chips(mf, p, n_use * 32)
    return measure.soft_values_from_chips(chips, phy.CHIP, SYMS[:n_use])


# ==========================================================
# 图 1: 星座图矩阵 —— 理想 / 各损伤 / 全校正后
# ==========================================================
def fig_constellations():
    snr = 18.0
    awgn = lambda x: phy.add_awgn(x, snr, rng=np.random.default_rng(7))

    y_all = imp.add_dc_offset(
        imp.add_iq_imbalance(imp.add_cfo(awgn(TX), 50e3), 2.0, 10.0),
        0.35, 0.30)

    cases = [
        ("Ideal AWGN",        awgn(TX)),
        ("CFO 50 kHz",       imp.add_cfo(awgn(TX), 50e3)),
        ("IQ 2 dB / 10°",    imp.add_iq_imbalance(awgn(TX), 2.0, 10.0)),
        ("DC (0.35, 0.30)",  imp.add_dc_offset(awgn(TX), 0.35, 0.30)),
        ("CFO+DC+IQ uncorrected", y_all),
    ]

    fig, axs = plt.subplots(1, 6, figsize=(23, 4.3))
    n_show = 80
    for ax, (name, y) in zip(axs[:5], cases):
        c = symbol_soft_values(y)[:n_show]
        ax.scatter(c.real, c.imag, s=16, alpha=0.75, color="tab:blue")
        ax.set_title(name, fontsize=11)
        ax.axhline(0, color="gray", lw=0.4); ax.axvline(0, color="gray", lw=0.4)
        ax.set_aspect("equal"); ax.grid(alpha=0.3)
        ax.set_xlim(-2.5, 2.5); ax.set_ylim(-2.5, 2.5)

    # 第 6 格: 全校正后
    y = y_all
    mf = phy.matched_filter(y)
    f_c = phy.estimate_cfo_coarse(mf)
    f_f, _ = phy.estimate_cfo_fine(y, f_c)
    y = phy.correct_cfo(y, f_f)
    mf = phy.matched_filter(y)
    p, _ = phy.correlate_preamble(mf)
    r_pre = phy.extract_preamble_chips(mf, p)
    s_pre = phy.known_preamble_chips(derail=False)
    alpha, beta, d = phy.estimate_dc_iq(r_pre, s_pre)
    n_chips = N_SYM * 32
    m = np.arange(n_chips)
    idx = p + m * phy.SPS + np.where(m % 2, phy.SPS // 2, 0) + (phy.SPS - 1)
    v = mf[idx].astype(complex)
    w = phy.apply_dc_iq_correction(v, alpha, beta, d)
    chips = np.where(m % 2 == 0, w, w * (-1j))
    mat = chips.reshape(-1, 32)
    c = np.sum(mat * phy.CHIP[np.asarray(SYMS, dtype=int)], axis=1)[:n_show]
    axs[5].scatter(c.real, c.imag, s=16, alpha=0.75, color="tab:green")
    axs[5].set_title("Fully corrected", fontsize=11)
    axs[5].axhline(0, color="gray", lw=0.4); axs[5].axvline(0, color="gray", lw=0.4)
    axs[5].set_aspect("equal"); axs[5].grid(alpha=0.3)
    axs[5].set_xlim(-2.5, 2.5); axs[5].set_ylim(-2.5, 2.5)

    fig.suptitle(f"Constellation: impairment shape and correction effect (chip SNR = {snr:.0f} dB, first {n_show} symbols)",
                 fontsize=13)
    fig.tight_layout()
    fig.savefig(OUT / "vis_constellation.png", dpi=130)
    plt.close(fig)
    print("✓ vis_constellation.png")


# ==========================================================
# 图 2: 前导模板相关器扫描输出 —— CFO 免疫的直观解释
# ==========================================================
def fig_sync_scan():
    snr = -2.0
    y = phy.add_awgn(TX, snr, rng=np.random.default_rng(11))

    fig, axs = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
    n_show = 4 * 32 * phy.SPS          # 只看帧起点附近

    for ax, (label, yin) in zip(axs, [
        ("No CFO", y),
        ("CFO = 100 kHz", imp.add_cfo(y, 100e3)),
    ]):
        mf = phy.matched_filter(yin)
        _, _, corr = phy.correlate_preamble(mf, return_corr=True)
        ax.plot(corr[:n_show], lw=0.9)
        ax.axvline(0, color="r", ls="--", lw=1, label="True frame start p = 0")
        ax.axvline(32 * phy.SPS, color="gray", ls=":", lw=0.8)
        ax.text(32 * phy.SPS + 5, ax.get_ylim()[1] * 0.9,
                "Symbol boundary", fontsize=8, color="gray")
        ax.set_ylabel("|correlation|")
        ax.set_title(f"Preamble template correlation output — {label}", fontsize=11)
        ax.legend(loc="upper right"); ax.grid(alpha=0.3)

    axs[-1].set_xlabel("Sample index")
    fig.suptitle(f"Correlator internal behavior (chip SNR = {snr} dB): decision uses only amplitude → naturally immune to CFO",
                 fontsize=12)
    fig.tight_layout()
    fig.savefig(OUT / "vis_sync_scan.png", dpi=130)
    plt.close(fig)
    print("✓ vis_sync_scan.png")


# ==========================================================
# 图 3: 16×16 PN 码片互相关矩阵
# ==========================================================
def fig_chip_corr():
    G = phy.CHIP @ phy.CHIP.T     # 对角=32, 非对角∈[-12,12]

    fig, axs = plt.subplots(1, 2, figsize=(13, 5.6))
    im0 = axs[0].imshow(G, cmap="RdBu_r", vmin=-32, vmax=32)
    axs[0].set_title("Chip cross-correlation matrix $G_{ij}=\\langle PN_i, PN_j\\rangle$")
    axs[0].set_xlabel("j"); axs[0].set_ylabel("i")
    plt.colorbar(im0, ax=axs[0], fraction=0.046)
    for i in range(16):
        for j in range(16):
            axs[0].text(j, i, f"{int(G[i, j])}", ha="center", va="center",
                        fontsize=6,
                        color="white" if abs(G[i, j]) > 20 else "black")

    off = G[~np.eye(16, dtype=bool)]
    axs[1].hist(off, bins=np.arange(-12.5, 13.5, 1), edgecolor="black", alpha=0.75)
    axs[1].axvline(0, color="r", ls="--", lw=1)
    axs[1].set_title(f"Off-diagonal distribution: max|cross-correlation| = {np.abs(off).max()} "
                     f"(relative to diagonal 32 → spreading gain {phy.SPREADING_GAIN_DB:.2f} dB)")
    axs[1].set_xlabel("Cross-correlation value"); axs[1].set_ylabel("Count")
    axs[1].grid(alpha=0.3)

    fig.tight_layout()
    fig.savefig(OUT / "vis_chip_corr.png", dpi=130)
    plt.close(fig)
    print("✓ vis_chip_corr.png")


# ==========================================================
# 图 4: I/Q 不平衡 → 镜像抑制比 (IRR) 等高线
# ==========================================================
def fig_iq_irr():
    gain = np.linspace(-3, 3, 61)      # dB
    phase = np.linspace(-20, 20, 61)   # deg
    G, P = np.meshgrid(gain, phase)
    g = 10 ** (G / 20)
    p = np.radians(P)

    # 由 impairments.add_iq_imbalance 的模型反推:
    #   x' = g·Re(s) + j(sinφ·Re(s) + cosφ·Im(s))
    # 写成 α·s + β·s*:
    #   α = (g + cosφ)/2 + j·sinφ/2
    #   β = (g - cosφ)/2 + j·sinφ/2
    alpha = (g + np.cos(p)) / 2 + 1j * np.sin(p) / 2
    beta = (g - np.cos(p)) / 2 + 1j * np.sin(p) / 2
    irr = 20 * np.log10(np.abs(alpha) / np.abs(beta) + 1e-12)

    fig, ax = plt.subplots(figsize=(8.5, 6))
    cs = ax.contourf(G, P, irr, levels=25, cmap="viridis")
    plt.colorbar(cs, ax=ax, label="IRR (dB)")
    ax.contour(G, P, irr, levels=[20, 30, 40, 50, 60],
               colors="white", linewidths=0.7)
    ax.set_xlabel("Gain imbalance (dB)"); ax.set_ylabel("Phase imbalance (°)")
    ax.set_title("I/Q imbalance → image rejection ratio (IRR) contour")
    ax.plot([2.0], [10.0], "r*", markersize=15,
            label="this sim (2 dB, 10°) → IRR ≈ 27 dB")
    ax.legend(loc="upper right")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "vis_iq_irr.png", dpi=130)
    plt.close(fig)
    print("✓ vis_iq_irr.png")


# ==========================================================
# 图 5: 多场景端到端 BER 对比
# ==========================================================
def fig_ber_scenarios():
    """多场景 BER 对比: 信号处理/统计样板交给 chains + measure (只留配置与出图)"""
    from run_ber import qpsk_ber     # 复用理论曲线
    snr_list = np.arange(-4, 8, 1.0)
    N_FRAMES = 120
    rng = np.random.default_rng(999)

    # 场景 → (损伤阶段, 接收段选项)。损伤列表顺序 = 损伤作用顺序, 与迁移前
    # 逐行对应: AWGN 恒在最前 (rng 抽取顺序不变), 其后 CFO 或 IQ→DC;
    # 校正场景只是在接收段多开一个开关 (cfo='two_stage' / dc_iq=True),
    # 由 chains 配方按同样的顺序估计/消旋/校正。
    SCENES = {
        "ideal":       ((), {}),
        "cfo_uncorr":  ((imp.cfo_stage(50e3),), {}),
        "cfo_corr":    ((imp.cfo_stage(50e3),), {"cfo": "two_stage"}),
        "dciq_uncorr": ((imp.iq_stage(2.0, 10.0), imp.dc_stage(0.35, 0.30)), {}),
        "dciq_corr":   ((imp.iq_stage(2.0, 10.0), imp.dc_stage(0.35, 0.30)),
                        {"dc_iq": True}),
    }

    def run_one(chip_snr_db, mode):
        """单点仿真: 简化链 + AWGN + 场景损伤, 前导同步用诚实全搜索。

        链路对象在扫参点内复用 (阶段无状态; rng 由闭包持有以保证可复现)。
        比特误码统计用 measure.frame_bit_errors —— 与迁移前一致: 同步失败帧
        按整帧 (4·n_sym) 计错, 且原实现无早停。
        """
        dmg, rx_kw = SCENES[mode]
        chain = chains.link([chains.awgn(chip_snr_db, rng), *dmg],
                            full=False, sync="honest", **rx_kw)
        be = bt = 0
        for _ in range(N_FRAMES):
            psdu = bytes(rng.integers(0, 256, size=20).tolist())
            e, t = measure.frame_bit_errors(chain.run(payload=psdu))
            be += e
            bt += t
        return be / bt if bt else 1.0

    fig, ax = plt.subplots(figsize=(9.5, 6.5))
    for mode, label, color, ls in [
        ("ideal",       "Ideal AWGN (baseline)", "black",      "-"),
        ("cfo_uncorr",  "CFO 50k uncorrected",    "tab:red",    "-"),
        ("cfo_corr",    "CFO 50k two-stage corrected", "tab:green", "-"),
        ("dciq_uncorr", "DC+IQ uncorrected",      "tab:orange", "-"),
        ("dciq_corr",   "DC+IQ LS corrected",     "tab:blue",   "-"),
    ]:
        bers = [max(run_one(s, mode), 1e-6) for s in snr_list]
        ax.semilogy(snr_list, bers, marker="o", label=label, color=color, lw=1.4)

    xc = np.linspace(-4, 7, 100)
    ax.semilogy(xc, [qpsk_ber(8 * 10 ** (x / 10)) for x in xc],
                "k--", lw=1, alpha=0.4, label="coherent QPSK theory + 9.03 dB")

    ax.set_xlabel("Chip SNR (dB)"); ax.set_ylabel("BER")
    ax.set_title("Multi-scenario BER comparison (20 B PSDU, fixed 400 frames/point)")
    ax.grid(True, which="both", alpha=0.3)
    ax.legend(loc="lower left", fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT / "vis_ber_scenarios.png", dpi=130)
    plt.close(fig)
    print("✓ vis_ber_scenarios.png")

# ==========================================================
# 图 6: 时域波形 —— 码片 → I/Q → 复包络
# ==========================================================
def fig_waveform():
    n_sym_show = 4                                    # 看前 4 个符号
    chips_show = phy.symbols_to_chips(SYMS[:n_sym_show])   # (128,)
    tx_short = phy.modulate_oqpsk(chips_show)              # (1028,)
    t_chip = np.arange(len(tx_short)) / phy.SPS

    fig, axs = plt.subplots(4, 1, figsize=(12, 10), sharex=False)

    # ① 码片序列
    ct = np.arange(len(chips_show))
    axs[0].step(ct, chips_show, where="post", lw=1.3, color="tab:blue")
    axs[0].set_ylabel("Chip (±1)")
    axs[0].set_title(f"① PN chip sequence (first {n_sym_show} symbols = {len(chips_show)} chips)")
    axs[0].set_xlim(-1, len(chips_show)); axs[0].grid(alpha=0.3)

    # ② I/Q 成形波形
    axs[1].plot(t_chip, tx_short.real, lw=1.2, label="I (even chips)")
    axs[1].plot(t_chip, tx_short.imag, lw=1.2, label="Q (odd chips, delayed half chip)")
    axs[1].set_ylabel("Amplitude"); axs[1].legend(fontsize=9); axs[1].grid(alpha=0.3)
    axs[1].set_title("② O-QPSK half-sine pulse shaping (I/Q offset by half chip)")

    # ③ 复包络
    axs[2].plot(t_chip, np.abs(tx_short), lw=1.2, color="tab:purple")
    axs[2].set_ylabel("|s(t)|"); axs[2].grid(alpha=0.3)
    axs[2].set_title("③ complex baseband envelope (approaches zero at chip boundaries)")

    # ④ 相位轨迹
    axs[3].plot(t_chip, np.unwrap(np.angle(tx_short)), lw=1.0, color="tab:green")
    axs[3].set_ylabel("arg s(t) (rad)"); axs[3].set_xlabel("Chip period $T_c$")
    axs[3].grid(alpha=0.3); axs[3].set_title("④ phase trajectory (continuous phase of O-QPSK)")

    fig.suptitle("Time-domain waveform: from PN chips to O-QPSK modulation", fontsize=13)
    fig.tight_layout(); fig.savefig(OUT / "vis_waveform.png", dpi=130); plt.close(fig)
    print("✓ vis_waveform.png")


# ==========================================================
# 图 7: 频谱 —— 基带谱 / 扩频前后对比
# ==========================================================
def fig_spectrum():
    fs = phy.SPS * phy.CHIP_RATE                # 16 MHz
    N = len(TX)

    # 频谱
    f = np.fft.fftshift(np.fft.fftfreq(N, 1 / fs))
    X = np.fft.fftshift(np.fft.fft(TX * np.hanning(N)))
    P_tx = 20 * np.log10(np.abs(X) / np.max(np.abs(X)) + 1e-12)

    # 无扩频参考: 每符号一个脉冲 (不展开 32 码片)
    ref = np.zeros(N, dtype=complex)
    for k, s in enumerate(SYMS):
        ref[k * 32 * phy.SPS] = (1.0 if s < 8 else -1.0)
    Xr = np.fft.fftshift(np.fft.fft(ref * np.hanning(N)))
    P_ref = 20 * np.log10(np.abs(Xr) / np.max(np.abs(Xr)) + 1e-12)

    fig, axs = plt.subplots(2, 1, figsize=(12, 7))

    axs[0].plot(f / 1e6, P_tx, lw=0.8)
    axs[0].axvspan(-1, 1, color="g", alpha=0.12)
    axs[0].text(-0.9, -10, "half-sine pulse shaping main lobe ≈ ±1 MHz", fontsize=9, color="g")
    axs[0].set_xlim(-8, 8); axs[0].set_ylim(-80, 3)
    axs[0].set_xlabel("Frequency (MHz)"); axs[0].set_ylabel("dB")
    axs[0].set_title("Complex baseband spectrum — O-QPSK/DSSS half-sine pulse shaping"); axs[0].grid(alpha=0.3)

    axs[1].plot(f / 1e6, P_tx, lw=0.8, label="DSSS after spreading (32 chips/symbol)")
    axs[1].plot(f / 1e6, P_ref, lw=0.8, alpha=0.6, label="unspread reference (62.5 ksym/s)")
    axs[1].set_xlim(-4, 4); axs[1].set_ylim(-80, 3)
    axs[1].set_xlabel("Frequency (MHz)"); axs[1].set_ylabel("dB")
    axs[1].set_title("Spreading comparison: PN sequence widens spectrum to chip rate 2 MHz"); axs[1].grid(alpha=0.3)
    axs[1].legend()

    fig.tight_layout(); fig.savefig(OUT / "vis_spectrum.png", dpi=130); plt.close(fig)
    print("✓ vis_spectrum.png")


# ==========================================================
# 图 8: 接收链逐级 —— 原始 → 匹配滤波 → 解扩
# ==========================================================
def fig_rx_chain():
    snr = 10
    y = phy.add_awgn(TX, snr, rng=np.random.default_rng(7))
    mf = phy.matched_filter(y)

    fig, axs = plt.subplots(3, 1, figsize=(12, 9))

    # ① 接收复基带 (I 路)
    t = np.arange(len(y)) / phy.SPS
    axs[0].plot(t, y.real, lw=0.5, alpha=0.75, color="tab:blue")
    axs[0].set_ylabel("I"); axs[0].set_xlim(0, 64)
    axs[0].set_title(f"① received complex baseband I path (chip SNR={snr} dB, first 64 chips)")
    axs[0].grid(alpha=0.3)

    # ② 匹配滤波后 (脉冲压缩)
    t2 = np.arange(len(mf)) / phy.SPS
    axs[1].plot(t2, mf.real, lw=0.5, alpha=0.75, color="tab:orange")
    axs[1].set_ylabel("I (MF output)"); axs[1].set_xlim(0, 64)
    axs[1].set_title("② after matched filter — chip peaks alternate between even/odd positions")
    axs[1].grid(alpha=0.3)

    # ③ 解扩后符号软值
    p, _ = phy.correlate_preamble(mf)
    soft = symbol_soft_values(y, max_syms=min(N_SYM, 30))   # 复用归一化函数
    axs[2].stem(np.arange(len(soft)), soft.real, basefmt=" ",
                linefmt="C2-", markerfmt="C2o")
    axs[2].axhline(0, color="gray", lw=0.5)
    axs[2].set_ylabel("symbol soft value (normalized)")
    axs[2].set_xlabel("symbol index (low nibble first)")
    axs[2].set_title("③ despread symbol soft value — amplitude ≈ +1 means correct detection")
    axs[2].grid(alpha=0.3)

    fig.suptitle("Receive chain waveforms (from time domain to symbol domain)", fontsize=13)
    fig.tight_layout(); fig.savefig(OUT / "vis_rx_chain.png", dpi=130); plt.close(fig)
    print("✓ vis_rx_chain.png")


# ==========================================================
# 图 9: 眼图 —— 匹配滤波输出按码片周期折叠
# ==========================================================
def fig_eye():
    snr = 10
    mf_ideal = phy.matched_filter(TX)
    mf = phy.matched_filter(phy.add_awgn(TX, snr, rng=np.random.default_rng(7)))

    chip_len = phy.SPS                  # 8 采样/码片
    sym_len = 2 * chip_len              # 每路符号周期 = 2 码片 = 16 采样 (I/Q 各 1 Mchip/s)
    n_traces = 48
    # 符号边界 = 峰值往前 (chip_len-1) 采样, 使最佳采样点落在窗口中间 (index 7)
    i_bnd = phy.chip_peak_index(0, 0) - (chip_len - 1)   # 偶数码片(I) 边界 = 0
    q_bnd = phy.chip_peak_index(0, 1) - (chip_len - 1)   # 奇数码片(Q) 边界 = 12

    fig, axs = plt.subplots(2, 2, figsize=(13, 9), sharex=True, sharey=True)

    for col, (label, sig) in enumerate([("Transmit (ideal, no impairment)", mf_ideal),
                                         (f"Receive (AWGN only {snr} dB)", mf)]):
        for row, (qname, qsig, qbnd) in enumerate([
                ("I eye (even chips)", sig.real, i_bnd),
                ("Q eye (odd chips)", sig.imag, q_bnd)]):
            ax = axs[row, col]
            for k in range(n_traces):
                start = qbnd + k * sym_len
                if start + sym_len > len(sig):
                    break
                ax.plot(np.arange(sym_len) / sym_len,
                        qsig[start:start + sym_len], lw=0.6, alpha=0.4)
            ax.axvline((chip_len - 1) / sym_len, color="r", ls="--", lw=1.0, alpha=0.7)
            ax.set_title(f"{qname} — {label}", fontsize=10)
            ax.grid(alpha=0.3)

    axs[0, 0].set_ylabel("Amplitude")
    axs[1, 0].set_ylabel("Amplitude")
    axs[1, 0].set_xlabel("Symbol period (red line = optimum sampling point)")
    axs[1, 1].set_xlabel("Symbol period (red line = optimum sampling point)")

    fig.suptitle("Eye diagram: O-QPSK half-sine pulse shaping (I/Q split, peak centered, red line=optimum sampling point)", fontsize=13)
    fig.tight_layout(); fig.savefig(OUT / "vis_eye.png", dpi=130); plt.close(fig)
    print("✓ vis_eye.png")


# ==========================================================
# 图 9b: 眼图 —— 逐层加入非理想效应 (I/Q 分路, 每栏独立注入单一非理想)
# ==========================================================
def fig_eye_impairments():
    """逐层加非理想: 同一 I/Q 眼在「理想/加噪/频偏/直流/失衡」下的对比 (注入 O(N), 秒出)"""
    snr = 10.0
    chip_len = phy.SPS
    sym_len = 2 * chip_len
    n_traces = 30
    n_data = 10                                   # 数据段起始符号 (跳过前导 8 + SFD 2)
    i_bnd = phy.chip_peak_index(0, n_data * 32) - (chip_len - 1)     # 数据第一个偶数码片边界
    q_bnd = phy.chip_peak_index(0, n_data * 32 + 1) - (chip_len - 1) # 数据第一个奇数码片边界

    rng = np.random.default_rng(2026)
    y_awgn = phy.add_awgn(TX, snr, rng=rng)

    # 每栏独立注入单一非理想 (隔离变量, 除加噪外均无噪声)
    cases = [
        ("Ideal",                phy.matched_filter(TX)),
        (f"+AWGN {snr:.0f} dB",  phy.matched_filter(y_awgn)),
        ("+CFO 50 kHz",          phy.matched_filter(imp.add_cfo(TX, 50e3))),
        ("+DC (0.3, 0.3)",       phy.matched_filter(imp.add_dc_offset(TX, 0.3, 0.3))),
        ("+IQ 2 dB / 10°",       phy.matched_filter(imp.add_iq_imbalance(TX, 2.0, 10.0))),
    ]

    fig, axs = plt.subplots(2, len(cases), figsize=(len(cases) * 3.4, 8.2),
                            sharex=True, sharey=True)

    for col, (label, mf) in enumerate(cases):
        for row, (qname, qsig, qbnd) in enumerate([
                ("I eye (even chips)", mf.real, i_bnd),
                ("Q eye (odd chips)", mf.imag, q_bnd)]):
            ax = axs[row, col]
            for k in range(n_traces):
                start = qbnd + k * sym_len
                if start + sym_len > len(mf):
                    break
                ax.plot(np.arange(sym_len) / sym_len,
                        qsig[start:start + sym_len], lw=0.6, alpha=0.4)
            ax.axvline((chip_len - 1) / sym_len, color="r", ls="--", lw=0.9, alpha=0.6)
            ax.grid(alpha=0.3)
            if row == 0:
                ax.set_title(label, fontsize=11)
            if col == 0:
                ax.set_ylabel(qname, fontsize=10)
            if row == 1:
                ax.set_xlabel("Symbol period", fontsize=9)

    fig.suptitle("Eye diagram: adding impairments layer by layer (each column injects a single impairment; red line=optimum sampling point)", fontsize=13)
    fig.tight_layout(); fig.savefig(OUT / "vis_eye_impairments.png", dpi=130); plt.close(fig)
    print("✓ vis_eye_impairments.png")


# ==========================================================
# 图 9c: 加扰(PN9白化)效果 —— 频谱变平坦, 但眼图张开度不变
# ==========================================================
def fig_whitening():
    """加扰让数据随机化(去离散谱线), 但不改变信号能量/眼图张开度 (眼图大小靠扩频增益)"""
    psdu = b"\x00" * 20                                  # 全0数据: 未加扰时码片周期重复
    sym_plain = phy.ppdu_symbols(psdu)                   # 未加扰
    sym_whiten = phy.tx_symbols(psdu)                    # 加扰 (PN9 白化)
    tx_plain = phy.modulate_oqpsk(phy.symbols_to_chips(sym_plain))
    tx_whiten = phy.modulate_oqpsk(phy.symbols_to_chips(sym_whiten))

    fs = phy.SPS * phy.CHIP_RATE
    chip_len = phy.SPS
    sym_len = 2 * chip_len
    n_traces = 30
    i_bnd = phy.chip_peak_index(0, 10 * 32) - (chip_len - 1)   # 数据段第一个偶数码片边界

    fig, axs = plt.subplots(2, 2, figsize=(13, 8))

    # ①② 频谱: 离散谱线 -> 平坦
    for ax, (label, tx) in zip(axs[0], [("unwhitened spectrum (all 0 → chip period repeats → discrete lines)", tx_plain),
                                         ("whitened spectrum (random data → flat spectrum)", tx_whiten)]):
        X = np.fft.fftshift(np.fft.fft(tx * np.hanning(len(tx))))
        f = np.fft.fftshift(np.fft.fftfreq(len(tx), 1 / fs))
        P = 20 * np.log10(np.abs(X) / np.max(np.abs(X)) + 1e-12)
        ax.plot(f / 1e6, P, lw=0.7)
        ax.set_xlim(-2, 2); ax.set_ylim(-70, 3)
        ax.set_xlabel("MHz"); ax.set_ylabel("dB")
        ax.set_title(label, fontsize=11)
        ax.grid(alpha=0.3)

    # ③④ 眼图: 张开度不变 (±4)
    for ax, (label, tx) in zip(axs[1], [("unwhitened I eye (chip repeats, few traces)", tx_plain),
                                         ("whitened I eye (random chips, full traces)", tx_whiten)]):
        mf = phy.matched_filter(tx)
        for k in range(n_traces):
            start = i_bnd + k * sym_len
            if start + sym_len > len(mf):
                break
            ax.plot(np.arange(sym_len) / sym_len, mf.real[start:start + sym_len],
                    lw=0.6, alpha=0.4)
        ax.axvline((chip_len - 1) / sym_len, color="r", ls="--", lw=1.0, alpha=0.7)
        ax.set_xlabel("Symbol period"); ax.set_ylabel("I-path amplitude")
        ax.set_title(label, fontsize=11)
        ax.grid(alpha=0.3)

    fig.suptitle("Whitening (PN9) effect: spectrum flattens, but eye opening is unchanged (±4 comes from spreading gain, not whitening)", fontsize=13)
    fig.tight_layout(); fig.savefig(OUT / "vis_whitening.png", dpi=130); plt.close(fig)
    print("✓ vis_whitening.png")


# ==========================================================
# 图 10: 变频结果 —— 送到中频 → 欠采样 → DDC 到基带 (独立复现带通采样的接收侧)
# ==========================================================
def fig_downconvert():
    # 模拟: 把复基带搬到 2.405 GHz, 用 8 MHz 采样, 再 DDC
    fs_hi = phy.SPS * phy.CHIP_RATE * 300        # 4.8 GHz 高仿真率 (能表示 2.4 GHz)
    N_up = int(len(TX) * fs_hi / (phy.SPS * phy.CHIP_RATE))
    t_hi = np.arange(N_up) / fs_hi

    # 截取前 40 个符号的复基带
    n_cut = min(len(TX), 40 * 32 * phy.SPS)
    bb = np.zeros(N_up, dtype=complex)
    bb[:n_cut] = TX[:n_cut] * np.hanning(n_cut)

    fc = 2405e6
    rf = np.real(bb * np.exp(2j * np.pi * fc * t_hi))   # 实带通信号

    fs = 8e6
    dec = int(fs_hi / fs)
    rf_s = rf[::dec]
    n_s = np.arange(len(rf_s))
    if_sig = rf_s * np.exp(-2j * np.pi * 3e6 * n_s / fs)  # DDC 混频到基带

    fig, axs = plt.subplots(3, 1, figsize=(12, 9))

    # ① 2.4 GHz 通带
    f_hi = np.fft.fftshift(np.fft.fftfreq(N_up, 1 / fs_hi))
    P_hi = 20 * np.log10(np.abs(np.fft.fftshift(np.fft.fft(rf))) + 1e-12)
    axs[0].plot(f_hi / 1e6, P_hi, lw=0.7)
    axs[0].set_xlim(2395, 2415)
    axs[0].set_xlabel("MHz"); axs[0].set_ylabel("dB")
    axs[0].set_title("① 2.4 GHz passband signal (after complex baseband upconversion)")
    axs[0].grid(alpha=0.3)

    # ② 欠采样后低中频
    f_s = np.fft.fftshift(np.fft.fftfreq(len(rf_s), 1 / fs))
    P_s = 20 * np.log10(np.abs(np.fft.fftshift(np.fft.fft(rf_s * np.hanning(len(rf_s))))) + 1e-12)
    axs[1].plot(f_s / 1e6, P_s, lw=0.7)
    axs[1].axvspan(2, 4, color="g", alpha=0.12, label="folded into [2,4] MHz")
    axs[1].set_xlim(-4, 4); axs[1].set_xlabel("MHz"); axs[1].set_ylabel("dB")
    axs[1].set_title(f"② after undersampling fs = {fs/1e6:.0f} MHz: 2.4 GHz band folded to low IF")
    axs[1].legend(); axs[1].grid(alpha=0.3)

    # ③ DDC 后基带
    f_b = np.fft.fftshift(np.fft.fftfreq(len(if_sig), 1 / fs))
    P_b = 20 * np.log10(np.abs(np.fft.fftshift(np.fft.fft(if_sig * np.hanning(len(if_sig))))) + 1e-12)
    axs[2].plot(f_b / 1e6, P_b, lw=0.7)
    axs[2].set_xlim(-4, 4); axs[2].set_xlabel("MHz"); axs[2].set_ylabel("dB")
    axs[2].set_title("③ after DDC (3 MHz mixing) — complex baseband recovered")
    axs[2].grid(alpha=0.3)

    fig.suptitle("Frequency conversion chain: RF → undersampling → low IF → digital down-conversion → complex baseband", fontsize=13)
    fig.tight_layout(); fig.savefig(OUT / "vis_downconvert.png", dpi=130); plt.close(fig)
    print("✓ vis_downconvert.png")


def fig_despread_gain():
    """扩频增益: 糊透的码片星座 vs 清晰的符号判决 (测量用 measure.corr_power)"""
    SNR = -1.0
    rng = np.random.default_rng(42)
    n_sym = 120
    syms = rng.integers(0, 16, size=n_sym)
    mf = phy.matched_filter(phy.add_awgn(
        phy.modulate_oqpsk(phy.symbols_to_chips(syms)), SNR, rng=rng))
    soft = phy.sample_chips(mf, 0, len(syms) * 32) / 4.0
    pwr = measure.corr_power(soft, phy.CHIP)          # ← 库: 16 路相关功率
    got = pwr.argmax(axis=1)
    err = int(np.sum(got != syms))
    margin_db = [10 * np.log10(p[np.argmax(p)] / np.delete(p, np.argmax(p)).max())
                 for p, s in zip(pwr, syms) if np.argmax(p) == s]

    fig, axs = plt.subplots(2, 2, figsize=(13, 10))
    visualize.plot_constellation(soft[:160], ax=axs[0, 0], s=14, c="tab:red",
                                 title=f"before despread: chip constellation @ {SNR} dB (smeared)")
    i_show = 5
    p = pwr[i_show]
    axs[0, 1].bar(np.arange(16), p, color=["tab:red" if k == got[i_show] else "tab:blue"
                                           for k in range(16)], alpha=0.85)
    axs[0, 1].set_xticks(np.arange(16))
    axs[0, 1].set_title(f"after despread: symbol {i_show} 16-path correlation power (red=argmax, margin "
                        f"{10*np.log10(p[got[i_show]]/np.sort(p)[-2]):.1f} dB)")
    axs[0, 1].set_xlabel("PN index k"); axs[0, 1].set_ylabel("|correlation|²")
    show = pwr[:40].T
    axs[1, 0].imshow(show, aspect="auto", cmap="viridis",
                     norm=matplotlib.colors.LogNorm(vmin=show.max() * 0.02, vmax=show.max()))
    axs[1, 0].plot(np.arange(40), syms[:40], "wx", ms=7, mew=1.5)
    axs[1, 0].set_xlabel("Symbol index"); axs[1, 0].set_ylabel("PN index k")
    axs[1, 0].set_title("40 symbols × 16-path correlation power heatmap (white ×=transmit, along diagonal=all correct)")
    axs[1, 1].hist(margin_db, bins=12, color="tab:green", alpha=0.8, edgecolor="white")
    axs[1, 1].axvline(np.mean(margin_db), color="r", ls="--", lw=1.5,
                      label=f"mean {np.mean(margin_db):.1f} dB")
    axs[1, 1].set_title(f"decision margin distribution (correct path vs second-highest, N={len(margin_db)})\n"
                        f"32-chip correlation processing gain ≈ 9 dB: chip {SNR} dB → symbol equivalent ~8 dB")
    axs[1, 1].set_xlabel("Margin (dB)"); axs[1, 1].set_ylabel("Symbol count"); axs[1, 1].legend()
    fig.suptitle(f"Spreading gain visualization: chip SNR={SNR} dB smeared constellation → clear decision after despread ({err}/{n_sym} errors)",
                 fontsize=14)
    fig.tight_layout(rect=(0, 0, 1, 0.95)); fig.savefig(OUT / "vis_despread_gain.png", dpi=140)
    plt.close(fig); print("✓ vis_despread_gain.png")


def fig_cfo_rotation():
    """CFO 旋转可视化 + 谱峰法粗估验证 (谱峰用 measure.spectrum)"""
    CFO_HZ, SNR = 50e3, -1.0
    rng = np.random.default_rng(555)
    syms = phy.ppdu_symbols(bytes(rng.integers(0, 256, size=20).tolist()))
    tx = phy.modulate_oqpsk(phy.symbols_to_chips(syms))
    y_cfo = imp.add_cfo(phy.add_awgn(tx, SNR, rng=rng), CFO_HZ)
    mf0 = phy.matched_filter(phy.add_awgn(tx, SNR, rng=rng))
    mfc = phy.matched_filter(y_cfo)
    p, _, _ = phy.correlate_preamble(mf0, return_corr=True)
    chips0 = phy.extract_preamble_chips(mf0, p, derail=True)
    chipsc = phy.extract_preamble_chips(mfc, p, derail=True)

    # 谱峰法: 去调制 → DFT 定位 Δf
    zc = (phy.extract_preamble_chips(mfc, p, derail=False)
          * np.conj(phy.known_preamble_chips(derail=False)))
    N_FFT = 8192
    freqs, spec = measure.spectrum(zc, fs=phy.CHIP_RATE, n_fft=N_FFT)   # ← 库
    kmax = int(np.argmax(spec))
    f_peak = freqs[kmax]

    fig, axs = plt.subplots(2, 2, figsize=(13, 10))
    visualize.plot_constellation(chips0[:64], ax=axs[0, 0], s=28, c="tab:blue",
                                 label="no CFO (real axis ±1)")
    axs[0, 0].scatter(chipsc.real[:64], chipsc.imag[:64], s=28, c="tab:red", marker="x",
                      label=f"CFO {CFO_HZ/1e3:.0f} kHz (rotating)")
    axs[0, 0].set_xlim(-1.4, 1.4); axs[0, 0].set_ylim(-1.4, 1.4); axs[0, 0].legend()
    axs[0, 0].set_title("complex plane: preamble chip samples (rotation = uniform phase growth)")
    axs[1, 0].plot(freqs / 1e3, 20 * np.log10(spec + 1e-12), lw=0.8)
    axs[1, 0].axvline(CFO_HZ / 1e3, color="r", ls="--", lw=1, label=f"true {CFO_HZ/1e3:.0f} kHz")
    axs[1, 0].axvline(f_peak / 1e3, color="g", ls=":", lw=1, label=f"bin peak {f_peak/1e3:.2f} kHz")
    axs[1, 0].set_xlim(-300, 300); axs[1, 0].set_title("spectral peak coarse estimate: DFT of z[n] locates Δf (unambiguous)")
    axs[1, 0].set_xlabel("Frequency (kHz)"); axs[1, 0].set_ylabel("|Z| (dB)"); axs[1, 0].legend()
    f_coarse = phy.estimate_cfo_coarse(mfc)
    f_fine, sigma_f = phy.estimate_cfo_fine(y_cfo, f_coarse)
    axs[0, 1].axis("off")
    axs[0, 1].text(0.05, 0.72, f"spectral peak bin peak = {f_peak/1e3:.2f} kHz", fontsize=13)
    axs[0, 1].text(0.05, 0.52, f"grid coarse search = {f_coarse/1e3:.2f} kHz", fontsize=13)
    axs[0, 1].text(0.05, 0.32, f"fine estimate = {f_fine/1e3:.3f} kHz (σ={sigma_f/1e3:.3f})", fontsize=13)
    axs[0, 1].text(0.05, 0.12, f"residual = {abs(f_fine-CFO_HZ)/1e3:.3f} kHz", fontsize=13)
    axs[0, 1].set_title("estimate comparison")
    axs[1, 1].axis("off")
    axs[1, 1].text(0.05, 0.6, "spectral peak method = no search path", fontsize=13)
    axs[1, 1].text(0.05, 0.4, "phase trajectory slope → Δf", fontsize=13)
    axs[1, 1].text(0.05, 0.2, "grid coarse search fragility see estimate_cfo_coarse", fontsize=11, color="gray")
    fig.suptitle(f"CFO rotation visualization and estimation (chip SNR={SNR} dB)", fontsize=15)
    fig.tight_layout(rect=(0, 0, 1, 0.96)); fig.savefig(OUT / "vis_cfo_rotation.png", dpi=140)
    plt.close(fig); print("✓ vis_cfo_rotation.png")


def fig_byte_journey():
    """1 字节的旅程: bit → 码片 → 成形求和 → IQ/射频 → 相关 → 还原"""
    BYTE = 0xA7
    syms = [BYTE & 0xF, BYTE >> 4]
    chips = np.concatenate([phy.CHIP[syms[0]], phy.CHIP[syms[1]]])
    wave = phy.modulate_oqpsk(chips)
    mf = phy.matched_filter(wave)
    soft = phy.sample_chips(mf, 0, 64) / 4.0
    pwr = measure.corr_power(soft, phy.CHIP)          # ← 库
    got = pwr.argmax(axis=1)

    fig, axs = plt.subplots(4, 2, figsize=(18, 13), gridspec_kw={"hspace": 0.5, "wspace": 0.25})
    bits = np.unpackbits(np.array([BYTE], dtype=np.uint8))
    axs[0, 0].step(np.arange(8) + 0.5, bits, where="mid", lw=2, color="tab:blue")
    axs[0, 0].set_xticks(np.arange(8) + 0.5); axs[0, 0].set_xticklabels([f"b{7-i}" for i in range(8)])
    axs[0, 0].set_ylim(-0.15, 1.3); axs[0, 0].set_yticks([0, 1])
    axs[0, 0].set_title(f"① 1 byte: {BYTE:02x} = 1010 0111 → symbols {syms[0]}/{syms[1]}")
    axs[1, 0].plot(np.arange(64), chips, "s-", ms=5, lw=0.9, color="tab:blue")
    axs[1, 0].axvline(31.5, color="gray", ls="--", lw=1)
    axs[1, 0].set_title("② spreading: 2 symbols → 64 chips ±1"); axs[1, 0].set_ylim(-1.5, 1.6)
    h = phy.half_sine(phy.SPS)
    axs[2, 0].plot(np.arange(len(h)), h, "o-", ms=4, lw=1, color="gray", label="half-sine pulse")
    visualize.plot_waveform(wave, ax=axs[2, 0], title="③ shaping = sum (I even chips / Q odd chips half-chip offset)")
    axs[2, 0].set_xlim(0, 8); axs[2, 0].legend(fontsize=8, loc="upper right")
    visualize.plot_waveform(wave, ax=axs[3, 0], title="④ complex baseband waveform = I + jQ (16 Msps)")
    n_rf = 200; f_dem = 2e6
    t_rf = np.arange(n_rf) / 16e6
    s_rf = wave.real[:n_rf] * np.cos(2*np.pi*f_dem*t_rf) - wave.imag[:n_rf] * np.sin(2*np.pi*f_dem*t_rf)
    env = np.sqrt(wave.real[:n_rf]**2 + wave.imag[:n_rf]**2)
    axs[0, 1].plot(t_rf * 1e6, s_rf, lw=0.6, color="k")
    axs[0, 1].plot(t_rf * 1e6, env, lw=1.4, color="tab:green", label="envelope")
    axs[0, 1].plot(t_rf * 1e6, -env, lw=1.4, color="tab:green", alpha=0.6)
    axs[0, 1].set_title(f"⑤ oscilloscope: RF real voltage (f₀={f_dem/1e6:.0f} MHz, illustrative)")
    axs[0, 1].set_xlabel("Time (µs)"); axs[0, 1].legend(fontsize=8)
    visualize.plot_waveform(mf, ax=axs[1, 1], title="⑥ matched filter → 64 chip peaks (peak ±4)")
    x = np.arange(16); w1 = 0.42
    axs[2, 1].bar(x - w1/2, pwr[0], width=w1, color=["tab:red" if k == got[0] else "tab:blue" for k in range(16)])
    axs[2, 1].bar(x + w1/2, pwr[1], width=w1, color=["tab:red" if k == got[1] else "tab:green" for k in range(16)], alpha=0.85)
    axs[2, 1].set_title(f"⑦ correlation: argmax = {got[0]}/{got[1]}, sent = {syms[0]}/{syms[1]}")
    axs[2, 1].set_xlabel("PN index k")
    axs[3, 1].axis("off")
    byte_back = (got[1] << 4) | got[0]
    axs[3, 1].text(0.08, 0.72, "recover: symbol → 4 bit ×2 → byte", fontsize=13)
    axs[3, 1].text(0.08, 0.52, f"{got[1]:04b} {got[0]:04b}  =  {byte_back:02X}", fontsize=16, family="monospace")
    axs[3, 1].text(0.08, 0.3, f"sent {BYTE:02X} → recovered {byte_back:02X}  "
                   + ("match ✓" if byte_back == BYTE else "mismatch ×"), fontsize=14, color="tab:green")
    fig.suptitle(f"journey of 1 byte ({BYTE:02X}): bit → chip → summed shaping → IQ/RF → correlation → recovery", fontsize=15)
    fig.tight_layout(); fig.savefig(OUT / "vis_byte_journey.png", dpi=130)
    plt.close(fig); print("✓ vis_byte_journey.png")


def fig_derotation():
    """CFO 消旋效果: 星座(环→聚轴)、相位轨迹(陡线→平线)、解扩符号错误率"""
    CFO_HZ, SNR = 50e3, -1.0
    rng = np.random.default_rng(555)
    syms = phy.ppdu_symbols(bytes(rng.integers(0, 256, size=20).tolist()))
    tx = phy.modulate_oqpsk(phy.symbols_to_chips(syms))
    y_cfo = imp.add_cfo(phy.add_awgn(tx, SNR, rng=rng), CFO_HZ)
    mf_before = phy.matched_filter(y_cfo)
    f_fine, sigma_f = phy.estimate_cfo_fine(y_cfo, phy.estimate_cfo_coarse(mf_before))
    mf_after = phy.matched_filter(phy.correct_cfo(y_cfo, f_fine))
    res = abs(f_fine - CFO_HZ)
    p, _, _ = phy.correlate_preamble(phy.matched_filter(phy.add_awgn(tx, SNR, rng=rng)),
                                     return_corr=True)
    c_before = phy.extract_preamble_chips(mf_before, p, derail=True)
    c_after = phy.extract_preamble_chips(mf_after, p, derail=True)

    def traj(mf_sig):
        v = phy.extract_preamble_chips(mf_sig, p, derail=False)
        zc = v * np.conj(phy.known_preamble_chips(derail=False))
        m = np.arange(len(zc))
        t = (m * phy.SPS + np.where(m % 2, phy.SPS // 2, 0) + (phy.SPS - 1)) / 16e6
        return np.unwrap(np.angle(zc)), t
    phi_b, t = traj(mf_before)
    phi_a, _ = traj(mf_after)

    n_sym = len(syms)
    s_before = phy.despread_chips(phy.sample_chips(mf_before, p, n_sym * 32))
    s_after = phy.despread_chips(phy.sample_chips(mf_after, p, n_sym * 32))
    ber_before = measure.ser(s_before, syms)   # ← 库
    ber_after = measure.ser(s_after, syms)

    fig, axs = plt.subplots(2, 3, figsize=(15, 9))
    visualize.plot_constellation(c_before[:64], ax=axs[0, 0], s=26, c="tab:red", marker="x",
                                 title="before derotation: preamble chip constellation (ring)")
    axs[0, 1].axis("off")
    axs[0, 1].text(0.05, 0.85, f"true CFO = {CFO_HZ/1e3:.1f} kHz", fontsize=12)
    axs[0, 1].text(0.05, 0.62, f"fine estimate = {f_fine/1e3:.3f} kHz (σ={sigma_f/1e3:.3f})", fontsize=12)
    axs[0, 1].text(0.05, 0.42, f"residual after derotation = {res/1e3:.3f} kHz", fontsize=12)
    axs[0, 1].set_title("two-stage estimation + derotation")
    visualize.plot_constellation(c_after[:64], ax=axs[0, 2], s=26, c="tab:blue",
                                 title="after derotation: clustered back to real axis ±1")
    axs[1, 0].plot(t * 1e6, phi_b, ".-", ms=3, lw=0.8, c="tab:red", label="trajectory before derotation")
    axs[1, 0].set_title(f"phase trajectory before derotation (rotates ~{2*np.pi*CFO_HZ*128e-6:.0f} rad in 128 µs)")
    axs[1, 0].set_xlabel("Time (µs)"); axs[1, 0].set_ylabel("Phase (rad)")
    axs[1, 1].bar(["before derotation", "after derotation"], [ber_before, ber_after], color=["tab:red", "tab:blue"])
    axs[1, 1].set_ylim(0, 1.05)
    axs[1, 1].set_title(f"despread symbol error rate: {ber_before:.2f} → {ber_after:.2f}")
    axs[1, 1].set_ylabel("Symbol error rate")
    axs[1, 2].plot(t * 1e6, phi_a, ".-", ms=3, lw=0.8, c="tab:blue", label="trajectory after derotation")
    axs[1, 2].set_title(f"phase trajectory after derotation (residual {res/1e3:.3f} kHz → visually static)")
    axs[1, 2].set_xlabel("Time (µs)"); axs[1, 2].set_ylabel("Phase (rad)")
    fig.suptitle(f"CFO derotation effect: {CFO_HZ/1e3:.0f} kHz → estimate+mix → residual {res/1e3:.3f} kHz", fontsize=14)
    fig.tight_layout(rect=(0, 0, 1, 0.95)); fig.savefig(OUT / "vis_derotation.png", dpi=140)
    plt.close(fig); print("✓ vis_derotation.png")


def fig_ref_cfo_case():
    """ref 修复成功案例: 有旋信号 (CFO+噪) → 估计消旋 → 整帧还原"""
    CFO_HZ, SNR, SHR = 50e3, 8.0, 10
    rng = np.random.default_rng(31)
    psdu = bytes(rng.integers(0, 256, size=16).tolist())
    syms = phy.tx_symbols(psdu)
    wave = phy.modulate_oqpsk(phy.symbols_to_chips(syms))
    y = imp.add_cfo(phy.add_awgn(wave, SNR, rng=rng), CFO_HZ)
    p0, _, _ = phy.correlate_preamble(phy.matched_filter(wave), return_corr=True)
    mf_dmg = phy.matched_filter(y)
    syms_raw = phy.despread_chips(phy.sample_chips(mf_dmg, p0, len(syms) * 32))
    f_est = phy.estimate_cfo_preamble_diff(y)
    mf2 = phy.matched_filter(phy.correct_cfo(y, f_est))
    p2, _, _ = phy.correlate_preamble(mf2, return_corr=True)
    syms_fix = phy.despread_chips(phy.sample_chips(mf2, p2, len(syms) * 32))
    fix_psdu, fix_ok, fix_len = phy.rx_deframe_symbols(syms_fix[SHR:])
    raw_err = int(np.sum(syms_raw[SHR:] != syms[SHR:]))
    fix_err = int(np.sum(syms_fix[SHR:] != syms[SHR:]))
    res = abs(f_est - CFO_HZ)

    fig, axs = plt.subplots(2, 3, figsize=(15, 9))
    DATA0 = 10 * 32
    z = phy.sample_chips(mf_dmg, p0, DATA0 + 96)[DATA0:] / 4.0
    z2 = phy.sample_chips(mf2, p2, DATA0 + 96)[DATA0:] / 4.0
    visualize.plot_constellation(z, ax=axs[0, 0], s=14, c="tab:red",
                                 title=f"before fix: CFO {CFO_HZ/1e3:.0f} kHz rotated into a ring")
    visualize.plot_constellation(z2, ax=axs[0, 1], s=14, c="tab:green",
                                 title=f"after fix: derotated (residual {res/1e3:.3f} kHz)")
    axs[0, 2].axis("off")
    axs[0, 2].text(0.05, 0.8, f"CFO = {CFO_HZ/1e3:.0f} kHz, SNR = {SNR} dB", fontsize=12)
    axs[0, 2].text(0.05, 0.6, f"inter-block differential estimate f_est = {f_est/1e3:.3f} kHz", fontsize=12)
    axs[0, 2].text(0.05, 0.4, f"before fix: {raw_err} symbol errors", fontsize=12, color="tab:red")
    axs[0, 2].text(0.05, 0.2, f"after fix: {fix_err} symbol errors, fcs_ok={fix_ok}", fontsize=12, color="tab:green")
    axs[0, 2].set_title("case parameters")
    w = syms[SHR:]
    axs[1, 0].plot(np.arange(len(w)), w, "o", ms=4, c="tab:blue", label="sent")
    axs[1, 0].plot(np.arange(len(w)), syms_raw[SHR:], "x", ms=6, c="tab:red", label=f"before fix ({raw_err} err)")
    axs[1, 0].set_ylim(-0.5, 15.5); axs[1, 0].set_title("before fix: despread symbols vs sent")
    axs[1, 0].legend(fontsize=8)
    axs[1, 1].plot(np.arange(len(w)), w, "o", ms=4, c="tab:blue", label="sent")
    axs[1, 1].plot(np.arange(len(w)), syms_fix[SHR:], "x", ms=6, c="tab:green", label=f"after fix ({fix_err} err)")
    axs[1, 1].set_ylim(-0.5, 15.5); axs[1, 1].set_title("after fix: despread symbols vs sent")
    axs[1, 1].legend(fontsize=8)
    axs[1, 2].axis("off")
    axs[1, 2].text(0.05, 0.8, f"sent PSDU ({len(psdu)} B):", fontsize=11)
    axs[1, 2].text(0.05, 0.7, psdu.hex(" "), fontsize=9, family="monospace")
    match = "exact match ✓" if fix_psdu == psdu else "mismatch ×"
    axs[1, 2].text(0.05, 0.4, "after fix: " + match, fontsize=12, color="tab:green")
    axs[1, 2].set_title("PSDU byte-level recovery")
    fig.suptitle("ref fix success case: rotated signal (CFO+noise) → estimate+derotate → full-frame recovery", fontsize=14)
    fig.tight_layout(rect=(0, 0, 1, 0.95)); fig.savefig(OUT / "vis_ref_cfo_case.png", dpi=140)
    plt.close(fig); print("✓ vis_ref_cfo_case.png")


def fig_impairment_gallery():
    """非理想效应渐进图板 (损伤 → 修复): 星座 + 眼图"""
    SPS = phy.SPS
    rng = np.random.default_rng(7)
    syms = phy.ppdu_symbols(bytes(rng.integers(0, 256, size=20).tolist()))
    wave = phy.modulate_oqpsk(phy.symbols_to_chips(syms))
    DATA0, N_SHOW, WIN = 10 * 32, 128, 6 * SPS
    x_win = np.arange(WIN)
    # 眼图叠加要看清, 两个量必须小: (1) AWGN —— 决定眼开处的“毛边”;
    # (2) 残余 CFO —— 叠加窗跨越整帧, 残余相位旋转会让各窗波形错开而糊成一片。
    CFO_HZ, SNR = 2e3, 14.0

    def data_chips(mf, p):
        return phy.sample_chips(mf, p, DATA0 + N_SHOW)[DATA0:] / 4.0

    def repair_cfo(w):
        f = phy.estimate_cfo_preamble_diff(w)
        return phy.correct_cfo(w, f), f

    scenes = [
        ("1. Ideal", lambda w: w, "none"),
        (f"2. +AWGN ({SNR} dB)", lambda w: phy.add_awgn(w, SNR, rng=rng), "none"),
        (f"3. +CFO {CFO_HZ/1e3:.0f} kHz", lambda w: imp.add_cfo(w, CFO_HZ), "cfo"),
        ("4. +DC (0.30+0.20j)", lambda w: imp.add_dc_offset(w, 0.30, 0.20), "dciq"),
        ("5. +I/Q 2dB/10°", lambda w: imp.add_iq_imbalance(w, 2.0, 10.0), "dciq"),
        ("6. +timing +0.25 chip", lambda w: imp.add_timing_offset(w, 0.25), "timing"),
        ("7. +multipath (0.71@1 chip)", lambda w: imp.add_multipath(w, [1.0, 0.707], [0.0, 1.0]), "none"),
        (f"8. combined noise{SNR}dB+CFO{CFO_HZ/1e3:.0f}k+DC+IQ",
         lambda w: imp.add_iq_imbalance(imp.add_dc_offset(
             imp.add_cfo(phy.add_awgn(w, SNR, rng=rng), CFO_HZ), 0.20, 0.15), 1.0, 5.0), "all"),
    ]
    fig, axs = plt.subplots(len(scenes), 4, figsize=(21, 2.5 * len(scenes)))
    for row, (title, dmg_fn, rtype) in enumerate(scenes):
        w = dmg_fn(wave)
        mf = phy.matched_filter(w)
        p, _, _ = phy.correlate_preamble(mf, return_corr=True)
        mf_fixed, p_fixed, note = mf, p, ""
        if rtype in ("cfo", "all"):
            w, f_est = repair_cfo(w)
            mf_fixed = phy.matched_filter(w)
            p_fixed, _, _ = phy.correlate_preamble(mf_fixed, return_corr=True)
            note = f"derotate f_est={f_est/1e3:.2f} kHz"
        if rtype == "dciq":
            a, b, d = phy.estimate_dc_iq(phy.extract_preamble_chips(mf_fixed, p_fixed),
                                         phy.known_preamble_chips(derail=False))
            note = "LS(α,β,d)"
        if rtype == "all":
            a2, b2, d2 = phy.estimate_dc_iq(phy.extract_preamble_chips(mf_fixed, p_fixed),
                                            phy.known_preamble_chips(derail=False))
            w = phy.apply_dc_iq_correction(w, a2 / 4.0, b2 / 4.0, d2 / np.sum(phy.half_sine(SPS)))
            f2 = phy.estimate_cfo_preamble_diff(w)
            w = phy.correct_cfo(w, f2)
            mf_fixed = phy.matched_filter(w)
            p_fixed, _, _ = phy.correlate_preamble(mf_fixed, return_corr=True)
            a3, b3, d3 = phy.estimate_dc_iq(phy.extract_preamble_chips(mf_fixed, p_fixed),
                                            phy.known_preamble_chips(derail=False))
            w = phy.apply_dc_iq_correction(w, a3 / 4.0, b3 / 4.0, d3 / np.sum(phy.half_sine(SPS)))
            mf_fixed = phy.matched_filter(w)
            p_fixed, _, _ = phy.correlate_preamble(mf_fixed, return_corr=True)
            note = f"derotate {f_est/1e3:.2f}k + LS → fine derotate {f2/1e3:.2f}k + LS"
        if rtype == "timing":
            note = "correlation peak relocation"
        if rtype == "none" and row == 1:
            note = "no fix: covered by spreading gain"
        if rtype == "none" and row == 6:
            note = "no fix: DSSS endures (prior ≤1 chip)"
        z_dmg = data_chips(mf, p)
        z_fix = data_chips(mf_fixed, p_fixed)
        visualize.plot_constellation(z_dmg, ax=axs[row, 0], s=14, c="tab:red",
                                     title=f"{title} — impairment")
        visualize.plot_constellation(z_fix, ax=axs[row, 1], s=14, c="tab:green",
                                     title=f"after fix ({note})")
        # O-QPSK 的 I/Q 是**两路独立码片流且时间错开**: 奇数码片走 Q, 其峰比 I 的
        # 偶数码片峰晚 1.5 码片 (12 采样 = 奇偶交替 1 码片 + 标准要求的半码片偏移),
        # 这是 O-QPSK 的定义性特征。二者各按**自己的**码元边界折叠, 分成两个眼图,
        # 各自都能看清眼开; 若叠在同一窗口, 两族脉冲会交错而显得“乱” —— 那其实
        # 就是 O-QPSK 的正常形态, 不是损伤。
        # 注: 分开画后两图的脉冲都居中, 因此**看不出** I/Q 的相对错位; 想观察错位
        # 本身要看波形图那一列 (vis_waveform) 或把它们叠在同一时间轴。
        for ax, sig, bnd, color, name in (
                (axs[row, 2], mf_fixed.real / 4.0,
                 phy.chip_peak_index(p_fixed, DATA0), "tab:blue", "I eye (even chips)"),
                (axs[row, 3], mf_fixed.imag / 4.0,
                 phy.chip_peak_index(p_fixed, DATA0 + 1), "tab:red",
                 "Q eye (odd chips) — I/Q 错开 1.5 码片")):
            for k in range(24):
                s0 = bnd + k * WIN - WIN // 2
                if s0 < 0 or s0 + WIN > len(sig):
                    continue
                ax.plot(x_win, sig[s0:s0 + WIN], color=color, lw=0.5, alpha=0.45)
            ax.axvline(WIN // 2, color="k", ls="--", lw=0.9, alpha=0.8)
            ax.set_ylim(-1.6, 1.6)
            ax.set_title(name, fontsize=8.5)
    fig.suptitle("Impairments: impairment → fix (constellation/eye diagram, fix = ref closed-loop scheme → RTL spec)", fontsize=14)
    fig.tight_layout(rect=(0, 0, 1, 0.985)); fig.savefig(OUT / "vis_impairment_gallery.png", dpi=130)
    plt.close(fig); print("✓ vis_impairment_gallery.png")


def main():
    phy._selftest()
    print(f"Output dir: {OUT}\n")
    fig_constellations()      # 修好版
    fig_sync_scan()
    fig_chip_corr()
    fig_iq_irr()
    fig_ber_scenarios()     # BER 多场景对比 (粗搜已向量化 + 降帧 N_FRAMES=120, 约 4 分钟)
    fig_waveform()            # 新增
    fig_spectrum()            # 新增
    fig_rx_chain()            # 新增
    fig_eye()                 # 新增
    fig_eye_impairments()     # 新增: 逐层加非理想
    fig_whitening()           # 新增: 加扰效果 (频谱平坦/眼图不变)
    fig_downconvert()         # 新增
    fig_despread_gain()       # 扩频增益 (并入)
    fig_cfo_rotation()        # CFO 旋转 + 谱峰法 (并入)
    fig_byte_journey()        # 1 字节旅程 (并入)
    fig_derotation()          # 消旋效果 (并入)
    fig_ref_cfo_case()        # 整帧还原案例 (并入)
    fig_impairment_gallery()  # 损伤→修复渐进图板 (并入)
    print("\nAll figures generated. Open model/out/ to view.")


if __name__ == "__main__":
    main()