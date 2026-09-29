# -*- coding: utf-8 -*-
"""
run_filter_study.py —— 窗函数与 FIR 滤波器设计（窗函数法）
==============================================================
回答「窗函数和滤波器是什么关系」: **窗函数是设计 FIR 的手段**。

理想低通的冲激响应是 sinc —— 无限长、非因果, 不可实现。截断到 N 点等价于在
时域乘一个矩形窗, 而矩形窗的频谱是 sinc 形, 卷积后就产生吉布斯现象 (通带波纹
+ 高旁瓣)。换用平滑的窗能让截断边缘平缓过去: 旁瓣压低, 代价是过渡带变宽。

本实验产出:
  window_compare.png   各窗的时域形状 + 自身频谱 (旁瓣对照教科书指标)
  fir_compare.png      同参数下各窗设计的低通: 频响与阻带/过渡带权衡
  kaiser_sweep.png     Kaiser β 连续调节这个权衡
  fir_applied.png      把设计出的低通真正用起来 (低通滤波前后频谱)

运行: python model/experiments/run_filter_study.py  →  out/filter_study/
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
from baseband import channels as ch, filters as flt

FS = 16e6
N_TAPS = 65
CUTOFF = 0.25                 # 归一化 (fs/4)
SEED = 2026


def draw_windows(inst):
    """各窗的时域 + 自身频谱 (标出实测旁瓣)。"""
    names = list(flt.WINDOW_SPECS)
    fig, axs = plt.subplots(2, len(names), figsize=(4.0 * len(names), 6.4))
    for j, name in enumerate(names):
        w = flt.window(name, 64)
        axs[0, j].plot(w, lw=1.2, color="tab:blue")
        axs[0, j].set_title(f"{name}\n{flt.WINDOW_SPECS[name]['desc']}", fontsize=9)
        axs[0, j].grid(alpha=0.3)
        if j == 0:
            axs[0, j].set_ylabel("窗系数")
        W = np.abs(np.fft.fft(w, 8192))
        W /= W.max()
        db = 20 * np.log10(W + 1e-30)
        k = 2048                                   # 只看前 1/4 谱, 便于看旁瓣
        axs[1, j].plot(np.arange(k) / 8192, db[:k], lw=0.9, color="tab:red")
        sl = flt.window_sidelobe_db(name)
        axs[1, j].axhline(sl, ls="--", lw=0.9, color="k", alpha=0.6)
        axs[1, j].text(0.03, sl + 3, f"实测旁瓣 {sl:.1f} dB\n(经典 "
                      f"{flt.WINDOW_SPECS[name]['peak_sidelobe_db']} dB)",
                      fontsize=8, color="k")
        axs[1, j].set_ylim(-100, 5)
        axs[1, j].set_xlabel("归一化频率")
        axs[1, j].grid(alpha=0.3)
        if j == 0:
            axs[1, j].set_ylabel("|W| (dB)")
    fig.suptitle("窗函数: 时域形状 (上) 与自身频谱 (下) —— 旁瓣实测与教科书指标对照",
                 fontsize=13)
    inst.add_figure(fig, "window_compare.png")
    rows = [f"{n}: 经典旁瓣 {flt.WINDOW_SPECS[n]['peak_sidelobe_db']} dB, "
            f"实测 {flt.window_sidelobe_db(n):.1f} dB" for n in names]
    inst.table("窗函数自身旁瓣 (实测 vs 经典)", rows)


def draw_fir_compare(inst):
    """同参数下各窗设计的低通: 频响 + 权衡散点。"""
    names = list(flt.WINDOW_SPECS)
    fig, axs = plt.subplots(1, 2, figsize=(13, 4.6))
    rows = []
    for name in names:
        h = flt.fir_lowpass(CUTOFF, N_TAPS, win=name)
        f, db, _, _ = flt.freq_response(h, 8192)
        axs[0].plot(f, db, lw=1.0, label=name)
        bw3 = flt.occupied_bandwidth(h, fs=1.0) / 2
        stop = flt.stopband_db(h)
        f40 = f[np.where(db < -40)[0][0]] if (db < -40).any() else np.nan
        rows.append((name, bw3, f40 - bw3, stop))
        axs[1].scatter(f40 - bw3, stop, s=60, label=name)
        axs[1].annotate(name, (f40 - bw3, stop), xytext=(5, 4),
                        textcoords="offset points", fontsize=9)
    axs[0].axvline(CUTOFF, color="gray", ls=":", lw=0.9)
    axs[0].set_xlim(0, 0.45)
    axs[0].set_ylim(-100, 5)
    axs[0].set_xlabel("归一化频率")
    axs[0].set_ylabel("|H| (dB)")
    axs[0].set_title(f"窗函数法低通 FIR 频响 (N={N_TAPS}, cutoff={CUTOFF})")
    axs[0].grid(alpha=0.3)
    axs[0].legend(fontsize=8)
    axs[1].set_xlabel("过渡带宽度 (归一化)")
    axs[1].set_ylabel("阻带最高电平 (dB)")
    axs[1].set_title("经典权衡: 阻带越低 ↔ 过渡带越宽")
    axs[1].grid(alpha=0.3)
    inst.add_figure(fig, "fir_compare.png")
    inst.table("窗函数法低通 FIR (N=65, cutoff=0.25)",
               [f"{n}: -3dB 频率 {b:.4f}, 过渡带 {t:.4f}, 阻带 {s:.1f} dB"
                for n, b, t, s in rows])


def draw_kaiser(inst):
    """Kaiser β 连续调节过渡带/旁瓣的取舍。"""
    betas = [0, 2, 4, 6, 8, 10]
    fig, axs = plt.subplots(1, 2, figsize=(13, 4.6))
    rows = []
    for b in betas:
        h = flt.fir_lowpass(CUTOFF, N_TAPS, win=("kaiser", b))
        f, db, _, _ = flt.freq_response(h, 8192)
        axs[0].plot(f, db, lw=1.0, label=f"β={b}")
        bw3 = flt.occupied_bandwidth(h, fs=1.0) / 2
        stop = flt.stopband_db(h)
        f40 = f[np.where(db < -40)[0][0]] if (db < -40).any() else np.nan
        axs[1].plot(b, stop, "o-", label="阻带 (dB)")
        axs[1].annotate(f"{stop:.0f}", (b, stop), xytext=(4, 4),
                        textcoords="offset points", fontsize=8)
        rows.append((b, stop, f40 - bw3))
    axs[0].set_xlim(0, 0.45)
    axs[0].set_ylim(-110, 5)
    axs[0].set_xlabel("归一化频率")
    axs[0].set_ylabel("|H| (dB)")
    axs[0].set_title("Kaiser 窗: β 越大阻带越低")
    axs[0].grid(alpha=0.3)
    axs[0].legend(fontsize=8)
    axs[1].set_xlabel("Kaiser β")
    axs[1].set_ylabel("阻带最高电平 (dB)")
    axs[1].set_title("β 是连续可调的取舍旋钮")
    axs[1].grid(alpha=0.3)
    inst.add_figure(fig, "kaiser_sweep.png")
    inst.table("Kaiser β 扫描 (N=65, cutoff=0.25)",
               [f"β={b:<4g} 阻带 {s:7.1f} dB, 过渡带 {t:.4f}" for b, s, t in rows])


def draw_applied(inst):
    """把设计出的低通真正用起来: 对含噪信号低通, 看频谱前后。"""
    rng = np.random.default_rng(SEED)
    n = 1 << 13
    # 信号: fs/8 附近的窄带 + 宽带噪声
    t = np.arange(n) / FS
    sig = np.exp(2j * np.pi * FS / 8 * t)
    noisy = ch.awgn_waveform(sig, 10.0, rng)
    h = flt.fir_lowpass(0.12, N_TAPS, win="hamming")
    filt = np.convolve(noisy, h)[:n]               # 线性相位, 群延迟 (N-1)/2

    fig, axs = plt.subplots(3, 1, figsize=(11, 9.5))
    for ax, (name, x) in zip(axs, (("滤波前 (信号 + 噪声)", noisy),
                                   ("滤波后 (低通 cutoff=0.12)", filt))):
        f, mag = measure.spectrum(x, FS)
        fs_, ms_ = np.fft.fftshift(f), np.fft.fftshift(mag)
        env, env_mean = measure.spectrum_envelope(fs_, ms_)
        ax.plot(fs_ / 1e6, ms_, lw=0.6, label="|X|")
        ax.plot(fs_ / 1e6, env, lw=1.6, color="tab:orange",
                label=f"包络 (均值 {env_mean:.3g})")
        bw, flo, fhi = measure.occupied_bandwidth(x, FS)
        ax.set_xlabel("frequency (MHz)")
        ax.set_ylabel("|X|")
        ax.set_title(f"{name} —— 99% 占用带宽 {bw/1e6:.2f} MHz")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
    axs[2].plot(np.asarray(h), lw=1.1)
    axs[2].set_title(f"所用低通 (Hamming, N={N_TAPS}, 阻带 "
                     f"{flt.stopband_db(h):.1f} dB)")
    axs[2].set_xlabel("tap")
    axs[2].grid(alpha=0.3)
    inst.add_figure(fig, "fir_applied.png")
    inst.note("窗函数不是滤波器本身, 而是**设计 FIR 的手段**: 它决定把理想响应截断时"
              "通带波纹与旁瓣压到多低, 以及为此付出多宽的过渡带。")
    inst.note("工程选窗: 旁瓣要求不严 -> Hann; 通信里常用 Hamming (固定窗中旁瓣最低);"
              " 需要 −60 dB 以下 -> Blackman 或 Kaiser; 要连续调 -> Kaiser 的 β。")


def main():
    inst = instance.Instance("filter_study", fs=1.0,
                             title="窗函数与 FIR 滤波器设计 (窗函数法)")
    draw_windows(inst)
    draw_fir_compare(inst)
    draw_kaiser(inst)
    draw_applied(inst)
    inst.table("窗与滤波器的关系", [
        "理想低通冲激响应 = sinc, 无限长非因果 -> 必须截断成 FIR",
        "截断 = 时域乘窗; 窗的频谱与理想响应卷积 -> 决定波纹与旁瓣",
        "矩形窗: 过渡带最窄但旁瓣 -13 dB (吉布斯现象)",
        "平滑窗: 旁瓣低但过渡带宽 —— 这是窗函数法的全部权衡",
        "本仓库的成形脉冲 (升余弦/半正弦/正弦) 是解析给定的, 不经窗设计;",
        "窗函数法服务于数字中频的低通/抽取滤波 (docs/02 的 CIC/FIR 那段)",
    ])
    inst.close()
    print(f"✓ 滤波器实验实例: {inst.dir}")
    print(f"  {len(inst.produced)} 个产出")


if __name__ == "__main__":
    main()
