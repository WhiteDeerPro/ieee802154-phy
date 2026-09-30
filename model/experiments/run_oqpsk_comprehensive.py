# -*- coding: utf-8 -*-
"""
run_oqpsk_comprehensive.py —— OQPSK 完整可视化
===============================================
基于 baseband/measure/visualize 模块的 OQPSK 系统级可视化:
  1. 基带码元波形（I/Q 路）
  2. OQPSK 调制信号（时域 + I/Q 分路）
  3. 功率谱密度
  4. 星座图（理想 + 噪声）
  5. 眼图（I/Q 路）
  6. 非理想效应：
     - IQ 不平衡（增益/相位不匹配）
     - DC 偏移
     - 多径效应
     - 定时偏差导致的星座旋转
  7. 对应的补偿处理方案和效果对比

本脚本只负责「配置 + 建链 + 出图」:
  - 损伤注入走链路库 —— ``impairments.*_stage`` 由 ``baseband.link.Chain`` 组合成
    信道段 (本实验在波形域, 故直接组装 Chain; 帧级 802.15.4 配方见 ``chains.link``);
  - 观测点取样/表征走 ``measure`` (眼图数据), 画图走 ``visualize``;
  - 成形与调制走 ``baseband.modulation`` (半正弦脉冲 + O-QPSK 调制)。

运行: python model/experiments/run_oqpsk_comprehensive.py
"""

import sys
from pathlib import Path

# 本脚本位于 model/experiments/ —— 库模块 (chains / measure / phy_802154 / visualize)
# 在上一级的 model/, 而 Python 只自动把脚本自身目录加入 sys.path, 故显式引导。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from scipy import signal as sp_signal

# 复用项目基础模块
import _common
import chains
import measure
import visualize
from baseband import impairments, modulation
from baseband.link import Chain, Signal

plt = _common.init()                          # Agg 后端 + 中文字体
OUT = _common.out_dir("oqpsk_visual")         # model/out/oqpsk_visual/（已创建）

# ===========================
# 参数配置
# ===========================
CHIP_RATE = 2e6          # 2 Mchip/s
SPS = 8                  # 每码片 8 采样
FS = CHIP_RATE * SPS     # 16 MHz 采样率
N_CHIPS = 256            # 仿真码片数
SNR_DB = 10              # 星座图对比用的码片 SNR
N_CONST = 400            # 每张星座图的点数
RNG = np.random.default_rng(42)   # 固定种子: 码片序列与噪声逐位可复现


# ===========================
# 辅助函数
# ===========================

def impaired(tx, *stages):
    """理想发送波形 → 受损接收波形 (链路库的信道段)。

    信道入口约定 ``rx = tx``, 各损伤阶段在 ``rx`` 上按列表顺序接力; 与逐个直接
    调用 ``impairments.add_*`` 纯函数完全等价 (``*_stage`` 只是薄封装), 但「损伤
    组合 + 作用顺序」变成一处声明, 新增损伤只需往列表里加一个阶段。

    注意: 含 AWGN 的组合只跑一次 —— 阶段闭包持有 rng, 重复跑会重复消耗随机数。
    """
    return Chain(name="oqpsk_impairments").then_channel(*stages).run(Signal(tx=tx)).rx


def const_points(x, n=N_CONST):
    """取星座观测点: 每码片第 ``SPS//2`` 个样本 = 半正弦成形峰值处, 取前 n 个。"""
    return x[SPS // 2::SPS][:n]


def correct_iq_imbalance(x, gain_imb_db, phase_imb_deg):
    """IQ 不平衡补偿: ``add_iq_imbalance`` 的解析逆变换 (已知损伤量, 非盲估计)。

    盲估计/校正路由 ``baseband.frontend.estimate_dc_iq`` (以已知前导为参考) 承担;
    这里画的是「理想补偿」的上界, 用来对照损伤前后的星座。
    """
    g = 10 ** (gain_imb_db / 20)
    p = np.radians(phase_imb_deg)
    I = x.real / g
    Q = (x.imag - np.sin(p) * x.real) / np.cos(p)
    return I + 1j * Q


def compute_psd(x, fs, nperseg=512):
    """功率谱密度 (dB): Welch 平均周期图, 双边谱把零频移到中心。

    ``measure.spectrum`` 是单次 FFT 幅度谱 (线性、不平均、不取 dB), 与本图
    「PSD (dB/Hz)」的统计口径不同, 故此处保留 Welch 实现。
    """
    f, pxx = sp_signal.welch(x, fs=fs, nperseg=nperseg, return_onesided=False)
    return np.fft.fftshift(f), 10 * np.log10(np.fft.fftshift(pxx) + 1e-12)


def constellation_row(cases, path, figsize, caption):
    """一行并列的星座对比图并出图。

    cases = [(子图标题, 星座点, plot_constellation 关键字), ...] —— 关键字逐子图传入,
    因此各子图可以用不同的点大小 / 透明度 / 颜色。
    """
    fig, axes = plt.subplots(1, len(cases), figsize=figsize)
    for ax, (title, pts, kw) in zip(axes, cases):
        visualize.plot_constellation(pts, ax=ax, title=title, **kw)
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()
    print(caption)


def main():
    print("=" * 60)
    print("OQPSK full visualization simulation (based on baseband module)")
    print("=" * 60)

    # 生成基带码片序列
    chips = 2 * RNG.integers(0, 2, size=N_CHIPS) - 1  # ±1 码片
    print(f"✓ Generated {N_CHIPS} random chips")

    # 半正弦脉冲
    pulse = modulation.half_sine(sps=SPS)
    pulse_energy = np.sum(pulse**2)
    print(f"✓ Half-sine pulse (SPS={SPS}), energy={pulse_energy:.2f}")

    # OQPSK 调制
    sig_ideal = modulation.oqpsk_modulate(chips, pulse)
    print(f"✓ OQPSK modulation done, signal length: {len(sig_ideal)} samples")

    # ===========================
    # 图1: 基带码元波形
    # ===========================
    fig, axes = plt.subplots(3, 1, figsize=(12, 8))

    # 显示前 32 个码片
    t_chip = np.arange(32 * SPS) / FS * 1e6  # 转换为微秒

    axes[0].step(np.arange(32), chips[:32], where='post', linewidth=1.5)
    axes[0].set_ylabel('Chip value')
    axes[0].set_title('Baseband chip sequence (first 32 chips)')
    axes[0].grid(True, alpha=0.3)
    axes[0].set_ylim([-1.5, 1.5])

    # I/Q 路分离显示
    axes[1].plot(t_chip, sig_ideal[:32*SPS].real, label='I path', linewidth=1.2)
    axes[1].set_ylabel('Amplitude')
    axes[1].set_title('I-path baseband signal (half-sine shaping)')
    axes[1].grid(True, alpha=0.3)
    axes[1].legend()

    axes[2].plot(t_chip, sig_ideal[:32*SPS].imag, label='Q path (delayed half chip)', color='orange', linewidth=1.2)
    axes[2].set_ylabel('Amplitude')
    axes[2].set_xlabel('Time (µs)')
    axes[2].set_title('Q-path baseband signal (half-sine shaping, offset half chip)')
    axes[2].grid(True, alpha=0.3)
    axes[2].legend()

    plt.tight_layout()
    plt.savefig(OUT / "01_baseband_waveform.png", dpi=150)
    plt.close()
    print("✓ Figure 1: baseband symbol waveform -> 01_baseband_waveform.png")

    # ===========================
    # 图2: OQPSK 调制信号
    # ===========================
    fig, axes = plt.subplots(2, 1, figsize=(12, 7))

    t_show = np.arange(64 * SPS) / FS * 1e6
    axes[0].plot(t_show, sig_ideal[:64*SPS].real, label='I', linewidth=1)
    axes[0].plot(t_show, sig_ideal[:64*SPS].imag, label='Q', linewidth=1, alpha=0.8)
    axes[0].set_ylabel('Amplitude')
    axes[0].set_title('OQPSK modulated signal I/Q components')
    axes[0].legend()
    axes[0].grid(True, alpha=0.3)

    # 复数包络幅度
    env = np.abs(sig_ideal[:64*SPS])
    axes[1].plot(t_show, env, linewidth=1.2, color='purple')
    axes[1].set_ylabel('Envelope amplitude')
    axes[1].set_xlabel('Time (µs)')
    axes[1].set_title('OQPSK envelope (half-sine shaping smooths the envelope)')
    axes[1].grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(OUT / "02_oqpsk_modulated.png", dpi=150)
    plt.close()
    print("✓ Figure 2: OQPSK modulated signal -> 02_oqpsk_modulated.png")

    # ===========================
    # 图3: 功率谱密度
    # ===========================
    fig, ax = plt.subplots(figsize=(12, 6))

    f, psd = compute_psd(sig_ideal, FS, nperseg=1024)
    ax.plot(f / 1e6, psd, linewidth=1.2)
    ax.set_xlabel('Frequency (MHz)')
    ax.set_ylabel('Power spectral density (dB/Hz)')
    ax.set_title('OQPSK power spectral density (half-sine shaping)')
    ax.grid(True, alpha=0.3)
    ax.axvline(CHIP_RATE / 1e6, color='r', linestyle='--', alpha=0.5, label=f'Chip rate ±{CHIP_RATE/1e6} MHz')
    ax.axvline(-CHIP_RATE / 1e6, color='r', linestyle='--', alpha=0.5)
    ax.legend()

    plt.tight_layout()
    plt.savefig(OUT / "03_power_spectrum.png", dpi=150)
    plt.close()
    print("✓ Figure 3: power spectral density -> 03_power_spectrum.png")

    # ===========================
    # 图4: 星座图 (理想 vs 加噪)
    # ===========================
    # AWGN 只注入一次 (图11 复用同一段受损波形), 避免重复消耗 rng
    sig_noisy = impaired(sig_ideal, chains.awgn(SNR_DB, RNG))
    sig_sampled = const_points(sig_ideal)
    sig_noisy_sampled = const_points(sig_noisy)

    constellation_row(
        [('Ideal constellation', sig_sampled, dict(s=10, alpha=0.6)),
         (f'Constellation with noise (SNR={SNR_DB}dB)', sig_noisy_sampled,
          dict(s=10, alpha=0.5, color='orange'))],
        OUT / "04_constellation.png", (12, 5),
        "✓ Figure 4: constellation -> 04_constellation.png")

    # ===========================
    # 图5: 眼图
    # ===========================
    eye_i = measure.eye_data(sig_ideal.real, sps=2*SPS, n_traces=100)
    eye_q = measure.eye_data(sig_ideal.imag, sps=2*SPS, n_traces=100)

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))

    visualize.plot_eye(eye_i, ax=axes[0], title='I-path eye diagram', alpha=0.3, linewidth=0.8, color='blue')
    visualize.plot_eye(eye_q, ax=axes[1], title='Q-path eye diagram', alpha=0.3, linewidth=0.8, color='orange')

    plt.tight_layout()
    plt.savefig(OUT / "05_eye_diagram.png", dpi=150)
    plt.close()
    print("✓ Figure 5: eye diagram -> 05_eye_diagram.png")

    # ===========================
    # 图6: 非理想效应 - IQ 不平衡
    # ===========================
    sig_iq_imb = impaired(sig_ideal, impairments.iq_stage(1.5, 8.0))
    sig_iq_corrected = correct_iq_imbalance(sig_iq_imb, gain_imb_db=1.5, phase_imb_deg=8.0)

    constellation_row(
        [('Ideal constellation', sig_sampled, dict(s=8, alpha=0.5)),
         ('IQ imbalance (gain 1.5dB, phase 8°)', const_points(sig_iq_imb),
          dict(s=8, alpha=0.5, color='red')),
         ('After IQ imbalance correction', const_points(sig_iq_corrected),
          dict(s=8, alpha=0.5, color='green'))],
        OUT / "06_iq_imbalance.png", (15, 4.5),
        "✓ Figure 6: IQ imbalance and correction -> 06_iq_imbalance.png")

    # ===========================
    # 图7: 非理想效应 - DC 偏移
    # ===========================
    sig_dc = impaired(sig_ideal, impairments.dc_stage(0.15, 0.1))
    sig_dc_corrected = sig_dc - np.mean(sig_dc)   # DC 补偿 = 去均值 (简单有效)

    constellation_row(
        [('Ideal constellation', sig_sampled, dict(s=8, alpha=0.5)),
         ('DC offset (I=0.15, Q=0.1)', const_points(sig_dc),
          dict(s=8, alpha=0.5, color='red')),
         ('After DC offset correction (mean subtraction)', const_points(sig_dc_corrected),
          dict(s=8, alpha=0.5, color='green'))],
        OUT / "07_dc_offset.png", (15, 4.5),
        "✓ Figure 7: DC offset and correction -> 07_dc_offset.png")

    # ===========================
    # 图8: 非理想效应 - 多径效应
    # ===========================
    sig_mp = impaired(sig_ideal, impairments.scenario_stage("indoor_3tap", sps=SPS))

    fig, axes = plt.subplots(2, 2, figsize=(12, 10))

    # 时域对比
    t_mp = np.arange(100 * SPS) / FS * 1e6
    axes[0, 0].plot(t_mp, sig_ideal[:100*SPS].real, label='Ideal', linewidth=1.2, alpha=0.7)
    axes[0, 0].plot(t_mp, sig_mp[:100*SPS].real, label='Multipath', linewidth=1.2, alpha=0.7)
    axes[0, 0].set_xlabel('Time (µs)')
    axes[0, 0].set_ylabel('Amplitude')
    axes[0, 0].set_title('I-path time-domain waveform comparison')
    axes[0, 0].legend()
    axes[0, 0].grid(True, alpha=0.3)

    # 星座图对比
    visualize.plot_constellation(sig_sampled, ax=axes[0, 1], title='Ideal constellation', s=8, alpha=0.5)
    visualize.plot_constellation(const_points(sig_mp), ax=axes[1, 0],
                                  title='Multipath constellation (indoor 3-tap)', s=8, alpha=0.5, color='red')

    # 频谱对比
    f_ideal, psd_ideal = compute_psd(sig_ideal, FS, nperseg=512)
    f_mp, psd_mp = compute_psd(sig_mp, FS, nperseg=512)
    axes[1, 1].plot(f_ideal / 1e6, psd_ideal, label='Ideal', alpha=0.7, linewidth=1.2)
    axes[1, 1].plot(f_mp / 1e6, psd_mp, label='Multipath', alpha=0.7, linewidth=1.2)
    axes[1, 1].set_xlabel('Frequency (MHz)')
    axes[1, 1].set_ylabel('Power spectral density (dB/Hz)')
    axes[1, 1].set_title('Power spectrum comparison (multipath causes frequency-selective fading)')
    axes[1, 1].grid(True, alpha=0.3)
    axes[1, 1].legend()

    plt.tight_layout()
    plt.savefig(OUT / "08_multipath.png", dpi=150)
    plt.close()
    print("✓ Figure 8: multipath effects -> 08_multipath.png")

    # ===========================
    # 图9: 非理想效应 - CFO导致的相位旋转
    # ===========================
    sig_cfo = impaired(sig_ideal, impairments.cfo_stage(50e3, fs=FS))

    fig, axes = plt.subplots(2, 2, figsize=(12, 10))

    # 时域相位演化
    phase_ideal = np.angle(sig_ideal[:200*SPS])
    phase_cfo = np.angle(sig_cfo[:200*SPS])
    t_phase = np.arange(200 * SPS) / FS * 1e6

    axes[0, 0].plot(t_phase, np.unwrap(phase_ideal), label='Ideal', linewidth=1)
    axes[0, 0].set_xlabel('Time (µs)')
    axes[0, 0].set_ylabel('Phase (rad)')
    axes[0, 0].set_title('Ideal signal phase')
    axes[0, 0].grid(True, alpha=0.3)

    axes[0, 1].plot(t_phase, np.unwrap(phase_cfo), label='CFO=50kHz', color='orange', linewidth=1)
    axes[0, 1].set_xlabel('Time (µs)')
    axes[0, 1].set_ylabel('Phase (rad)')
    axes[0, 1].set_title('Phase accumulation rotation caused by CFO (50kHz)')
    axes[0, 1].grid(True, alpha=0.3)

    # 星座图对比
    visualize.plot_constellation(sig_sampled, ax=axes[1, 0], title='Ideal constellation', s=8, alpha=0.5)
    visualize.plot_constellation(const_points(sig_cfo), ax=axes[1, 1],
                                  title='Constellation rotation caused by 50kHz CFO', s=8, alpha=0.5, color='red')

    plt.tight_layout()
    plt.savefig(OUT / "09_cfo_rotation.png", dpi=150)
    plt.close()
    print("✓ Figure 9: CFO-induced rotation -> 09_cfo_rotation.png")

    # ===========================
    # 图10: 定时偏差
    # ===========================
    sig_timing = impaired(sig_ideal, impairments.timing_stage(0.3, sps=SPS))

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))

    # 时域对比
    t_cmp = np.arange(64 * SPS) / FS * 1e6
    axes[0].plot(t_cmp, sig_ideal[:64*SPS].real, label='Ideal', linewidth=1.2, alpha=0.7)
    axes[0].plot(t_cmp, sig_timing[:64*SPS].real, label='Timing offset 0.3 chip', linewidth=1.2, alpha=0.7)
    axes[0].set_xlabel('Time (µs)')
    axes[0].set_ylabel('Amplitude')
    axes[0].set_title('I-path time-domain waveform comparison')
    axes[0].legend()
    axes[0].grid(True, alpha=0.3)

    # 星座图
    visualize.plot_constellation(const_points(sig_timing), ax=axes[1],
                                  title='Constellation distortion caused by 0.3 chip timing offset', s=8, alpha=0.5, color='purple')

    plt.tight_layout()
    plt.savefig(OUT / "10_timing_offset.png", dpi=150)
    plt.close()
    print("✓ Figure 10: timing offset -> 10_timing_offset.png")

    # ===========================
    # 图11: 综合对比 - 所有非理想效应
    # ===========================
    fig, axes = plt.subplots(2, 3, figsize=(16, 10))

    # 理想
    visualize.plot_constellation(sig_sampled, ax=axes[0, 0], title='Ideal signal', s=5, alpha=0.6)

    # AWGN
    visualize.plot_constellation(sig_noisy_sampled, ax=axes[0, 1],
                                  title='AWGN (SNR=10dB)', s=5, alpha=0.5, color='orange')

    # IQ 不平衡
    visualize.plot_constellation(const_points(sig_iq_imb), ax=axes[0, 2],
                                  title='IQ imbalance', s=5, alpha=0.5, color='red')

    # DC 偏移
    visualize.plot_constellation(const_points(sig_dc), ax=axes[1, 0],
                                  title='DC offset', s=5, alpha=0.5, color='purple')

    # 多径
    visualize.plot_constellation(const_points(sig_mp), ax=axes[1, 1],
                                  title='Multipath', s=5, alpha=0.5, color='brown')

    # CFO
    visualize.plot_constellation(const_points(sig_cfo), ax=axes[1, 2],
                                  title='CFO 50kHz rotation', s=5, alpha=0.5, color='green')

    plt.suptitle('OQPSK non-ideal effects comprehensive comparison', fontsize=14, fontweight='bold')
    plt.tight_layout()
    plt.savefig(OUT / "11_comprehensive_comparison.png", dpi=150)
    plt.close()
    print("✓ Figure 11: comprehensive comparison -> 11_comprehensive_comparison.png")

    # ===========================
    # 总结
    # ===========================
    print("\n" + "=" * 60)
    print("Simulation complete! Generated figures:")
    print("=" * 60)
    print(f"Output directory: {OUT}")
    print("\nGenerated figures:")
    print("  01_baseband_waveform.png      - baseband symbol waveform (I/Q paths)")
    print("  02_oqpsk_modulated.png        - OQPSK modulated signal and envelope")
    print("  03_power_spectrum.png         - power spectral density")
    print("  04_constellation.png          - constellation (ideal vs noisy)")
    print("  05_eye_diagram.png            - eye diagram (I/Q paths)")
    print("  06_iq_imbalance.png           - IQ imbalance and correction")
    print("  07_dc_offset.png              - DC offset and correction")
    print("  08_multipath.png              - multipath effects analysis")
    print("  09_cfo_rotation.png           - CFO-induced phase rotation")
    print("  10_timing_offset.png          - timing offset effects")
    print("  11_comprehensive_comparison.png - comprehensive comparison of all non-ideal effects")
    print("\nCorrection approach summary:")
    print("  • IQ imbalance: estimate gain/phase errors -> inverse-transform correction")
    print("  • DC offset: subtract signal mean (simple and effective)")
    print("  • Multipath: requires an equalizer (MMSE/ZF) or RAKE receiver")
    print("  • CFO: requires a carrier synchronization loop to track phase rotation")
    print("  • Timing offset: requires timing recovery and interpolation")
    print("\nTechnical parameters:")
    print(f"  • Chip rate: {CHIP_RATE/1e6} Mchip/s")
    print(f"  • Sampling rate: {FS/1e6} MHz (SPS={SPS})")
    print(f"  • Pulse shaping: half-sine (energy={pulse_energy:.2f})")
    print(f"  • Simulated chips: {N_CHIPS}")
    print("=" * 60)


if __name__ == "__main__":
    main()
