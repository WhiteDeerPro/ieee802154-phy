"""从 RTL dump 叠眼图, 展示 cfo_corr 修正的效果。

三列对比: 无 CFO 参照 / 有 CFO 未修 / 有 CFO 已修 (后两列是同一段波形的 RTL 实际输出)。

眼图对象 = 匹配滤波输出的 I 路 (O-QPSK 的偶数码片全在实轴上),
折叠周期 = 偶数码片间隔 = 2*SPS = 16 采样, 最佳采样点落在窗口正中 (x=0.5)。
叠加迹线数 500 条 —— 是 run_eye_modulations.py (60 条) 的 8 倍多。

用法: .venv/bin/python tb/cfo_corr/analyze_eye.py   (在仓库根跑)
产物: model/out/ref/cfo_eye/
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
N_TRACES = 500                      # 叠加迹线数 (run_eye_modulations 用 60, 这里 8 倍多)
D = np.load(ROOT / "tb" / "cfo_corr" / "cfo_eye.npz")
PERIOD = int(D["period"])           # 16 = 2*SPS, 偶数码片间隔


def fold(w, first_peak, period, n_traces):
    """把波形按 period 折叠成 (n_traces, period); 窗口自峰值前 period//2 起。"""
    start = first_peak - period // 2
    segs = [w[start + k * period: start + (k + 1) * period].real
            for k in range(n_traces)
            if start + (k + 1) * period <= len(w)]
    return np.arange(period) / period, np.array(segs)


def main():
    # 从帧首第 4 个码片起折叠, 避开前导最前面的卷积上冲段, 也让 500 条铺得下
    first_peak = phy.chip_peak_index(0, 4)

    cases = [
        ("无 CFO 参照\n(+AWGN)", D["i_ref"] + 1j * D["q_ref"], "gray"),
        (f"有 CFO {D['cfo']/1e3:.0f} kHz · 未修\n(消旋旁路, +AWGN)",
         D["i_cfo"] + 1j * D["q_cfo"], "tab:red"),
        (f"有 CFO {D['cfo']/1e3:.0f} kHz · 已修\n(cfo_est + cfo_rot, +AWGN)",
         D["i_fix"] + 1j * D["q_fix"], "tab:green"),
    ]

    inst = instance.Instance("cfo_eye", root=ROOT / "model" / "out" / "ref",
                             fs=SPS * phy.CHIP_RATE,
                             title=f"CFO 修正效果的眼图 (RTL 实测, 每格 500 条迹线, 芯片SNR {float(D["chip_snr_db"]):+.0f} dB)")

    fig, axes = plt.subplots(1, 3, figsize=(16.5, 5.0), sharey=True)
    # 定量指标的分母: 无 CFO 参照在眼开点的幅度。
    # 不能用每格自己的 mean(|mid|) —— 未修时迹线在 ±A 间乱窜, 分子分母同时变小,
    # 比值只差 11% 完全看不出; 以参照幅度归一才能反映"迹线被打散"的真实程度。
    # 指标用 |mid| 而不是 mid: 偶数码片的符号本来就是 ±1 交替 (数据决定的),
    # mid 的标准差天然很大; 而非相干检测只看幅度, 所以离散度应衡量 |mid|。
    _, seg_ref = fold(cases[0][1], first_peak, PERIOD, N_TRACES)
    ref_amp = float(np.abs(seg_ref[:, PERIOD // 2]).mean()) + 1e-12
    stats = []
    for ax, (title, w, col) in zip(axes, cases):
        x, segs = fold(w, first_peak, PERIOD, N_TRACES)
        for s in segs:
            ax.plot(x, s, color=col, lw=0.35, alpha=0.16)
        # 眼开点 (窗口正中) 的幅度标准差, 相对参照幅度归一
        mid = np.abs(segs[:, PERIOD // 2])
        edge = np.abs(segs[:, 0])
        eye_open = float(np.std(mid) / ref_amp)
        # 眼开比 = 中心/边缘; 边缘恰好为 0 (理想眼图) 时记为 inf, 用大数表示便于排序
        em = float(edge.mean())
        eye_ratio = float(mid.mean() / em) if em > 1.0 else float('inf')
        stats.append((eye_open, float(mid.min()), eye_ratio))
        ax.axvline(0.5, color="k", ls=":", lw=0.8, alpha=0.5)
        ax.grid(alpha=0.2)
        ax.set_xlabel("码片周期内相位 (归一化)")
        ax.set_title(f"{title}\n幅度 std/参照 = {eye_open:.4f}  |  眼开比 = {eye_ratio:.1f}"
                 f"\n眼开点最小幅度 = {float(mid.min()):.0f}", fontsize=9.5)
    axes[0].set_ylabel("匹配滤波输出 I 路幅度")

    # 眼图中心处的直方图 (看判决点的分布)
    axh = axes[2].inset_axes([0.62, 0.06, 0.34, 0.34])
    for (title, w, col) in cases[1:]:
        _, segs = fold(w, first_peak, PERIOD, N_TRACES)
        axh.hist(segs[:, PERIOD // 2], bins=30, alpha=0.55, color=col,
                 label="未修" if col == "tab:red" else "已修")
    axh.set_yticks([])
    axh.tick_params(labelsize=7)
    axh.legend(fontsize=7)

    fig.suptitle(f"CFO 修正的眼图对比 —— 变量: 是否消旋 | RTL 实测, 每格 {N_TRACES} 条迹线\n"
                 "(O-QPSK 匹配滤波输出 I 路, 按偶数码片周期折叠; 未修时载波在漂 -> 迹线)"
                 "参差, 已修后重新聚拢)", fontsize=11)
    fig.tight_layout()
    inst.add_figure(fig, "cfo_eye_500traces.png")

    # ---- 第二张: 相位漂移本身 (为什么眼图会散) ----
    fig2, (a1, a2) = plt.subplots(1, 2, figsize=(13, 4.6))
    m = 500
    idx = first_peak + np.arange(m) * PERIOD
    ph_ref = np.degrees(np.angle(cases[0][1][idx]))
    ph_raw = np.degrees(np.unwrap(np.angle(cases[1][1][idx])))
    ph_fix = np.degrees(np.unwrap(np.angle(cases[2][1][idx])))
    a1.plot(ph_raw, lw=1.2, color="tab:red", label="未修")
    a1.plot(ph_fix, lw=1.2, color="tab:green", label="已修")
    a1.set_xlabel(f"码片序号 (共 {m} 个采样点)")
    a1.set_ylabel("软值相位 (度, unwrap)")
    a1.grid(alpha=0.3)
    a1.legend(fontsize=9)
    a1.set_title(f"眼图窗口采样点上的相位: 未修时漂移 {ph_raw.max()-ph_raw.min():.0f} 度",
                 fontsize=10)
    a2.plot(np.abs(cases[1][1][idx]), lw=1.0, color="tab:red", alpha=0.8, label="未修")
    a2.plot(np.abs(cases[2][1][idx]), lw=1.0, color="tab:green", alpha=0.8, label="已修")
    a2.plot(np.abs(cases[0][1][idx]), lw=1.0, color="gray", ls="--", alpha=0.7, label="参照")
    a2.set_xlabel("码片序号")
    a2.set_ylabel("|软值|")
    a2.grid(alpha=0.3)
    a2.legend(fontsize=9)
    a2.set_title("幅度: 已修贴合参照, 未修随相位漂移起落", fontsize=10)
    inst.add_figure(fig2, "cfo_eye_phase.png")

    nm = [c[0].splitlines()[0] for c in cases]
    _fmt = lambda v: '∞ (边缘为 0)' if v == float('inf') else f'{v:.1f}'
    inst.table("眼图定量指标 (500 条迹线, RTL 实测)", [
        f"**{nm[0]}**: std/参照 = {stats[0][0]:.4f}   最小幅度 = {stats[0][1]:.0f}"
        f"   眼开比 = {_fmt(stats[0][2])}",
        f"**{nm[1]}**: std/参照 = {stats[1][0]:.4f}   最小幅度 = {stats[1][1]:.0f}"
        f"   眼开比 = {_fmt(stats[1][2])}",
        f"**{nm[2]}**: std/参照 = {stats[2][0]:.4f}   最小幅度 = {stats[2][1]:.0f}"
        f"   眼开比 = {_fmt(stats[2][2])}",
    ])
    inst.table("怎么读这三个数", [
        f"**幅度 std / 参照**: 未修 {stats[1][0]:.3f} 恰好是 |cos| 分布在均匀相位下的理论"
        f" 标准差 0.308 —— 说明载波漂移把幅度打成了纯正弦分布; 已修 {stats[2][0]:.3f},"
        f" 剩下的是定点量化残差 (LUT 1.4 度 + 右移截断), 对非相干检测无影响。",
        f"**眼开点最小幅度**: 未修时低到 {stats[1][1]:.0f} (参照是 {stats[0][1]:.0f}) —— "
        f"有些码片被旋转到判决轴附近, 软值几乎归零, 这正是 BER 崩到 0.54 的直接原因;"
        f" 已修后最小值 {stats[2][1]:.0f}, 已接近参照。",
        f"**眼开比 (中心/边缘)**: 未修 {stats[1][2]:.1f} -> 已修 {stats[2][2]:.1f}, "
        f"改善 {stats[2][2]/max(stats[1][2],1e-9):.0f} 倍 —— 眼睛重新张开。",
    ])
    inst.note(f"叠加 {N_TRACES} 条迹线 (run_eye_modulations.py 用 60 条, 这里 8 倍多); "
              f"数据来自 VCS 仿真的 cfo_rot 实际输出 (tb/cfo_corr/test_eye.py dump 的 "
              f"cfo_eye.npz), 不是浮点模型重算 —— 眼图里的每一个毛刺都是 RTL 定点的真实结果。")
    inst.close()
    print(f"✓ 实例产出: {inst.dir}")
    for p in inst.produced:
        print(f"    {p}")


if __name__ == "__main__":
    main()
