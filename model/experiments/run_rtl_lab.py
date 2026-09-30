# -*- coding: utf-8 -*-
"""
run_rtl_lab.py —— RTL 数据落地后的模型侧分析（模型 ↔ RTL 联合实验）
======================================================================
上游: ``tb/rtl_lab`` 跑 VCS, 把 ``oqpsk_modulator`` 的 12bit 定点 I/Q 导出到
``out/rtl_lab/rtl_iq.npz``（导出时已用断言守住位宽/连续性/bit-true）。

本脚本接手做**信号级**的事, 全部在模型侧:

    RTL 定点 I/Q
        ↓ ① IQ 合成   x = (i + j·q)/2^q      ← 信道里只走**一个**(复)波形
    [DAC 输入]  复基带码字
        ↓ ② DAC 建模  码字 → 模拟量 (可带增益/失调误差)
    [模拟侧]   复基带波形 ──┬─→ 实中频 Re{x·e^{jωt}}  ← 看**包络起伏**
                            └─→ |x| 包络
        ↓ ③ 信道  AWGN / CFO / 多径 …
    [ADC 输入] 采样后的模拟量
        ↓ ④ ADC 建模  量化 + 饱和 → 定点码字
        ↓ ⑤ 接收机  匹配滤波 → 同步 → 解扩 → 判决
        ↓ ⑥ 可视化  波形 / 包络 / 频谱 / 星座 / 眼图

运行: python model/experiments/run_rtl_lab.py
前提: 先跑 tb/rtl_lab/run.py 生成 out/rtl_lab/rtl_iq.npz
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

import _common
import instance
import measure
import phy_802154 as phy
from baseband import channels as ch, impairments as imp, modulation as mod

plt = _common.init()                          # Agg 后端 + 中文字体
FS = phy.SPS * phy.CHIP_RATE          # 16 MHz
OUT = _common.out_dir("rtl_lab")              # model/out/rtl_lab/（已创建）
SRC = OUT / "rtl_iq.npz"

# 链路参数
SNR_DB = 6.0            # chip SNR
ADC_BITS = 8            # 接收 ADC 位宽 (RTL 侧是 12bit 发送, 接收量化另算)
ADC_FULL_SCALE = 2.0    # ADC 满量程 (相对信号幅度留 headroom)
IF_HZ = 2.0e6           # 观察用中频 (仅为把复基带画成实信号看起伏)

# 非准确定时 (三件套: 载波频偏 / 采样孔径抖动 / 采样率偏差)
# 取值依据实测: 非相干解扩对相位旋转免疫, 所以 CFO 要大到**打坏同步**才显形
# (实测 20 kHz 已是分界, 而晶振 ±40 ppm @2.4 GHz → ±96 kHz, 是真威胁);
# jitter 在**基带**可忽略 (它的 SNR 上限 -20log10(2πfσt) 在 f=2 MHz 时有几十 dB),
# 只有直接射频采样 (f=2.4 GHz) 才致命; SFO 对**短帧**不敏感 (要累积到 1 个采样以上)。
CFO_HZ = 20.0e3         # 载波频偏
JITTER_PS = 500.0       # 采样孔径抖动
SFO_PPM = 160.0         # 采样率偏差


# ----------------------------------------------------------------------
# ① IQ 合成 与 ②/④ DAC / ADC 建模
# ----------------------------------------------------------------------
def iq_compose(i_codes, q_codes, q_scale):
    """定点 I/Q 码字 → 复基带。**这就是「IQ 合成」**: 信道里只走这一个波形。"""
    return (np.asarray(i_codes, dtype=float) + 1j * np.asarray(q_codes, dtype=float)) / q_scale


def dac_model(x, gain_err_db=0.0, offset=(0.0, 0.0)):
    """DAC: 复基带码字 → 模拟量（可带增益/失调误差）。默认理想。"""
    y = np.asarray(x, dtype=complex) * 10 ** (gain_err_db / 20)
    return y + complex(*offset)


def adc_model(x, bits=ADC_BITS, full_scale=ADC_FULL_SCALE):
    """ADC: 模拟量 → 定点码字（均匀量化 + 饱和）。返回 (码字, 步长)。

    这是**接收链路的数字边界**: 之前的一切都是"模拟量", 之后才是数字基带。
    """
    step = 2.0 * full_scale / (1 << bits)
    hi, lo = (1 << (bits - 1)) - 1, -(1 << (bits - 1))
    code = np.clip(np.round(np.real(x) / step), lo, hi).astype(int) \
        + 1j * np.clip(np.round(np.imag(x) / step), lo, hi).astype(int)
    return code * step, step


def to_real_if(x, f_if=IF_HZ, fs=FS):
    """复基带 → **实**中频信号（正交上变频）。看"起伏"就得看这个: 它才是
    真正会在电路里流动的单路电压。"""
    n = np.arange(len(x))
    return np.real(np.asarray(x) * np.exp(2j * np.pi * f_if * n / fs))


def fixed_point_view(codes, q_scale, bits, name, n_show=512):
    """**定点图样**: 直接看码字的台阶与满量程, 而不是看过平滑后的浮点波形。

    这就是 Verdi 里把总线切 Analog 看的东西 —— 用 Python 画一样, 而且能连同
    量化台阶一起看。"""
    fig, axs = plt.subplots(2, 1, figsize=(11.5, 6), sharex=True)
    lim = (1 << (bits - 1)) - 1
    c = np.asarray(codes)[:n_show]
    axs[0].step(np.arange(len(c)), c, where="mid", lw=0.9, color="tab:blue")
    axs[0].axhline(lim, color="r", ls=":", lw=1.0, label=f"满量程 ±{lim}")
    axs[0].axhline(-lim, color="r", ls=":", lw=1.0)
    axs[0].set_title(f"{name} —— 定点码字 (有符号 {bits}bit, Q2.6; 1.0 = {q_scale:g}); "
                     f"实际峰值 {np.abs(c).max()}")
    axs[0].set_ylabel("码字")
    axs[0].legend(fontsize=8)
    axs[0].grid(alpha=0.3)
    axs[1].step(np.arange(len(c)), c / q_scale, where="mid", lw=0.9, color="tab:green")
    axs[1].set_xlabel("sample @ 16 MHz")
    axs[1].set_ylabel("幅度 (归一化)")
    axs[1].set_title("同一串码字按 1/64 定标后的浮点视角")
    axs[1].grid(alpha=0.3)
    fig.suptitle(f"定点图样: {name}", fontsize=12)
    return fig


def timing_study(inst, tx_analog, syms, sps):
    """非准确定时三件套对接收的影响: 载波频偏 / 采样孔径抖动 / 采样率偏差。

    三者都在**数字波形之后**注入 (它们本质是模拟/前端效应), 而 RTL 波形本身理想
    —— 这正好把「RTL 数字链」与「通道非理想」干净分开。
    """
    n_cps = 32
    cases = [("0. 理想 (RTL 原波形)", lambda w, r: w),
             (f"1. +CFO {CFO_HZ/1e3:g} kHz", lambda w, r: imp.add_cfo(w, CFO_HZ)),
             (f"2. +抖动 {JITTER_PS:g} ps",
              lambda w, r: imp.add_jitter(w, JITTER_PS, FS, r)),
             (f"3. +SFO {SFO_PPM:g} ppm", lambda w, r: imp.add_sfo(w, SFO_PPM)),
             ("4. 三件套齐上", None),
             ("5. 同上 + CFO 前导估计消旋", None)]
    rows, bers = [], []
    for name, fn in cases:
        rng = np.random.default_rng(7)
        w = ch.awgn_waveform(tx_analog, SNR_DB, np.random.default_rng(3))
        if fn is not None:
            w = fn(w, rng)
        else:
            w = imp.add_sfo(imp.add_jitter(imp.add_cfo(w, CFO_HZ), JITTER_PS, FS, rng),
                            SFO_PPM)
            if "消旋" in name:
                f_est = phy.estimate_cfo_preamble_diff(w)
                w = phy.correct_cfo(w, f_est)
                name += f" (f_est={f_est/1e3:.2f} kHz)"
        code, _ = adc_model(w)
        mf = phy.matched_filter(code)
        p, _, _ = phy.correlate_preamble(mf, return_corr=True)
        chips = phy.sample_chips(mf, p, len(syms) * n_cps)
        rx = phy.despread_chips(chips)
        err = int(sum(bin(int(v)).count("1") for v in (rx ^ syms)))
        ber = err / (4 * len(syms))
        bers.append(max(ber, 1e-9))
        rows.append(f"{name:<36} BER={ber:.3e}  同步 p={p}")
        print("  " + rows[-1])
    return rows, bers


def main():
    if not SRC.exists():
        sys.exit(f"缺少 RTL 数据 {SRC}\n请先运行: .venv/bin/python tb/rtl_lab/run.py")

    d = np.load(SRC)
    i_c, q_c, q_scale = d["i"], d["q"], float(d["q_scale"])
    syms = d["syms"]
    sps, n_chips_per_sym = int(d["sps"]), 32
    period = int(d["sample_per_sym"])          # 256 采样/符号

    # ---------------- 断言: 数据落地质量 ----------------
    assert i_c.shape == q_c.shape and i_c.ndim == 1, "I/Q 形状异常"
    assert i_c.size == int(d["n_q"]), f"采样数 {i_c.size} != 元数据 n_q={int(d['n_q'])}"
    assert i_c.size == len(syms) * period + sps // 2, \
        f"采样数 {i_c.size} != 符号数×每符号采样 + 半码片拖尾 ({len(syms)*period}+{sps//2})"
    assert q_scale > 0, "定标因子异常"
    bits = int(d["iq_bits"])
    lim = 1 << (bits - 1)
    assert np.abs(i_c).max() < lim and np.abs(q_c).max() < lim, "码字越界"
    print(f"✓ RTL 数据校验通过: {i_c.size} 采样 = {len(syms)} 符号 × {period} + "
          f"{sps//2} 拖尾, {bits}bit 定点 (1.0 = {q_scale:g})")

    # ---------------- ①② 合成 + DAC ----------------
    tx = iq_compose(i_c, q_c, q_scale)              # 信道里唯一的那一个波形
    tx_analog = dac_model(tx)                       # 理想 DAC

    inst = instance.Instance("rtl_lab", fs=FS,
                             title="模型 ↔ RTL 联合实验: RTL 定点 I/Q → 合成 → 信道 → 接收")

    # ---------------- ③ 信道 (主链路保持干净: 只加 AWGN) ----------------
    # 非准确定时三件套单独在 timing_study() 里对照, 不混进基准链路 —— 否则
    # 基准会被 CFO 打崩, “干净 vs 损伤”的对比就没了。
    rng = np.random.default_rng(3)
    rx_analog = ch.awgn_waveform(tx_analog, SNR_DB, rng)

    # ---------------- ④ ADC ----------------
    rx_code, step = adc_model(rx_analog)
    print(f"  ADC: {ADC_BITS}bit, 步长 {step:.5f}, 饱和点 {ADC_FULL_SCALE} "
          f"(信号峰值 {np.abs(rx_analog).max():.3f})")
    clip_frac = float(np.mean(np.abs(rx_analog) > ADC_FULL_SCALE))
    assert clip_frac < 1e-3, f"ADC 饱和比例过高: {clip_frac:.2%} (应留足 headroom)"

    # ---------------- ⑤ 接收机 ----------------
    mf = phy.matched_filter(rx_code)
    p, peak, _ = phy.correlate_preamble(mf, return_corr=True)
    chips = phy.sample_chips(mf, p, len(syms) * n_chips_per_sym)
    rx_syms = phy.despread_chips(chips)
    bit_err = int(sum(bin(int(v)).count("1") for v in (rx_syms ^ syms)))
    ber = bit_err / (4 * len(syms))
    # 真实 EVM: 解扩软值 (以发送符号的 PN 序列为参考, 理想值 = 1+0j)
    soft = measure.soft_values_from_chips(chips, phy.CHIP, syms)
    evm = measure.evm(soft, np.ones_like(soft))
    print(f"  接收: 同步 p={p}, BER={ber:.3e}, EVM={evm:.3f}")

    # ---------------- ⑥ 可视化 ----------------
    n_show = 4 * period
    real_if = to_real_if(tx_analog)
    env = np.abs(tx_analog)

    fig, axs = plt.subplots(4, 1, figsize=(11.5, 12))
    axs[0].plot(to_real_if(tx_analog)[:n_show], lw=0.7, color="tab:blue")
    axs[0].set_title(f"① RTL 定点 I/Q 合成后 → **实中频**信号 (IF = {IF_HZ/1e6:g} MHz) "
                     f"—— 这才是电路里真实流动的单路电压")
    axs[0].set_ylabel("voltage (a.u.)")
    axs[1].plot(env[:n_show], lw=0.9, color="tab:purple")
    axs[1].axhline(env.mean(), color="k", ls="--", lw=0.8,
                   label=f"均值 {env.mean():.3f}")
    axs[1].set_title(f"② 包络 |x| —— 起伏即 PAPR: "
                     f"峰/均 = {env.max()/env.mean():.2f} "
                     f"({20*np.log10(env.max()/env.mean()):.1f} dB)")
    axs[1].set_ylabel("|x|")
    axs[1].legend(fontsize=8)
    axs[2].plot(tx_analog.real[:n_show], lw=0.7, label="I (合成前的实部)")
    axs[2].plot(tx_analog.imag[:n_show], lw=0.7, alpha=0.8, label="Q (虚部)")
    axs[2].set_title("③ 复基带 I/Q 分量 (对照: 它们是同一个复波形的两个正交分量)")
    axs[2].set_xlabel("sample @ 16 MHz")
    axs[2].set_ylabel("amplitude")
    axs[2].legend(fontsize=8)
    for ax in axs[:3]:
        ax.grid(alpha=0.3)
        for k in range(0, n_show, period):
            ax.axvline(k, color="k", lw=0.4, alpha=0.18)
    freqs, mag = measure.spectrum(tx_analog, FS)
    axs[3].plot(np.fft.fftshift(freqs) / 1e6, 20 * np.log10(np.fft.fftshift(mag) + 1e-12),
                lw=0.7)
    axs[3].set_title("④ 频谱 (竖线 = 符号边界, 每 256 采样)")
    axs[3].set_xlabel("frequency (MHz)")
    axs[3].set_ylabel("dB")
    axs[3].grid(alpha=0.3)
    fig.suptitle("RTL → 模型 联合实验: oqpsk_modulator 的 12bit 定点 I/Q 落地后", fontsize=13)
    inst.add_figure(fig, "rtl_waveform_views.png")

    fig2, axs2 = plt.subplots(1, 3, figsize=(15, 4.4))
    for ax, (name, sig) in zip(axs2, (("发射 (DAC 输出, 模拟量)", tx_analog),
                                      (f"信道输出 (+AWGN {SNR_DB:g} dB chip)",
                                       rx_analog),
                                      (f"ADC 输入 ({ADC_BITS}bit 量化)",
                                       rx_code))):
        f, m = measure.spectrum(sig, FS)
        ax.plot(np.fft.fftshift(f) / 1e6, 20 * np.log10(np.fft.fftshift(m) + 1e-12), lw=0.7)
        bw, flo, fhi = measure.occupied_bandwidth(sig, FS)
        ax.set_title(f"{name}\n99% 占用带宽 {bw/1e6:.2f} MHz", fontsize=10)
        ax.set_xlabel("frequency (MHz)")
        ax.set_ylabel("dB")
        ax.grid(alpha=0.3)
    fig2.suptitle("链路三级频谱: DAC 输出 → 信道 → ADC 输入", fontsize=12)
    inst.add_figure(fig2, "rtl_link_spectrum.png")

    inst.spectrum(tx_analog, title=f"{inst.title} —— DAC 输出频谱")
    inst.energy(tx_analog)
    inst.constellation(chips.reshape(-1, n_chips_per_sym).sum(axis=1) / n_chips_per_sym,
                       title=f"{inst.title} —— 解扩软值星座 (BER={ber:.2e})")
    inst.eye(mf, period=sps, first_peak=phy.chip_peak_index(p, 0), n_traces=40,
             title=f"{inst.title} —— 接收码片眼图")

    # ---------------- 定点图样 (Verdi 里把总线切 Analog 看的就是这个) ----------------
    inst.add_figure(fixed_point_view(i_c, q_scale, bits, "RTL 输出的 I 路"),
                    "fixed_point_i.png")
    inst.add_figure(fixed_point_view(q_c, q_scale, bits, "RTL 输出的 Q 路"),
                    "fixed_point_q.png")

    # ---------------- 非准确定时对比 ----------------
    print("\n=== 非准确定时三件套 (CFO / 抖动 / SFO) ===")
    rows_t, _ = timing_study(inst, tx_analog, syms, sps)
    inst.table("非准确定时 (在 RTL 理想波形之后注入)", rows_t)

    # ---------------- 显式正交上/下变频 (信道里只有一路实信号) ----------------
    # 平时我们用复包络 x = I+jQ 做“等效基带建模”, 正交下变频被隐式吸收了。
    # 这一段把这一步显式走一遍, 看“单路模拟量 ←→ I/Q”到底怎么转换的。
    print("\n=== 显式正交上/下变频 (信道里只有一路实信号) ===")
    f_lo = FS / 4                                          # 本振 4 MHz
    s_real = mod.upconvert(tx_analog, f_lo, FS)            # ← 单路实信号!
    n_ch = len(s_real)
    print(f"  上变频: 复基带 {n_ch} 点 -> 实信号 {len(s_real)} 点 (单路), "
          f"峰值 {np.abs(s_real).max():.3f}")

    def _rec_err(x_orig, rel_noise):
        n = len(x_orig)
        s = mod.upconvert(x_orig, f_lo, FS)
        if rel_noise:
            s = s + rel_noise * np.std(s) * np.random.default_rng(5).standard_normal(n)
        xr = mod.downconvert(s, f_lo, FS) * 2.0            # 理想下变频给出 x/2, 故 ×2
        e = xr - x_orig
        return float(np.sqrt(np.mean(np.abs(e) ** 2)) /
                     np.sqrt(np.mean(np.abs(x_orig) ** 2)))

    rel_ideal = _rec_err(tx_analog, 0.0)                   # 无噪声: 应接近 0
    rel = _rec_err(tx_analog, 0.05)                        # +5% RMS 噪声 (~26 dB SNR)
    print(f"  下变频+LPF 恢复 I/Q (DSSS 波形): 无噪声误差 {rel_ideal:.2%} "
          f"(旁瓣重叠所致, 见下方窄带对照), 加 5% 噪声后 {rel:.1%}")
    s_rx = mod.upconvert(tx_analog, f_lo, FS)
    s_rx = s_rx + 0.05 * np.std(s_rx) * np.random.default_rng(5).standard_normal(n_ch)
    x_rec = mod.downconvert(s_rx, f_lo, FS) * 2.0

    # 用窄带信号验证“链本身无损”: 旁瓣不宽时才看得出上下变频是否真的可逆。
    # 取**整数个周期** (154 个 / 8192 点) —— 否则首尾相位不连续, FFT 的循环卷积
    # 会在边界处引入虚假误差, 把结论带偏。
    t_nb = np.arange(8192) / FS
    f_nb = 154 * FS / 8192                                 # ≈ 300.8 kHz
    x_nb = np.exp(2j * np.pi * f_nb * t_nb)
    s_nb = mod.upconvert(x_nb, f_lo, FS)
    x_nb_rec = mod.downconvert(s_nb, f_lo, FS) * 2.0
    rel_nb = float(np.sqrt(np.mean(np.abs(x_nb_rec - x_nb) ** 2)))
    print(f"  窄带对照 ({f_nb/1e3:.1f} kHz 单音, 整数周期): 无噪声相对误差 {rel_nb:.2e} "
          f"-> 带限信号下链无损")

    fig3, axs3 = plt.subplots(3, 1, figsize=(11.5, 9.5))
    n_show_if = 8 * 32 * phy.SPS
    axs3[0].plot(s_real[:n_show_if], lw=0.6, color="tab:blue")
    axs3[0].set_title(f"① 上变频后: **单路实信号** s(t) = I·cos(ωt) − Q·sin(ωt) "
                      f"(本振 {f_lo/1e6:g} MHz) —— 信道里真正流动的只有这一路")
    axs3[0].set_ylabel("voltage")
    axs3[1].plot(tx_analog.real[:n_show_if], lw=0.7, label="原始 I")
    axs3[1].plot(x_rec.real[:n_show_if], lw=0.7, alpha=0.75, label="下变频恢复 I")
    axs3[1].legend(fontsize=8)
    axs3[1].set_title(f"② 下变频 + 低通后恢复的 I (无噪声相对误差 {rel_ideal:.2%})")
    axs3[1].set_ylabel("amplitude")
    for ax, tag in ((axs3[0], "实信号"), (axs3[1], "恢复")):
        ax.grid(alpha=0.3)
        ax.set_xlabel("sample @ 16 MHz")
    f1, m1 = measure.spectrum(s_real, FS)
    f2, m2 = measure.spectrum(tx_analog, FS)
    axs3[2].plot(np.fft.fftshift(f1) / 1e6, 20 * np.log10(np.fft.fftshift(m1) + 1e-12),
                 lw=0.7, label="实信号 s(t) —— 双边带, 带宽翻倍")
    axs3[2].plot(np.fft.fftshift(f2) / 1e6, 20 * np.log10(np.fft.fftshift(m2) + 1e-12),
                 lw=0.7, alpha=0.75, label="复包络 x(t) —— 单边带")
    axs3[2].axvline(f_lo / 1e6, color="r", ls=":", lw=1.0)
    axs3[2].text(f_lo / 1e6 + 0.3, -20, f"本振 {f_lo/1e6:g} MHz", fontsize=8, color="r")
    axs3[2].legend(fontsize=8)
    axs3[2].set_title("③ 频谱: 实信号在 ±f_lo 处各一份 (共轭对称) —— 这就是“一个模拟量”的代价")
    axs3[2].set_xlabel("frequency (MHz)")
    axs3[2].set_ylabel("dB")
    axs3[2].grid(alpha=0.3)
    fig3.suptitle("显式正交调制/解调: 复包络 → 单路实信号 → 复包络", fontsize=13)
    inst.add_figure(fig3, "iq_updownconvert.png")
    inst.table("正交上/下变频 (显式走一遍)", [
        f"本振 f_lo = {f_lo/1e6:g} MHz; 上变频: s = Re{{x·e^(j2πf_lo t)}} (单路实信号)",
        "实信号是**双边带**: 基带正/负频率分量各自搬到 ±f_lo 附近 (共轭对称)",
        "下变频: x' = LPF{s·e^(-j2πf_lo t)}; 理想情况 x' = x/2, 已按 ×2 还原",
        f"窄带对照 (整数周期单音): 无噪声误差 {rel_nb:.1e} -> **链本身确实无损**",
        f"DSSS 波形: 无噪声误差 {rel_ideal:.1%} —— 残余来自**频谱重叠**而非链有损:"
        f"半正弦成形的旁瓣很宽, 基带旁瓣超出 ±f_lo/2 落到镜像上, LPF 分不开",
        f"加 5% RMS 噪声 (~26 dB SNR) 后的误差 {rel:.1%} 则主要反映噪声",
    ])
    inst.note("**为什么可以“直接分离 I/Q”**: 平时用的 x = I + jQ 是**复包络**, 不是物理量。\n"
              "以它为载体做完所有处理, 数学上等价于「正交上变频 → 模拟域 → 正交下变频」\n"
              "整条链 —— 因为正交是线性的、不随这些操作改变。这就是等效基带模型, 工程上\n"
              "默认这么用。**代价是看不见下变频那一步引入的误差**: LO 相位偏差表现为整体\n"
              "旋转, 而 I/Q 两路的增益/相位不匹配才会产生镜像分量 (见 algo 与前端校正那几节)。")

    inst.table("数据落地 (RTL 侧断言保证)", [
        f"来源: tb/rtl_lab 跑 VCS, 导出 {bits}bit 定点 I/Q (Q2.6, 1.0 = {q_scale:g})",
        f"采样数 {i_c.size} = {len(syms)} 符号 × {period} 采样/符号",
        f"断言: 形状一致 / 码字不越界 / sample_dv 无缺口 / 与黄金模型 bit-true",
    ])
    inst.table("模型侧链路 (全程只走一个复波形)", [
        f"① IQ 合成: x = (i + jq)/2^6 —— **信道里只走这一个波形**",
        f"② DAC: 码字→模拟量 (理想); ③ 信道: AWGN {SNR_DB:g} dB (chip 口径)",
        f"④ ADC: {ADC_BITS}bit 量化, 步长 {step:.5f}, 饱和比例 {clip_frac:.1e}",
        f"⑤ 接收: 匹配滤波 → 同步(p={p}) → 解扩 → BER {ber:.3e}, EVM {evm:.3f}",
        f"实中频观察: IF {IF_HZ/1e6:g} MHz, 包络峰/均 "
        f"{env.max()/env.mean():.2f} ({20*np.log10(env.max()/env.mean()):.1f} dB)",
    ])
    inst.note("**为什么信道里只走一个波形**: I/Q 是正交分量、不是两路独立信道 —— "
              "它们属于同一个复包络。分开传会丢掉两者的相对相位(即绝对相位), "
              "而对一个真实的射频/中频通道来说, 流动的只有一路电压 "
              "Re{x·e^{jωt}}; I/Q 只是它在两个正交基上的投影。")
    inst.note("**看起伏要看实信号或包络**: 复基带的 I/Q 各自看都只是随机跳变, 只有合成后的"
              "|x| 才反映波形的幅度包络。本帧实测峰/均 4.9 dB —— 这个起伏是"
              "**必然的**: O-QPSK 的 I/Q 分别承载独立的偶/奇码片, 两者幅度不相干, "
              "所以 |x|² = I²+Q² 会随数据变化 (射频侧的“恒包络”需要 I/Q 相位配合,"
              "对独立数据并不完全成立)。要看实信号的起伏就画 Re{x·e^{jωt}}。")
    inst.note("模拟侧的假设: 本实验把 DAC 当理想 (可选增益/失调误差), 并假设模拟侧"
              "已有抗混叠/去直流等设备 —— 所以模型侧只需处理数字可及的那些非理想。")
    inst.close()

    print(f"\n✓ 实例产出: {inst.dir}")
    print(f"  {len(inst.produced)} 个文件")


if __name__ == "__main__":
    main()
