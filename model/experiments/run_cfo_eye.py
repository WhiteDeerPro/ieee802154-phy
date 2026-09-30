# -*- coding: utf-8 -*-
"""
run_cfo_eye.py —— CFO 修正的眼图 (refmodel 浮点算法侧, 含 AWGN)
================================================================
对照 tb/cfo_corr 的 RTL 版本: 同一套算法 (前导联合估计 + 整帧消旋),
这里是 **model/ 的浮点黄金模型**实现 —— run_cfo_fix.estimate_cfo / derotate。

四路对比 (都加 AWGN):
    1. 无 CFO 参照
    2. 有 CFO 未修
    3. 有 CFO + refmodel 修正
    4. 有 CFO + RTL 修正   ← 由 tb/cfo_corr/test_eye.py dump, 本脚本读取

眼图对象 = 匹配滤波输出的 I 路 (O-QPSK 偶数码片全在实轴上),
折叠周期 = 偶数码片间隔 = 2*SPS = 16 采样, 最佳采样点落在窗口正中。
叠加 500 条迹线 (run_eye_modulations.py 用 60 条)。

运行: python model/experiments/run_cfo_eye.py  →  out/ref/cfo_eye_model/
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np

import _common
import instance
import phy_802154 as phy
from baseband import channels as ch
from baseband import impairments as imp
import run_cfo_fix as RCF

plt = _common.init()                          # Agg 后端 + 中文字体

FS = phy.SPS * phy.CHIP_RATE
SPS = phy.SPS
PERIOD = 2 * SPS                 # 16 = 偶数码片间隔
N_TRACES = 500
CHIP_SNR_DB = 8.0                # MF 后码片 SNR (802.15.4 口径)
# 之前用 awgn_waveform 的"波形功率 SNR 16 dB"是错的: 半正弦 Σh²=4 白送 6 dB
# MF 增益, 加上波形均值差异, 同样标称值下 O-QPSK 眼图比单载波干净 16 dB。
CFO_HZ = 96e3
PSDU_LEN = 20                    # 20 B -> 51 符号 -> 13056 采样, 够 500 条 × 16
OUT_ROOT = _common.out_dir("ref")       # model/out/ref/（已创建）


def fold(w, first_peak, period, n_traces):
    start = first_peak - period // 2
    segs = [w[start + k * period: start + (k + 1) * period].real
            for k in range(n_traces)
            if start + (k + 1) * period <= len(w)]
    return np.arange(period) / period, np.array(segs)


def main():
    rng = np.random.default_rng(2026)
    psdu = bytes(rng.integers(0, 256, size=PSDU_LEN).tolist())
    syms = phy.ppdu_symbols(psdu)
    chips = phy.symbols_to_chips(syms)
    tx = phy.modulate_oqpsk(chips)

    # ---- 四路的波形 (噪声用同一 rng 种子, 保证"未修/已修"是同一段实现) ----
    wave = {}
    clean = tx
    noisy = phy.add_awgn(tx, CHIP_SNR_DB, rng=np.random.default_rng(7))
    rot = imp.add_cfo(noisy, CFO_HZ, t0=0.0)

    # ---- refmodel 算法: 联合估计 + 整帧消旋 ----
    mf_noisy = phy.matched_filter(noisy)
    mf_rot = phy.matched_filter(rot)
    p_hat, f_hat, q = RCF.estimate_cfo(mf_rot)
    fixed = RCF.derotate(mf_rot, f_hat)
    print(f"  [model] p_hat={p_hat} f_hat={f_hat/1e3:.3f} kHz "
          f"(真值 {CFO_HZ/1e3:.0f} kHz, 一致性 {q:.4f})")

    # 参照也走同一噪声 (否则它 std=0 是假象, 无法与其余各路比)
    wave["ref"] = mf_noisy
    wave["raw"] = mf_rot
    wave["model"] = fixed

    # ---- 若有 RTL dump 则一并读入 ----
    rtl_path = Path(__file__).resolve().parents[2] / "tb" / "cfo_corr" / "cfo_eye.npz"
    has_rtl = rtl_path.exists()
    if has_rtl:
        D = np.load(rtl_path)
        wave["rtl_raw"] = D["i_cfo"] + 1j * D["q_cfo"]
        wave["rtl_fix"] = D["i_fix"] + 1j * D["q_fix"]
        print(f"  [model] 已读入 RTL dump (CFO={D['cfo']/1e3:.0f} kHz)")

    first_peak = phy.chip_peak_index(0, 4)
    # 两套实现的信号量级不同 (模型浮点峰值 ~4, RTL 定点 scale=8 峰值 ~131488),
    # 各自用自己的参照幅度归一, 这样"std/参照"与"眼开比"才能跨实现比较。
    _, seg_ref_m = fold(wave["ref"], first_peak, PERIOD, N_TRACES)
    ref_amp_model = float(np.abs(seg_ref_m[:, PERIOD // 2]).mean()) + 1e-12
    ref_amp_rtl = ref_amp_model
    if has_rtl:
        _, seg_ref_r = fold(D["i_ref"] + 1j * D["q_ref"], first_peak, PERIOD, N_TRACES)
        ref_amp_rtl = float(np.abs(seg_ref_r[:, PERIOD // 2]).mean()) + 1e-12

    # (标签, 波形, 颜色, 该路使用的参照幅度)
    cases = [("无 CFO 参照\n(+AWGN)", wave["ref"], "gray", ref_amp_model),
             (f"有 CFO {CFO_HZ/1e3:.0f} kHz · 未修\n(+AWGN)", wave["raw"], "tab:red",
              ref_amp_model),
             ("有 CFO · refmodel 修正\n(浮点: 联合估计+消旋)", wave["model"], "tab:blue",
              ref_amp_model)]
    if has_rtl:
        cases.append(("有 CFO · RTL 修正\n(cfo_est + cfo_rot)", wave["rtl_fix"],
                      "tab:green", ref_amp_rtl))

    inst = instance.Instance("cfo_eye_model", root=OUT_ROOT,
                             fs=FS, title="CFO 修正眼图: refmodel 算法 vs RTL (含 AWGN)")

    # 画图前把每路除以自己的参照幅度: 模型浮点峰值 ~4, RTL 定点峰值 ~131488,
    # 不归一的话共享 y 轴会把 RTL 那一路整个裁掉 (且四列无法并排比较)。
    fig, axes = plt.subplots(1, len(cases), figsize=(5.6 * len(cases), 5.0),
                             sharey=True)
    stats = []
    for ax, (title, w, col, refa) in zip(axes, cases):
        x, segs = fold(w / refa, first_peak, PERIOD, N_TRACES)   # 已对各自的参照归一
        for s in segs:
            ax.plot(x, s, color=col, lw=0.35, alpha=0.16)
        # segs 已归一化, 所以 mid/edge 本身就是"相对参照"的量, 不要再除 refa
        mid = np.abs(segs[:, PERIOD // 2])
        edge = np.abs(segs[:, 0])
        em = float(edge.mean())
        # 眼开比: 边缘幅度可忽略 (理想眼图的边缘本就是 0) 时记为 inf
        # |min| 也除以参照幅度 —— 两套实现量级差几万倍, 绝对值不可比
        stats.append((float(np.std(mid)), float(mid.min()),
                      float(mid.mean() / em) if em > 0.01 else float("inf")))
        ax.axvline(0.5, color="k", ls=":", lw=0.8, alpha=0.5)
        ax.grid(alpha=0.2)
        ax.set_xlabel("码片周期内相位 (归一化)")
        ax.set_title(f"{title}\nstd/参照 = {stats[-1][0]:.4f}  |  "
                     f"|眼开点|min/参照 = {stats[-1][1]:.3f}", fontsize=9.5)
    axes[0].set_ylabel("匹配滤波输出 I 路幅度 / 参照幅度")

    fig.suptitle(f"CFO 修正的眼图 —— chip SNR {CHIP_SNR_DB:+.0f} dB, 每格 {N_TRACES} 条迹线, "
                 f"O-QPSK 匹配滤波 I 路 (幅度已对各自参照归一化)\n"
                 f"变量: 是否消旋 + 用哪套实现 (refmodel 浮点 / RTL 定点)",
                 fontsize=11.5)
    fig.tight_layout()
    inst.add_figure(fig, "cfo_eye_model.png")

    # ---- 定量表 ----
    nm = [c[0].splitlines()[0] for c in cases]
    _f = lambda v: "∞" if v == float("inf") else f"{v:.1f}"
    rows = []
    for i, n_ in enumerate(nm):
        rows.append(f"**{n_}**: std/参照 = {stats[i][0]:.4f}   "
                    f"|眼开点|min/参照 = {stats[i][1]:.3f}   眼开比 = {_f(stats[i][2])}")
    inst.table("眼图定量指标 (500 条迹线, 均已对各自的参照幅度归一)", rows)

    md, mr, mn, rr = (stats[0][0], stats[1][0], stats[2][0],
                      stats[3][0] if len(stats) > 3 else None)
    inst.table("怎么读", [
        f"**噪声底**: 参照自身的 std/参照 = {md:.4f} —— 这是 16 dB AWGN 打出来的毛边, "
        f"任何一路都不可能比它更干净。",
        f"**未修**: {mr:.4f}, 是噪声底的 {mr/max(md,1e-9):.1f} 倍; 眼开比从参照的 "
        f"{_f(stats[0][2])} 掉到 {_f(stats[1][2])}; 最小软值 {stats[1][1]:.3f} (参照 1.0)"
        f" —— 有些码片被转到判决轴附近, 判决依据基本消失。",
        f"**refmodel 修正**: {mn:.4f}, 回到噪声底的 {mn/max(md,1e-9):.1f} 倍"
        + (f"; **RTL 修正**: {rr:.4f}, 回到噪声底的 {rr/max(md,1e-9):.1f} 倍"
           if rr is not None else "")
        + " —— 两套实现落在同一水平。",
    ])
    inst.note(f"模型侧用 model/experiments/run_cfo_fix.py 的联合估计算法 "
              f"(estimate_cfo 搜对齐点 + 频偏, derotate 整帧消旋), 与 RTL 是同一套算法。"
              f"估计结果 f_hat = {f_hat/1e3:.3f} kHz, 真值 {CFO_HZ/1e3:.0f} kHz, "
              f"误差 {(f_hat-CFO_HZ)/1e3:+.3f} kHz。")
    inst.note(f"加噪口径: MF 后码片 SNR {CHIP_SNR_DB:+.0f} dB (phy.add_awgn), "
              f"眼开点有效 SNR 约 6 dB。眼图上的毛边来自 AWGN, 迹线的系统性"
              f"参差来自 CFO —— 后者才是这一节要看的: 未修时幅度被打成 |cos| 分布, "
              f"有些码片软值趋近 0; 修正后重新聚拢。")
    inst.close()
    print(f"✓ 实例产出: {inst.dir}")
    for p in inst.produced:
        print(f"    {p}")


if __name__ == "__main__":
    main()
