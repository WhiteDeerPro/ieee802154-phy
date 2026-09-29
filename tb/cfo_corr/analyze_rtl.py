"""从 RTL dump 出图 —— cfo_est/cfo_rot 在真实仿真里跑出来的数据。

用法: .venv/bin/python tb/cfo_corr/analyze_rtl.py   (在仓库根跑)
产物: model/out/rtl_cfo/
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tb" / "cfo_corr"))

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import instance
import phy_802154 as phy

SPS = phy.SPS
D = np.load(ROOT / "tb" / "cfo_corr" / "rtl_cfo_dump.npz")
cfo = D["cfo"] / 1e3
x = [max(v, 2.0) for v in cfo]          # 对数轴: 0 画在 2 处


def chips(mfi, mfq, align, n_syms):
    m = np.arange(n_syms * 32)
    idx = np.clip(align + m * SPS + np.where(m % 2, SPS // 2, 0) + (SPS - 1), 0, len(mfi) - 1)
    v = mfi[idx] + 1j * mfq[idx]
    return np.where(m % 2 == 0, v, v * (-1j))


def main():
    inst = instance.Instance("rtl_cfo", fs=SPS * phy.CHIP_RATE,
                             title="RTL 实践: cfo_est + cfo_rot 在 VCS 仿真里的实测结果")

    # ---- 图 1: 估计精度 ----
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12.5, 4.6))
    a1.plot(x, [1.0] * len(x), "k--", lw=1, label="理想 y=x")
    ratio = [1.0 if c == 0 else (c + e / 1e3) / c for c, e in zip(cfo, D["est_err"])]
    a1.plot(x, ratio, "o-",
            lw=1.7, color="tab:blue", label="RTL 估计 / 真值")
    a1.set_xscale("log"); a1.set_xlim(1.5, 700); a1.set_ylim(0.99, 1.01)
    a1.set_xlabel("CFO 真值 (kHz)"); a1.set_ylabel("估计 / 真值")
    a1.grid(alpha=0.3, which="both"); a1.legend(fontsize=9)
    a1.set_title("无模糊性: 全程贴合 1.0", fontsize=10)
    a2.semilogy(x, np.abs(D["est_err"]), "s-", lw=1.7, color="tab:red")
    a2.axhline(62.5e3, color="tab:green", ls="--", lw=1.2)
    a2.text(2.5, 7e4, "非相干判决容限 62.5 kHz", fontsize=8, color="tab:green")
    a2.set_xscale("log"); a2.set_xlim(1.5, 700)
    a2.set_xlabel("CFO 真值 (kHz)"); a2.set_ylabel("|估计误差| (Hz)")
    a2.grid(alpha=0.3, which="both")
    a2.set_title("估计误差 < 11 Hz (比容限好 4 个数量级)", fontsize=10)
    inst.add_figure(fig, "rtl_cfo_estimate.png")

    # ---- 图 2: BER ----
    fig2, ax2 = plt.subplots(figsize=(9.5, 5.2))
    ax2.semilogy(x, np.maximum(D["ber_raw"], 1e-10), "o-", lw=1.8,
                 color="tab:red", label="修前 (RTL 消旋旁路)")
    ax2.semilogy(x, np.maximum(D["ber_fix"], 1e-10), "s-", lw=1.8,
                 color="tab:green", label="修后 (cfo_est + cfo_rot)")
    ax2.axvline(96, color="r", ls=":", lw=1.4)
    ax2.text(100, 2e-1, "晶振 ±40 ppm\n= 96 kHz", fontsize=8.5, color="r")
    ax2.set_xscale("log"); ax2.set_xlim(1.5, 700); ax2.set_ylim(1e-10, 1.5)
    ax2.set_xlabel("CFO (kHz, 对数轴; 最低点 2 处实为 0)")
    ax2.set_ylabel("BER")
    ax2.grid(True, which="both", alpha=0.3); ax2.legend(fontsize=9.5)
    ax2.set_title("RTL 闭环: 450 kHz 内 BER 全部回到零错误\n"
                  "(无噪条件, 隔离 RTL 本身的正确性)")
    inst.add_figure(fig2, "rtl_cfo_ber.png")

    # ---- 图 3: 码片软值相位 (消旋前/后) ----
    align = int(D["align"])
    ns = len(D["syms"])
    ch_r = chips(D["i_raw"], D["q_raw"], align, ns)
    ch_f = chips(D["i_fix"], D["q_fix"], align, ns)
    ang_r = np.degrees(np.unwrap(np.angle(ch_r)))
    ang_f = np.degrees(np.unwrap(np.angle(ch_f)))
    # 去调制: 减去理想码片的相位 (前导段全为 CHIP[0])
    fig3, (b1, b2) = plt.subplots(1, 2, figsize=(13, 4.6))
    b1.plot(ang_r[:256], lw=1.2, color="tab:red", label="修前")
    b1.plot(ang_f[:256], lw=1.2, color="tab:green", alpha=0.85, label="修后")
    b1.set_xlabel("码片序号 (前导段 256 片)"); b1.set_ylabel("软值相位 (度, 已 unwrap)")
    b1.grid(alpha=0.3); b1.legend(fontsize=9)
    b1.set_title("CFO 96 kHz: 修前相位线性漂移 2500 度, 修后被拉平", fontsize=10)
    w = slice(0, 256)
    b2.scatter(ch_r[w].real, ch_r[w].imag, s=12, alpha=0.5, color="tab:red",
               label="修前")
    b2.scatter(ch_f[w].real, ch_f[w].imag, s=12, alpha=0.5, color="tab:green",
               label="修后")
    b2.set_aspect("equal"); b2.grid(alpha=0.3); b2.legend(fontsize=9)
    b2.set_xlabel("I"); b2.set_ylabel("Q")
    b2.set_title("前导码片软值分布: 修后重新聚到实轴 (|.| 检测的判决依据)", fontsize=10)
    inst.add_figure(fig3, "rtl_cfo_phase.png")

    # ---- 图 4: 相关峰 ----
    fig4, ax4 = plt.subplots(figsize=(9.5, 4.7))
    base = D["pk_raw"][0]
    ax4.semilogy(x, D["pk_raw"] / base, "o-", lw=1.7, color="tab:red", label="修前")
    ax4.semilogy(x, D["pk_fix"] / base, "s-", lw=1.7, color="tab:green", label="修后")
    ax4.axhline(1.0, color="gray", lw=0.8, ls="--")
    ax4.set_xscale("log")
    ax4.set_xlabel("CFO (kHz)"); ax4.set_ylabel("前导相关峰 (归一化)")
    ax4.grid(True, which="both", alpha=0.3); ax4.legend(fontsize=9.5)
    ax4.set_title("RTL 消旋把同步环也救回来: 相关峰恢复到无 CFO 水平")
    inst.add_figure(fig4, "rtl_cfo_corr.png")

    rows = ["{:>8}  {:>8}  {:>12}  {:>11}  {:>11}".format(
        "CFO(k)", "p_hat", "f_est(k)", "修前BER", "修后BER")]
    for i in range(len(cfo)):
        f_est = (cfo[i] + D["est_err"][i] / 1e3)
        rows.append("{:>8.0f}  {:>8d}  {:>12.3f}  {:>11.2e}  {:>11.2e}".format(
            cfo[i], D["p_hat"][i], f_est, D["ber_raw"][i], D["ber_fix"][i]))
    inst.table("RTL 实测 (VCS 仿真)", rows)
    inst.table("RTL 实现要点", [
        "**cfo_est**: 8 个相位候选并行做差分相关, 用 |acc|^2 当相位一致性判据选最佳; "
        "对齐点与频偏一次搜完, 不依赖任何上游同步。",
        "**cordic_atan2**: 16 级向量模式 CORDIC 求 arg(acc), 只用移位和加减, 无乘法器; "
        "内置象限预处理把输入翻到右半平面 (否则 ±99.9 度收敛域装不下 ±180 度的差分相位)。",
        "**cfo_rot**: 24 位相位累加器 + 256 点 cos/sin 查找表 + 一次复乘 (4 实数乘)/采样。",
        "**位宽**: 输入 21bit (与 rx_matched_filter 同宽); z 右移 16 保 21bit; "
        "d 右移 6; acc 48bit; CORDIC 输入取 acc>>12。",
    ])
    inst.table("踩过的五个坑 (都已修)", [
        "**ROM 索引必须用码片序号**: 候选的偶链收的是 m=0,2,4,... 要用 2*mc_e 索引, "
        "不是\"第几片\"。",
        "**偶/奇链计数器必须分开**: 同一候选既收偶数码片又收奇数码片, 共用一个计数器互相踩。",
        "**跳过 t=3 槽位**: 候选 7 的奇数码片序列是 t=3,19,35,... 而 off[2j+1]=16j+19 "
        "最小是 19 —— t=3 是\"码片 -1\", 收进来整条奇链错位一片。",
        "**CORDIC 内部位宽要留够**: 原本余量 4 位, 而 |acc| 达 2^32 且迭代中涨 1.65 倍, "
        "x 回绕后 y 的符号判据失效, z 一路乱加 (实测 18 度被算成 68.6 度)。",
        "**CORDIC 要做象限预处理**: 每级累计旋转上限 99.9 度, 直接喂第二/三象限会转不过去; "
        "300 kHz CFO (差分相位 108 度) 曾被折叠成 277 kHz。",
    ])
    inst.note("RTL 的估计误差 (< 11 Hz) 比浮点模型 (1σ 8.7 kHz) 还好 —— 因为模型里那 8.7 kHz "
              "是噪声引起的统计误差, 而这里的 RTL 实验用无噪条件隔离了实现本身的正确性。"
              "有噪条件下 RTL 给出 0.03~0.05 kHz 偏差 (见 test_cfo_corr.est_clean_and_noisy)。")
    inst.close()
    print(f"✓ 实例产出: {inst.dir}")
    for p in inst.produced:
        print(f"    {p}")


if __name__ == "__main__":
    main()
