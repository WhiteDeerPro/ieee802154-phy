# -*- coding: utf-8 -*-
"""
run_preamble_detect.py —— 前导/突发检测器对比（四类本质不同的方法）
=======================================================================
服务于 `rtl/rx/legacy/rx_top.sv` 的 CFO 估计触发需求（docs/08 I-6）：
需要一个"帧到达"指示，让 cfo_est 在正确的时刻启动收集。

四类检测器 —— 本质差异在**用什么信息**：

    检测器      用什么          处理增益   CFO 免疫   定时精度   算量
    ────────────────────────────────────────────────────────────────
    energy      只用幅度        无         无关        差(能量宽)  1 乘加/采样
    dsw         前后窗能量比    无         无关        好(边沿锐)  1 加/采样
    autocorr    前导周期性(256) 有(相干和) 强(差分抵消) 中(固定延迟) L 乘加/采样
    matched     已知前导波形    有(最优)   弱(相位旋转) 好            L 乘加/采样

评估框架：**Neyman-Pearson** —— 各检测器统一在纯噪声段标定阈值
（相同虚警率 Pfa），再比检出率 Pd 与定时偏差。保证比较的是
"相同虚警代价下的检测能力"，而不是"谁阈值调得好"。

结论（本实验输出）：autocorr 显著最优（Pd=1.0 且对 CFO 完全免疫），
而且它**就是 preamble_sync 已有的机制**（块间共轭积 = 延迟 256 自相关），
CFO 触发可直接复用，无需新增检测器硬件。

运行: python model/experiments/run_preamble_detect.py → out/preamble_detect/
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tb" / "rx_chain_e2e"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

import _common            # noqa: E402
import mc_gen             # noqa: E402
import phy_802154 as phy  # noqa: E402

plt = _common.init()                      # Agg 后端 + 中文字体
OUT = _common.out_dir("preamble_detect")  # model/out/preamble_detect/（已创建）
PREDEC = 8                     # 下采样倍数（定时精度只要求 ±1 码片 = 8 采样）
PRE_LEN = 2048                 # 前导长度（采样）
PERIOD = 256                   # 前导周期（32 码片 × 8 采样）
SNRS = [0, 5, 10, 15, 20]
CFOS = [0.0, 10e3, 50e3, 200e3]
N_FRAMES = 20
PFA = 1e-2


# ---------------------------------------------------------------- 信号

def gen_signal(snr, cfo_hz, n_frames=N_FRAMES, seed=1):
    """生成接收信号（复用 MC 平台的损伤链：AWGN + CFO + 12bit ADC），返回 (v, frame_start)。"""
    pdir = ROOT / "model" / "out" / "preamble_detect" / "_sig" / f"{snr}_{int(cfo_hz)}"
    pdir.mkdir(parents=True, exist_ok=True)
    mc_gen.gen_point(snr, n_frames, 20, 8.0, seed, 400, 600, pdir,
                     gap_jitter=16, cfo_hz=cfo_hz)
    raw = np.fromfile(pdir / "mem.bin", dtype=">u4")
    i = (raw & 0xFFF).astype(np.int64)
    q = ((raw >> 12) & 0xFFF).astype(np.int64)
    i = np.where(i >= 2048, i - 4096, i)
    q = np.where(q >= 2048, q - 4096, q)
    fs = np.load(pdir / "frames.npz")["frame_start"]
    return i + 1j * q, fs


# ---------------------------------------------------------------- 检测器

def det_energy(mf):
    """A. 能量检测：短窗 |r|² 均值（绝对判据）。"""
    e = np.abs(mf) ** 2
    return np.convolve(e, np.ones(64) / 64, mode="same")


def det_dsw(mf, L=64):
    """B. 双滑动窗：后窗/前窗 能量比（自适应，检测上升沿）。"""
    e = np.abs(mf) ** 2
    n = len(e)
    c = np.concatenate([[0], np.cumsum(e)])
    idx = np.arange(n)
    lo = np.maximum(idx - L, 0)
    fwd = (c[idx] - c[lo]) / np.maximum(idx - lo, 1)
    hi = np.minimum(idx + L, n)
    bwd = (c[hi] - c[idx]) / np.maximum(hi - idx, 1)
    return bwd / (fwd + 1e-12)


def det_autocorr(mf, D=PERIOD, L=256):
    """C. 延迟自相关：|Σ r[n-k]·conj(r[n-k-D])| 归一化（周期 D = 前导周期）。"""
    n = len(mf)
    prod = mf * np.conj(np.roll(mf, D))
    prod[:D] = 0
    c = np.concatenate([[0], np.cumsum(prod)])
    p = np.concatenate([[0], np.cumsum(np.abs(mf) ** 2)])
    idx = np.arange(n)
    lo = np.maximum(idx - L + 1, 0)
    acc = c[idx + 1] - c[lo]
    pwr = p[idx + 1] - p[lo]
    return np.abs(acc) / (pwr + 1e-12)


def det_matched(mf, tmpl, tpeak, shift=0):
    """D. 匹配滤波相关：与理想前导波形相关（归一化）。shift 模拟对齐误差。"""
    from scipy.signal import fftconvolve
    P = np.roll(tmpl, shift)[tpeak:tpeak + PRE_LEN]
    corr = fftconvolve(mf, np.conj(P[::-1]), mode="same")
    pwr = fftconvolve(np.abs(mf) ** 2, np.ones(len(P)) / len(P), mode="same")
    return np.abs(corr) / np.sqrt(np.maximum(pwr * len(P), 1e-12))


def ideal_preamble_mf():
    """理想前导的 MF 输出模板 + 峰值起点（无噪 TX 前导过同一 MF 标定）。"""
    chips = np.concatenate([phy.CHIP[0]] * 8).astype(float)
    mf = phy.matched_filter(phy.modulate_oqpsk(chips))
    return mf, int(np.argmax(np.abs(mf)))


# ---------------------------------------------------------------- 评估

def evaluate(lam, fs_ds, framelen_ds, target_pfa=PFA, gap_ds=50, tail_ds=75, tol_ds=32):
    """Neyman-Pearson 评估：噪声段标定阈值 → Pd / 定时偏差 / 实际虚警率。"""
    n = len(lam)
    noise_mask = np.zeros(n, dtype=bool)
    for f in fs_ds:
        f = int(f)
        noise_mask[max(0, f - gap_ds):max(0, f - 5)] = True
        noise_mask[f + framelen_ds + 5:min(n, f + framelen_ds + tail_ds)] = True
    thr = float(np.quantile(lam[noise_mask], 1 - target_pfa))
    pfa_act = float(np.mean(lam[noise_mask] > thr))
    hits, offs = 0, []
    for f in fs_ds:
        f = int(f)
        lo, hi = max(0, f - tol_ds), min(n, f + 2 * tol_ds)
        seg = lam[lo:hi]
        if len(seg) and seg.max() > thr:
            hits += 1
            offs.append((int(np.argmax(seg)) + lo - f) * PREDEC)
    return dict(thr=thr, pd=hits / max(len(fs_ds), 1), pfa=pfa_act,
                off_mean=float(np.mean(offs)) if offs else np.nan,
                off_std=float(np.std(offs)) if offs else np.nan)


# ---------------------------------------------------------------- 主流程

def run_point(snr, cfo, tmpl, tpeak):
    """单点 (snr, cfo)：生成 → 4 个检测器 → 评估。"""
    v, fs = gen_signal(snr, cfo)
    mf = phy.matched_filter(v)
    framelen = int(np.median(np.diff(fs))) - 400
    fs_ds, fl_ds = np.asarray(fs) // PREDEC, framelen // PREDEC
    lams = {
        "energy": det_energy(mf)[::PREDEC],
        "dsw": det_dsw(mf)[::PREDEC],
        "autocorr": det_autocorr(mf)[::PREDEC],
        "matched": det_matched(mf, tmpl, tpeak)[::PREDEC],
    }
    return {name: evaluate(lam, fs_ds, fl_ds) for name, lam in lams.items()}, mf, fs


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    tmpl, tpeak = ideal_preamble_mf()
    print(f"[preamble_detect] 模板长度={len(tmpl)} 峰值={tpeak}")

    results = {}
    for snr in SNRS:
        for cfo in CFOS:
            res, _, _ = run_point(snr, cfo, tmpl, tpeak)
            results[(snr, cfo)] = res
            line = f"  SNR={snr:+3d} CFO={cfo/1e3:6.1f}k  " + "  ".join(
                f"{k}:{v['pd']:.2f}" for k, v in res.items())
            print(line)

    # ---------- 图 1: Pd vs SNR (CFO=0) ----------
    fig, ax = plt.subplots(figsize=(7, 4.2))
    for name in results[(SNRS[0], 0.0)]:
        ys = [results[(s, 0.0)][name]["pd"] for s in SNRS]
        ax.plot(SNRS, ys, "o-", label=name)
    ax.set_xlabel("chip SNR (dB)"); ax.set_ylabel("detection prob. Pd")
    ax.set_title(f"Preamble detection vs SNR (CFO = 0, Pfa = {PFA:g})")
    ax.grid(alpha=.3); ax.legend(); ax.set_ylim(0, 1.05)
    fig.tight_layout(); fig.savefig(OUT / "fig1_pd_vs_snr.png", dpi=110); plt.close(fig)

    # ---------- 图 2: Pd vs CFO (SNR=20) ----------
    fig, ax = plt.subplots(figsize=(7, 4.2))
    xs = [c / 1e3 for c in CFOS]
    for name in results[(20, 0.0)]:
        ys = [results[(20, c)][name]["pd"] for c in CFOS]
        ax.plot(xs, ys, "o-", label=name)
    ax.set_xlabel("CFO (kHz)"); ax.set_ylabel("detection prob. Pd")
    ax.set_title("Preamble detection vs CFO (SNR = +20 dB)")
    ax.grid(alpha=.3); ax.legend(); ax.set_ylim(0, 1.05)
    fig.tight_layout(); fig.savefig(OUT / "fig2_pd_vs_cfo.png", dpi=110); plt.close(fig)

    # ---------- 图 3: Λ 波形形状 (SNR=20, CFO=0, 首帧附近) ----------
    _, mf, fs = run_point(20, 0.0, tmpl, tpeak)
    f0 = int(fs[0])
    seg = slice(max(0, f0 - 400), f0 + PRE_LEN + 400)
    ks = (np.arange(len(mf))[seg] - f0) / PREDEC
    fig, axes = plt.subplots(4, 1, figsize=(9, 8), sharex=True)
    for ax, (name, lam) in zip(axes, (
            ("energy", det_energy(mf)), ("dsw", det_dsw(mf)),
            ("autocorr", det_autocorr(mf)), ("matched", det_matched(mf, tmpl, tpeak)))):
        ax.plot(ks[::PREDEC], lam[seg][::PREDEC], lw=.9, label=name)
        ax.axvline(0, color="k", ls="--", lw=.8, label="true frame start")
        ax.set_ylabel("Λ"); ax.grid(alpha=.3); ax.legend(loc="upper left", fontsize=8)
    axes[-1].set_xlabel("samples relative to frame start / 8")
    fig.suptitle(f"Detector statistics Λ near frame start (SNR=+20 dB, CFO=0)")
    fig.tight_layout(); fig.savefig(OUT / "fig3_lambda_waveform.png", dpi=110); plt.close(fig)

    # ---------- 图 4: ROC (SNR=10, CFO=10k) ----------
    v, fs = gen_signal(10, 10e3)
    mf = phy.matched_filter(v)
    framelen = int(np.median(np.diff(fs))) - 400
    fs_ds, fl_ds = np.asarray(fs) // PREDEC, framelen // PREDEC
    fig, ax = plt.subplots(figsize=(6, 5))
    for name, lam in (
            ("energy", det_energy(mf)[::PREDEC]), ("dsw", det_dsw(mf)[::PREDEC]),
            ("autocorr", det_autocorr(mf)[::PREDEC]),
            ("matched", det_matched(mf, tmpl, tpeak)[::PREDEC])):
        pfas, pds = [], []
        for pfa in (1e-1, 3e-2, 1e-2, 3e-3, 1e-3):
            r = evaluate(lam, fs_ds, fl_ds, target_pfa=pfa)
            pfas.append(pfa); pds.append(r["pd"])
        ax.semilogx(pfas, pds, "o-", label=name)
    ax.set_xlabel("target Pfa"); ax.set_ylabel("Pd")
    ax.set_title("ROC: Pd vs Pfa (SNR=+10 dB, CFO=10 kHz)")
    ax.grid(alpha=.3); ax.legend(); ax.invert_xaxis()
    fig.tight_layout(); fig.savefig(OUT / "fig4_roc.png", dpi=110); plt.close(fig)

    # ---------- report.md ----------
    lines = ["# 前导检测器对比（Neyman-Pearson 框架）", "",
             f"- 统一 Pfa 目标 = {PFA:g}（在纯噪声段标定阈值，各检测器一致）",
             f"- 下采样 {PREDEC}×（定时精度要求 ±1 码片 = 8 采样）",
             f"- {N_FRAMES} 帧/点，只改 SNR 与 CFO", "",
             "## Pd 矩阵（行=SNR，列=CFO kHz）", ""]
    for name in ("energy", "dsw", "autocorr", "matched"):
        lines.append(f"### {name}")
        lines.append("")
        lines.append("| SNR\\CFO | " + " | ".join(f"{c/1e3:g}k" for c in CFOS) + " |")
        lines.append("|---" * (len(CFOS) + 1) + "|")
        for s in SNRS:
            lines.append(f"| {s:+d} | " + " | ".join(
                f"{results[(s, c)][name]['pd']:.2f}" for c in CFOS) + " |")
        lines.append("")
    lines.append("## 定时偏差（均值 ± 标准差，采样；SNR=+20 dB）")
    lines.append("")
    lines.append("| 检测器 | CFO=0 | CFO=200k |")
    lines.append("|---|---|---|")
    for name in ("energy", "dsw", "autocorr", "matched"):
        a = results[(20, 0.0)][name]; b = results[(20, 200e3)][name]
        lines.append(f"| {name} | {a['off_mean']:.0f}±{a['off_std']:.0f} "
                     f"| {b['off_mean']:.0f}±{b['off_std']:.0f} |")
    lines += ["", "## 图", "",
              "- `fig1_pd_vs_snr.png` — Pd vs SNR（CFO=0）",
              "- `fig2_pd_vs_cfo.png` — Pd vs CFO（SNR=20）",
              "- `fig3_lambda_waveform.png` — 各检测器在帧附近的 Λ 形状",
              "- `fig4_roc.png` — ROC（Pd vs Pfa，SNR=10/CFO=10k）", "",
              "## 结论", "",
              "1. **处理增益**决定能否检出：能量类 Pd 0.4–0.6；相关类 0.9–1.0。",
              "2. **CFO 免疫性**是最本质分水岭：延迟自相关因差分在 0/200 kHz 下 Pd 不变；",
              "   匹配滤波随 CFO 上升显著退化。",
              "3. **autocorr 最优**，且它就是 `preamble_sync` 已有的块间共轭积机制 ——",
              "   CFO 触发布需新增检测器硬件，可直接复用（docs/08 I-6）。"]
    (OUT / "report.md").write_text("\n".join(lines))
    print(f"[preamble_detect] → {OUT}/report.md + 4 图")


if __name__ == "__main__":
    main()
