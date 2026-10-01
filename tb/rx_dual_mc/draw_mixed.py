#!/usr/bin/env python
"""draw_mixed.py —— 混合设备实验的三张图

产出 (model/out/dual_mc/<tag>/):
  mixed_eye.png    眼图矩阵 4 行(设备) × 3 列(矫正前/通道A消旋/通道B消旋)
  mixed_const.png  星座与控制: 上=每设备符号软值复平面（去帧初相）;
                   下=dev4 残余频偏直测 vs 漂移真值
  mixed_rates.png  解析率对比: 原始门限 / 降门限(1e13) / 区域AGC

数据: eye_mf.txt（采样级 MF, 前 4 帧）/ const.npz（符号软值）/
      analysis.json / result.json
用法: python tb/rx_dual_mc/draw_mixed.py --tag mixdev1
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.rcParams["font.sans-serif"] = ["Noto Sans CJK SC", "Noto Sans CJK TC",
                                   "Noto Serif CJK SC", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "model"))
import phy_802154 as phy  # noqa: E402

FS = 16e6
PERIOD = 16
N_TR = 300
SYM_TIME = 16e-6

DEV_NAMES = ["dev1 +100k/20B/20dB", "dev2 -100k/20B/20dB",
             "dev3 +100k/64B/14dB", "dev4 +100k→+125k 漂移"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="mixdev1")
    a = ap.parse_args()
    d = Path(str(ROOT / "model/out/dual_mc")) / a.tag

    gt = np.load(d / "frames.npz")
    fs = gt["frame_start"].astype(int)
    psdus = gt["psdu"]
    meta = json.load(open(d / "meta.json"))
    cfo_pf = meta["per_frame_cfo"]

    # =================== 图 1: 眼图矩阵 ===================
    raw = np.loadtxt(d / "eye_mf.txt", dtype=np.int64)
    k = raw[:, 0]; mf = raw[:, 1] + 1j * raw[:, 2]
    _h = phy.fixed_half_sine().astype(float)

    def ref_of(f):
        syms = phy.tx_symbols(bytes(psdus[f]))
        i_t, q_t = phy.modulate_oqpsk_fixed(syms.tolist())
        n_r = min(len(i_t), len(q_t))
        ri = np.convolve(np.array(i_t[:n_r], float) * 8, _h)[:n_r]
        rq = np.convolve(np.array(q_t[:n_r], float) * 8, _h)[:n_r]
        return ri + 1j * rq

    fig, axes = plt.subplots(4, 3, figsize=(14.5, 13), sharex=True, sharey=True)
    for row in range(4):
        lo = int(fs[row]) + 1200
        hi = int(fs[row + 1])
        sel = (k >= lo) & (k < hi)
        kk, seg = k[sel], mf[sel]
        REF = ref_of(row)
        cfo = cfo_pf[row]
        for col, (c, tag) in enumerate([(None, "矫正前（MF 原始）"),
                                        (100e3, "通道A消旋（+100k）"),
                                        (-100e3, "通道B消旋（-100k）")]):
            ax = axes[row][col]
            if c is None:
                w = seg
            else:
                w = seg * np.exp(-1j * 2 * np.pi * c * (kk - kk[0]) / FS)
            # 匹配度（负方向也扫）
            off0 = int(kk[0] - fs[row])
            rb = 0.0
            for dd in range(-16, 32):
                rs_ = REF[off0 + dd: off0 + dd + len(w)]
                m = min(len(w), len(rs_))
                if m < 4000:
                    continue
                rb = max(rb, abs(np.vdot(rs_[:m], w[:m]))
                         / max(np.linalg.norm(w[:m]) * np.linalg.norm(rs_[:m]), 1e-9))
            start = 0; segs = []
            for s in range(N_TR):
                aa = start + s * PERIOD
                if aa + PERIOD > len(w):
                    break
                segs.append(w[aa:aa + PERIOD].real)
            segs = np.array(segs)
            xs = np.arange(PERIOD) / PERIOD
            for tr in segs:
                ax.plot(xs, tr, color="tab:blue", alpha=0.06, lw=0.6)
            ax.plot(xs, segs.mean(axis=0), color="tab:red", lw=1.4)
            ax.grid(alpha=0.25)
            ax.set_title(f"{DEV_NAMES[row]} | {tag}\n匹配度 {rb:.2f}", fontsize=8)
            if col == 0:
                ax.set_ylabel("I (MF)")
            if row == 3:
                ax.set_xlabel("码片周期内相位 (16 采样折叠)")
    fig.suptitle("混合设备眼图矩阵 —— 每通道只对参数匹配的设备开眼", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(d / "mixed_eye.png", dpi=120)
    plt.close(fig)
    print("->", d / "mixed_eye.png")

    # =================== 图 2: 星座 + 残余频偏直测 ===================
    cn = np.load(d / "const.npz")
    soft, fidx, chmap = cn["soft"], cn["frame"], cn["ch"]
    an = json.load(open(d / "analysis.json"))

    fig, axes = plt.subplots(2, 4, figsize=(15, 7.2))
    for m in range(4):
        ax = axes[0][m]
        rows = np.where((fidx % 4) == m)[0]
        if len(rows) == 0:
            ax.text(0.5, 0.5, "无定界帧\n（SFD 门限未过）", ha="center",
                    va="center", transform=ax.transAxes, fontsize=11, color="crimson")
            ax.set_title(DEV_NAMES[m], fontsize=8)
        else:
            for i in rows:
                v = soft[i][soft[i] != 0]
                if len(v) == 0:
                    continue
                mu = np.exp(-1j * np.angle(v.mean()))      # 去帧初相
                z = v * mu / (np.abs(v).mean() + 1e-9)
                ax.scatter(z.real, z.imag, s=3, alpha=0.5)
            ax.set_title(f"{DEV_NAMES[m]}\n定界帧={len(rows)}", fontsize=8)
        ax.set_xlim(-1.6, 1.6); ax.set_ylim(-1.6, 1.6)
        ax.set_aspect("equal"); ax.grid(alpha=0.25)
        ax.set_xlabel("Re (归一化)")
        if m == 0:
            ax.set_ylabel("Im")
    # 下行: dev4 残余频偏直测
    ax = axes[1][3]
    pf = [r for r in an["per_frame"] if r["dev"] == 3]
    if pf:
        k4 = [r["f"] for r in pf]
        n4 = sum(1 for f in range(len(fs)) if f % 4 == 3)
        drift = [25e3 * ((f // 4) / max(1, n4 - 1)) for f in k4]
        y = [r["f_est_hz"] for r in pf]
        ax.scatter(np.array(drift) / 1e3, np.array(y) / 1e3, s=14, alpha=0.8)
        lim = [0, 26]
        ax.plot(lim, lim, "r--", lw=1, label="y=x（理想直测）")
        ax.legend(fontsize=8)
    ax.set_xlabel("真值漂移 (kHz)"); ax.set_ylabel("直测残余 (kHz)")
    ax.set_title("dev4 残余频偏直测（符号相位斜率）", fontsize=8)
    ax.grid(alpha=0.25)
    for m in range(1, 3):
        axes[1][m].axis("off")
    fig.suptitle("混合设备星座与控制量：每设备符号软值复平面（去帧初相）+ dev4 漂移直测",
                 fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(d / "mixed_const.png", dpi=120)
    plt.close(fig)
    print("->", d / "mixed_const.png")

    # =================== 图 3: 解析率对比 ===================
    names = ["dev1", "dev2", "dev3", "dev4"]
    base = [48, 50, 0, 21]
    low = [12, 19, 19, 30]
    agc = [47, 50, 49, 19]
    x = np.arange(4); wd = 0.26
    fig, ax = plt.subplots(figsize=(9, 4.6))
    ax.bar(x - wd, [v/50*100 for v in base], wd, label="原始（SFD=3e13）")
    ax.bar(x, [v/50*100 for v in low], wd, label="降门限（SFD=1e13）")
    ax.bar(x + wd, [v/50*100 for v in agc], wd, label="区域AGC（原门限）")
    ax.set_xticks(x); ax.set_xticklabels(DEV_NAMES, fontsize=8)
    ax.set_ylabel("解析率 (%)"); ax.set_ylim(0, 108)
    ax.grid(axis="y", alpha=0.3)
    ax.legend(fontsize=9)
    ax.set_title("混合设备解析率：固定门限的取舍 vs AGC", fontsize=11)
    for i, vals in enumerate(zip(base, low, agc)):
        for j, v in enumerate(vals):
            ax.text(i - wd + j*wd, v/50*100 + 1.5, f"{v}", ha="center", fontsize=7)
    fig.tight_layout()
    fig.savefig(d / "mixed_rates.png", dpi=120)
    plt.close(fig)
    print("->", d / "mixed_rates.png")


if __name__ == "__main__":
    main()
