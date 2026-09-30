# -*- coding: utf-8 -*-
"""
run_eye_modulations.py —— 多调制方式眼图对比
==============================================
变量 = **调制方式**。每种调制给出三种信道条件下的接收眼图:

    列: 理想  /  +AWGN  /  +AWGN + CFO (载波频偏 → 星座旋转)

被比较的调制:
    BPSK / QPSK / 4-ASK / 16-QAM —— 通用单载波链 (升余弦成形 + 匹配滤波)
    O-QPSK —— 802.15.4 制式链 (DSSS 扩频 + 半正弦成形), 作对照

怎么读这张图:
    · 眼开点数目 = **每符号携带的电平数** (BPSK 2 / 4-ASK 4 / 16-QAM 4 且双路)
      —— 这正是「一符号几比特」在时域波形上的直接体现;
    · 加噪后眼开处的「毛边」= 噪声余量, 电平越多间距越密、越先糊掉
      (16-QAM 内层最先闭合);
    · 加旋后各条迹线的**幅度参差** = 残余载波相位漂移 —— 眼图叠加窗跨越整段波形,
      相位一转, 每窗的 I 分量幅度就不同。

眼图对象 = **接收匹配滤波输出的实分量 (I 路)**。复数调制 (QPSK/QAM) 的 Q 路与之
对称, 不重复画。横轴按各自的符号周期归一化, 使不同调制可直接并排比较。

参数取舍: 各调制的帧长/符号率相差可达 32 倍 (扩频 vs 无扩频), 若用**固定赫兹数**
的 CFO, “旋转程度”将不可比 —— 故 CFO 按整段眼图跨度的**统一相位漂移**反推。

运行: python model/experiments/run_eye_modulations.py
输出: model/out/vis/vis_eye_modulations.png
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

import _common
import phy_802154 as phy
import visualize
from baseband import channels as ch, impairments as imp, modulation as mod

plt = _common.init()                       # Agg 后端 + 中文字体
OUT = _common.out_dir("vis")               # model/out/vis/（已创建）

FS = 16e6
SPS = 8                       # 每符号采样数 (与 802.15.4 主时钟一致)
N_SYM = 200                   # 通用链符号数
N_TRACES = 60                 # 眼图叠加迹线数
SNR_DB = 16.0                 # 波形 SNR (加噪列)
PHASE_DRIFT = 0.7             # 整段眼图跨度内的 CFO 相位漂移 (rad), 各调制统一

SPECS = [                     # (标签, 星座类型, 阶数) —— None 表示 O-QPSK 制式链
    ("BPSK", "psk", 2),
    ("QPSK", "psk", 4),
    ("4-ASK", "ask", 4),
    ("16-QAM", "qam", 16),
    ("O-QPSK", None, None),
]


def cfo_for_drift(span_samples, drift=PHASE_DRIFT, fs=FS):
    """反推使整段眼图跨度产生 ``drift`` 弧度相位漂移的 CFO (Hz)。

    统一相位漂移而非统一赫兹: 各调制帧长差 32 倍, 固定 Hz 下“旋转程度”不可比。
    """
    return drift / (2 * np.pi * (span_samples / fs))


def fold_eye(w, first_peak, period, n_traces=N_TRACES):
    """把接收波形按 ``period`` 折叠成眼图迹线 (取 I 路)。

    窗口自「峰值前 period//2」起 -> 最佳采样点落在窗口正中 (x = 0.5)。
    返回 ``(x 归一化轴, 迹线矩阵 (n_traces, period))``。
    """
    start = first_peak - period // 2
    segs = [w[start + k * period: start + (k + 1) * period].real
            for k in range(n_traces)
            if start + (k + 1) * period <= len(w)]
    return np.arange(period) / period, np.array(segs)


def build_row(spec, rng):
    """构建一行: 返回 (标签, 注解, [(列标题, x, 迹线), ...])。"""
    label, scheme, order = spec

    if scheme is None:
        # ---- 802.15.4 制式链: 组帧 → 扩频 → O-QPSK 半正弦 ----
        psdu = bytes(rng.integers(0, 256, size=20).tolist())
        syms = phy.ppdu_symbols(psdu)
        tx = phy.modulate_oqpsk(phy.symbols_to_chips(syms))
        pulse = phy.half_sine(SPS)
        mf = lambda w: phy.matched_filter(w)                    # noqa: E731
        # I 路取偶数码片; chip_peak_index 已含匹配滤波延迟
        first_peak = phy.chip_peak_index(0, 10 * 32)            # 跳过前导8+SFD2
        period = 2 * SPS                                        # 偶数码片间隔
        note = "DSSS 32chip/sym · 半正弦 2 电平(I) · 扩频增益 9 dB"
    else:
        # ---- 通用单载波链: 星座映射 → 升余弦成形 ----
        const = mod.constellation(scheme, order)
        symbols = mod.map_symbols(rng.integers(0, order, size=N_SYM), const)
        pulse = mod.raised_cosine(beta=0.35, sps=SPS)
        tx, _ = mod.pulse_shape(symbols, pulse, SPS)
        # 成形 + 匹配滤波两次卷积, 首个符号峰值落在 len(pulse)-1
        mf = lambda w: mod.matched_filter(w, pulse)             # noqa: E731
        first_peak = len(pulse) - 1
        period = SPS
        levels_i = len(np.unique(np.round(const.real, 9)))
        note = (f"{const.size}-point 星座 · 升余弦 β=0.35 · "
                f"I 路 {levels_i} 电平" +
                (" (Q 路同)" if np.any(const.imag) else " (纯实轴)"))

    span = N_TRACES * period
    cfo_hz = cfo_for_drift(span)
    # 眼图起点 (第一个叠加窗) —— 让相位漂移从第一个窗口起算, 各调制才可比:
    # 否则相同的漂移会落在不同的 cos 区间上, 幅度损失相差数倍。
    eye_start = first_peak - period // 2
    # 匹配滤波峰值增益 = Σh² —— 半正弦是 4 (sps/2), 升余弦单位能量归一为 1。
    # 不归一的话两种链的眼图幅度差 4 倍, 没法并排比较。
    peak_gain = float(np.sum(pulse ** 2))
    noise = lambda w, seed: ch.awgn_waveform(w, SNR_DB, np.random.default_rng(seed))  # noqa: E731
    rot = lambda w: imp.add_cfo(w, cfo_hz, t0=eye_start)        # noqa: E731

    cases = [
        ("1. 理想 (无损伤)", tx),
        (f"2. +AWGN {SNR_DB:.0f} dB", noise(tx, 2026)),
        (f"3. +AWGN +CFO {cfo_hz:.0f} Hz\n(整段相位漂移 {PHASE_DRIFT:.1f} rad)",
         rot(noise(tx, 2026))),
    ]
    # 每个叠加窗对应的**理想 I 采样值** (眼开点处) —— 用于算 EVM 式误差。
    # 窗口 k 的峰值在 first_peak + k*period, 对应第 k 个符号 / 第 (DATA0+2k) 个码片。
    if scheme is None:
        ideal_i = phy.symbols_to_chips(syms)[10 * 32::2][:N_TRACES].real
    else:
        ideal_i = symbols[:N_TRACES].real

    panels = []
    for title, w in cases:
        x, traces = fold_eye(mf(w) / peak_gain, first_peak, period)
        panels.append((title, x, traces))
    return label, note, panels, ideal_i[:len(panels[0][2])]


def main():
    visualize.use_cjk_font()
    rng = np.random.default_rng(2026)

    rows = [build_row(s, rng) for s in SPECS]
    ncol = len(rows[0][2])
    fig, axs = plt.subplots(len(rows), ncol,
                            figsize=(4.0 * ncol, 2.5 * len(rows)),
                            sharex="col", squeeze=False)

    for i, (label, note, panels, _ideal) in enumerate(rows):
        for j, (title, x, traces) in enumerate(panels):
            ax = axs[i, j]
            for tr in traces:
                ax.plot(x, tr, color="tab:blue", lw=0.5, alpha=0.4)
            ax.axvline(0.5, color="tab:red", ls="--", lw=0.9, alpha=0.8)
            ax.grid(alpha=0.25)
            ax.set_ylim(-1.7, 1.7)
            if i == 0:
                ax.set_title(title, fontsize=10)
            if j == 0:
                ax.set_ylabel(f"{label}\nI amplitude", fontsize=10)
        axs[i, 0].text(0.02, 0.03, note, transform=axs[i, 0].transAxes,
                       ha="left", va="bottom", fontsize=7, color="dimgray",
                       bbox=dict(fc="white", ec="none", alpha=0.8, pad=1.5))

    for j in range(ncol):
        axs[-1, j].set_xlabel("符号周期 (红虚线 = 最佳采样点)", fontsize=9)

    fig.suptitle("眼图 vs 调制方式 —— 变量: 调制方法 | 损伤: AWGN 与载波相位旋转\n"
                 "每行一种调制, 每列一种信道条件; 眼开点数 = 该调制每符号携带的电平数",
                 fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.945))
    path = OUT / "vis_eye_modulations.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    print(f"✓ {path.name}")

    # 终端摘要: 眼开点的 EVM 式误差 = 接收采样值相对理想值的偏差。
    # (不能直接看跨窗口的 |I| 标准差 —— 多电平调制自身的电平离散会被误当成损伤:
    #  4-ASK 的 |I| 标准差天然就是 0.45 量级, 与噪声无关。)
    print(f"\n{'调制':<10} {'理想 RMS':>10} {'受损 RMS':>10} {'RMS 误差':>10} {'相对误差':>10}")
    for label, _, panels, ideal_i in rows:
        i_opt = panels[0][2].shape[1] // 2
        ref = panels[0][2][:, i_opt]
        errs = np.concatenate([t[:, i_opt] - ideal_i for _, _, t in panels[1:]])
        ideal_rms = float(np.sqrt(np.mean(ideal_i ** 2)))
        err_rms = float(np.sqrt(np.mean(errs ** 2)))
        print(f"{label:<10} {ideal_rms:>10.3f} {np.sqrt(np.mean(ref**2)):>10.3f} "
              f"{err_rms:>10.3f} {err_rms / ideal_rms:>9.1%}")
    print("\n相对误差 = RMS 误差 / 理想 RMS —— 含 AWGN 与残余相位旋转两项贡献。")


if __name__ == "__main__":
    main()
