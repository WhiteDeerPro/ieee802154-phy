#!/usr/bin/env python
"""analyze_mc.py —— 汇总蒙特卡洛结果 → RTL vs 模型 BER 对比图 + 报告

读取:
  model/out/rtl_ber/data/snr*/result.json     (RTL 各点)
  model/out/rtl_ber/data/model_points.json    (浮点模型对照)
输出:
  model/out/rtl_ber/report.md
  model/out/rtl_ber/ber_compare.png
  model/out/rtl_ber/sync_health.png
  model/out/rtl_ber/ber_points.csv

用法: python model/experiments/analyze_mc.py
"""
import csv
import json
import math
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out" / "rtl_ber"
DATA = OUT / "data"


def qpsk_ber(ebn0_lin):
    return 0.5 * math.erfc(math.sqrt(ebn0_lin))


def collect_rtl():
    pts = []
    for d in sorted(DATA.glob("snr*")):
        f = d / "result.json"
        if f.exists():
            pts.append(json.loads(f.read_text()))
    pts.sort(key=lambda r: r["snr_db"])
    return pts


def main():
    rtl = collect_rtl()
    model = json.loads((DATA / "model_points.json").read_text())["points"]
    model.sort(key=lambda r: r["snr_db"])

    # ---- BER 对比图 ----
    fig, ax = plt.subplots(figsize=(9, 6))
    xs_r = np.array([r["snr_db"] for r in rtl], float)
    bs_r = np.array([max(r["ber"], 5e-7) for r in rtl])
    xs_m = np.array([r["snr_db"] for r in model], float)
    bs_m = np.array([max(r["ber"], 5e-7) for r in model])

    xc = np.linspace(-7, 8, 200)
    ax.semilogy(xc, [qpsk_ber(8 * 10 ** (x / 10)) for x in xc], "--",
                lw=1.2, color="gray",
                label="Coherent QPSK + 9.03 dB spreading (theory ref)")
    ax.semilogy(xs_m, bs_m, "o-", color="tab:blue", lw=1.6, ms=6,
                label="Golden model (float, honest full-search sync)")
    ax.semilogy(xs_r, bs_r, "s-", color="tab:red", lw=1.6, ms=6,
                label="RTL (fixed thresholds, 12-bit ADC)")
    for x, b, r in zip(xs_r, bs_r, rtl):
        ax.annotate(f"{r['ber']:.1e}", (x, b), textcoords="offset points",
                    xytext=(6, -12), fontsize=8, color="tab:red")
    ax.axvspan(-7, 4, color="tab:red", alpha=0.06)
    ax.text(-6.6, 5e-7 * 1.3, "RTL sync collapse region\n(fixed ph_thresh blocked by\nnoise-triggered mislock)",
            fontsize=8.5, color="tab:red")
    ax.set_xlabel("Chip SNR after matched filter (dB)")
    ax.set_ylabel("BER (bit errors / transmitted bits, sync-fail counted whole frame)")
    ax.set_title("IEEE 802.15.4 O-QPSK/DSSS: RTL vs Golden Model BER\n"
                 "(AWGN, 20 B PSDU, 2000 frames/point, 46 sym/frame)")
    ax.grid(True, which="both", alpha=0.25)
    ax.legend(loc="lower left", fontsize=9)
    fig.tight_layout()
    fig.savefig(OUT / "ber_compare.png", dpi=130)

    # ---- 同步健康度图 ----
    fig2, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.2))
    sync_pct = [100 * r["n_frames_synced"] / r["n_frames"] for r in rtl]
    fcs_pct = [100 * r["fcs_ok_ratio"] for r in rtl]
    a1.plot(xs_r, sync_pct, "s-", color="tab:orange", label="frames with frame_start")
    a1.plot(xs_r, fcs_pct, "^-", color="tab:green", label="frames with FCS ok")
    a1.set_xlabel("Chip SNR (dB)")
    a1.set_ylabel("% of frames")
    a1.set_title("RTL frame reception health")
    a1.grid(alpha=0.3)
    a1.legend(fontsize=9)
    det_only = [100 * max(0, r.get("n_detect", 0) - r.get("n_frame_start", 0))
                / r["n_frames"] for r in rtl]
    a2.plot(xs_r, det_only, "x-", color="tab:purple",
            label="DET without frame_start (mislock)")
    a2.set_xlabel("Chip SNR (dB)")
    a2.set_ylabel("% of frames")
    a2.set_title("Noise-triggered mislock rate")
    a2.grid(alpha=0.3)
    a2.legend(fontsize=9)
    fig2.tight_layout()
    fig2.savefig(OUT / "sync_health.png", dpi=130)

    # ---- CSV ----
    with open(OUT / "ber_points.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["source", "snr_db", "n_bit", "n_bit_err", "ber",
                    "n_frames", "n_synced", "fcs_ok_ratio", "clip_frac"])
        for r in model:
            w.writerow(["model", r["snr_db"], r["n_bit"], r["n_bit_err"],
                        f"{r['ber']:.4e}", r["n_frames"], "", "", ""])
        for r in rtl:
            w.writerow(["rtl", r["snr_db"], r["n_bit"], r["n_bit_err"],
                        f"{r['ber']:.4e}", r["n_frames"], r["n_frames_synced"],
                        f"{r['fcs_ok_ratio']:.4f}", f"{r['clip_frac']:.5f}"])

    # ---- 报告 ----
    lines = []
    A = lines.append
    A("# RTL 蒙特卡洛 BER 与浮点模型对比 (Phase 1 出口验证)")
    A("")
    A("> 平台: `tb/rx_chain_e2e/` 文件向量驱动 (`mc_gen.py` / `mc_tb.sv` / `run_mc.py`)")
    A("> 对照: `model/experiments/run_mc_model.py` (浮点链路, 同口径)")
    A("> 口径: 码片 SNR = 匹配滤波输出端信噪比; BER = SHR 之后全部符号 (PHR+PSDU+FCS)")
    A("> 折算比特错误 (同步失败帧按整帧计错, 与 `measure.frame_bit_errors` 一致)")
    A("")
    A("## 1. 结果总表")
    A("")
    A("每点 2000 帧 (20 B PSDU, 46 符号 = 184 bit/帧), 12-bit ADC, 帧到达相位随机。")
    A("")
    A("| 码片 SNR (dB) | RTL BER | 模型 BER | RTL 同步率 | RTL FCS 成功率 |")
    A("|---|---|---|---|---|")
    m_by = {r["snr_db"]: r for r in model}
    for r in rtl:
        m = m_by.get(r["snr_db"])
        mb = f"{m['ber']:.1e}" if m else "-"
        A(f"| {r['snr_db']:+g} | {r['ber']:.2e} | {mb} | "
          f"{100*r['n_frames_synced']/r['n_frames']:.1f}% | "
          f"{100*r['fcs_ok_ratio']:.1f}% |")
    A("")
    A("**模型**: 2 dB 起 BER ≈ 0 (1e-4 工作点约 +1 dB)。")
    A("**RTL (2026-09-28 归一化判据 + 退锁修复后)**: +6 dB BER=3.8e-3 / 检出 99.7%, ")
    A("+2 dB 检出 54.9% (修复前 28.7%)；+10 dB 完美；≤-2 dB 因归一化判据趋严而检出下降")
    A("(那区间链路本不可用, BER≥0.97; 取舍为「宁漏不误」)。")
    A("修复细节与前后对比见 `docs/07_同步器缺陷与修复分析.md` §3。")
    A("")
    A("## 2. RTL 设计发现与修复 (本次平台建设中发现×4, 已修×3)")
    A("")
    A("### 2.1 [已修复] `preamble_sync` 无帧尾退出路径 → 连续帧不可接收")
    A("")
    A("ST_LOCK 状态没有返回 ST_SCAN 的路径, 帧 0 之后无法检测任何新前导。")
    A("修复: 增加 `frame_done` 帧尾反馈输入 (来自 `rx_deframer`) + 两阶段保持超时。")
    A("验证: 3 帧连续流 3/3 → 20 帧随机相位 20/20 全部还原。")
    A("")
    A("### 2.2 [已修复] 扫描器相位覆盖仅 6/16 → 62.5% 帧到达相位不可接收")
    A("")
    A("8 相位候选只覆盖偶片峰 ∈ [0,8) 的半周期。实测 16 个帧到达相位 (mod 16)")
    A("中仅 {0, 11..15} 可收; Python 镜像独立复现同一缺陷 (证明是算法/设计层, 非实现 bug)。")
    A("修复: 候选扩到 16 相位、每拍服务 (偶链候选 q / 奇链候选 (q+4)%16),")
    A("不改变块结构/门限量级。验证: 16/16 相位全部还原 (修复前 6/16)。")
    A("")
    A("### 2.3 [已修复] 低 SNR 误锁: 绝对门限散布 ∝ σ² + 无退锁 (卡死 18 帧)")
    A("")
    A("`ph_thresh=2e11` 的噪声散布 σ_r=8σ_mf² → 裕量随 SNR² 崩缩 (24.6dB 时 423σ,")
    A("2dB 仅 2.3σ); 叠上极值统计 (bestR 历史最大 ×16 候选) → 低 SNR 下误锁必然发生,")
    A("且误锁后卡死 2^18 采样 (≈18 帧) 才恢复。")
    A("修复 (两层): (a) 归一化双判据 —— `r ≥ 0.344·E_prev` (E=块能量) 且**连续 2 块**合格;")
    A("与绝对门限取 AND, 低 SNR 由归一化主导、高 SNR 由绝对门限主导 (自动切换);")
    A("(b) 退锁两阶段超时 —— 锁定→SFD 4096 采样 (真锁最坏 2816 内必出 SFD),")
    A("SFD 后 2^17 兑底；误锁恢复 16.4ms → 0.26ms (待毁帧数 18 → ~1)。")
    A("")
    A("**修复效果 (2000 帧/点)**: +6 dB BER 0.034→0.0038 (检出 98.2%→99.7%), ")
    A("+2 dB 检出 28.7%→54.9%; ≤-2 dB 因判据趋严而检出下降 (该区间链路本不可用, BER≥0.97)。")
    A("")
    A("**误锁统计 (修复后, 本次运行)**: 有 DET 无 frame_start = 锁定后未过 SFD 的次数。")
    A("")
    A("| 码片 SNR | 有 DET 无 frame_start (误锁) | 无任何事件 | 同步成功率 |")
    A("|---|---|---|---|")
    for r in rtl:
        d = r.get("n_detect", 0)
        fs_ = r.get("n_frame_start", 0)
        mis = max(0, d - fs_)
        none = r["n_frames"] - d if d >= fs_ else 0
        A(f"| {r['snr_db']:+g} | {mis} | {max(0, r['n_frames'] - d)} | "
          f"{100*r['n_frames_synced']/r['n_frames']:.1f}% |")
    A("")
    A("## 3. 结论与建议")
    A("")
    A("- **Phase 1 出口判定 (RTL vs 浮点 < 0.5 dB @BER=1e-4): 未通过, 但差距大幅缩小。**")
    A("  已修: 连续帧不可收 (缺陷1) / 相位覆盖 6/16 (缺陷2) / 低 SNR 误锁 (缺陷3+3b, 归一化双判据+退锁)。")
    A("- **剩余差距的两个来源**:")
    A("  1. **CFO 检测振荡 (缺陷4, 未集成)**: 无共轭积使检测相关相位以 2ω 累积,")
    A("     CFO ≥1 kHz 时检出率降到 ~50% (docs/07 §4); 修复 = cfo_corr 前置接入。")
    A("  2. 极低 SNR (≤-2 dB) 的判据取舍——设计选择, 该区间链路本不可用。")
    A("- 修复后本平台可直接复用 (同一网格/口径) 复测出口指标; 误锁的完整机制分析见 docs/07。")
    A("")
    A(f"图: `ber_compare.png` / `sync_health.png` ｜ 数据: `ber_points.csv`")
    (OUT / "report.md").write_text("\n".join(lines))
    print(f"wrote {OUT/'report.md'} / ber_compare.png / sync_health.png / ber_points.csv")


if __name__ == "__main__":
    main()
