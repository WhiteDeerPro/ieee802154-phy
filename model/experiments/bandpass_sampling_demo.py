"""
bandpass_sampling_demo.py
=========================
带通采样（undersampling / IF sampling）数学性质演示
对象: IEEE 802.15.4 2.4 GHz 信号, 占用带宽 B = 2 MHz, 占据频带 [fL, fH] = [2404, 2406] MHz

前提假设（用户提出）: RF 前级已用带通滤波器把信号限制在 [fL, fH] 内 —— §3 量化验证该假设的必要性。

四个实验:
  1. 允许采样率区域: 对 [fL, fH] 计算所有无混叠折叠的 fs 区间 (bandpass sampling theorem)
  2. 频谱折叠仿真: 真实时域信号按 fs = 8 MHz 欠采样, 验证频带无混叠地折到低中频
  3. 噪声折叠: 有/无前级 BPF 两种情况下, 采样后带内噪声底抬升多少 dB
  4. 孔径抖动: 直接对 2.4 GHz 载波采样时, 抖动-信噪比上限曲线

运行: python model/experiments/bandpass_sampling_demo.py
输出: model/out/bandpass_*.png + 终端定量结论
"""

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "out" / "bandpass"
OUT.mkdir(exist_ok=True)
rng = np.random.default_rng(42)

# ---------------- 全局参数 ----------------
fL, fH = 2404e6, 2406e6      # 带通滤波后信号占据频带 (Hz)
fc = (fL + fH) / 2            # 2405 MHz 信道中心
B = fH - fL                   # 2 MHz

def bandpass_regions(fL, fH):
    """返回 [(n, lo, hi), ...]: 每个 n 对应的无混叠采样率区间 [2fH/n, 2fL/(n-1)]"""
    n_max = int(fH // B)
    regs = []
    for n in range(2, n_max + 1):
        lo, hi = 2 * fH / n, 2 * fL / (n - 1)
        if lo <= hi:
            regs.append((n, lo, hi))
    return regs

def band_limited_noise(N, fs, f_keep, seed_rng):
    """FFT 域理想带限高斯噪声 (f_keep: (lo, hi) 正频率区间, Hz), 返回实信号"""
    X = np.fft.rfft(seed_rng.standard_normal(N))
    f = np.fft.rfftfreq(N, 1 / fs)
    mask = (f >= f_keep[0]) & (f <= f_keep[1])
    X[~mask] = 0
    x = np.fft.irfft(X, N)
    return x / np.sqrt(np.mean(x ** 2) + 1e-30)

# =====================================================================
# 实验 1: 允许采样率区域
# =====================================================================
regs = bandpass_regions(fL, fH)
print("=" * 72)
print("Experiment 1: allowed fs regions for bandpass sampling (n = fold order, region width = clock tolerance)")
print("=" * 72)
for n, lo, hi in regs[:6]:
    print(f"  n={n:5d}: fs ∈ [{lo/1e6:9.4f}, {hi/1e6:9.4f}] MHz  (window width { (hi-lo)/1e3:8.2f} kHz, ±{(hi-lo)/2/lo*1e6:9.1f} ppm)")
print("  ...")
for target in (8e6, 16e6):
    hit = [(n, lo, hi) for n, lo, hi in regs if lo <= target <= hi]
    n, lo, hi = hit[0]
    print(f"  fs = {target/1e6:.0f} MHz -> hit n = {n}, tolerance window ±{(hi-lo)/2/target*1e6:.2f} ppm "
          f"[{lo/1e6:.4f}, {hi/1e6:.4f}] MHz")
print(f"  n=1 (lowpass sampling): fs >= 2*fH = {2*fH/1e9:.3f} GHz")

fig, ax = plt.subplots(figsize=(9, 5.5))
for n, lo, hi in regs:
    ax.plot([lo / 1e6, hi / 1e6], [n, n], "b-", lw=1.2)
ax.axvline(2 * B / 1e6, color="r", ls="--", lw=1, label="2B = 4 MHz (bandpass sampling lower bound)")
ax.axvline(8, color="g", ls="-", lw=1.5, label="fs = 8 MHz (this simulation)")
ax.axvline(16, color="m", ls="-", lw=1.5, label="fs = 16 MHz (spec ADC rate)")
ax.set_yscale("log"); ax.set_xscale("log")
ax.set_xlabel("sampling rate $f_s$ (MHz)"); ax.set_ylabel("fold index  n")
ax.set_title("Bandpass-sampling allowed regions, 2.4 GHz / B=2 MHz\n(each line: alias-free $f_s$ window for fold order n)")
ax.legend(loc="upper right"); ax.grid(True, which="both", alpha=0.3)
fig.tight_layout(); fig.savefig(OUT / "bandpass_regions.png", dpi=130); plt.close(fig)

# =====================================================================
# 实验 2: 时域频谱折叠仿真 (fs = 8 MHz, n = 602)
# =====================================================================
print()
print("=" * 72)
print("Experiment 2: time-domain simulation —— 2.4 GHz real passband signal undersampled at fs = 8 MHz")
print("=" * 72)
fs = 8e6
dec = 602                      # 高仿真采样率 = 602 × 8 MHz = 4816 MHz (相干关系, 避免 FFT 泄漏)
fs_hi = fs * dec               # 奈奎斯特 = 2408 MHz > 2406 MHz: 能真实表示 2.4 GHz 通带!
T = 20e-6                      # 20 µs 观测窗
N = int(round(fs_hi * T))      # 96320 = 602 × 160
assert N % dec == 0
t = np.arange(N) / fs_hi

bb = band_limited_noise(N, fs_hi, (0.2e6, 1.0e6), rng)          # 基带带限 (等效任意调制)
tone_hi = 1.0 * np.cos(2 * np.pi * (fc + 0.4e6) * t)           # 标记音 2405.4 MHz (幅度须显著高于每-bin 折叠噪声)
tone_lo = 1.0 * np.cos(2 * np.pi * (fL + 0.2e6) * t)           # 标记音 2404.2 MHz
x = bb * np.cos(2 * np.pi * fc * t) + tone_hi + tone_lo         # 实带通信号

xs = x[::dec].copy()                                            # 欠采样 (理想冲激采样)
Ns = len(xs)

def db_spectrum(sig, fs_):
    w = np.hanning(len(sig))
    X = np.fft.rfft(sig * w)
    P = np.abs(X) ** 2
    P /= np.max(P) + 1e-30
    return np.fft.rfftfreq(len(sig), 1 / fs_), 10 * np.log10(P + 1e-12)

f_orig, P_orig = db_spectrum(x, fs_hi)
f_samp, P_samp = db_spectrum(xs, fs)

# 数值验证: 两个标记音的折叠落点 (预测: 2405.4 -> 2.6 MHz; 2404.2 -> 3.8 MHz [频谱反转])
def peak_near(f, P, center, half=0.15e6):
    m = (f > center - half) & (f < center + half)
    return f[m][np.argmax(P[m])] if m.any() else np.nan

pk1 = peak_near(f_samp, P_samp, 2.6e6)
pk2 = peak_near(f_samp, P_samp, 3.8e6)
print(f"  marker tone 2405.4 MHz -> fold location {pk1/1e6:.3f} MHz  (predicted 2.600, {'OK' if abs(pk1-2.6e6)<30e3 else 'FAIL'})")
print(f"  marker tone 2404.2 MHz -> fold location {pk2/1e6:.3f} MHz  (predicted 3.800, inverted {'OK' if abs(pk2-3.8e6)<30e3 else 'FAIL'})")
inb = (f_samp >= 2e6) & (f_samp <= 4e6)
outb = (f_samp > 0.05e6) & ((f_samp < 1.9e6))
print(f"  out-of-band [0.05,1.9] MHz relative to in-band peak: {np.max(P_samp[outb]) - np.max(P_samp[inb]):.1f} dB "
      f"(alias suppression, should be ≈ <-50 dB with ideal brick-wall BPF)")
print("  conclusion: 2 MHz band folds alias-free to [2, 4] MHz low-IF; n=602 even -> high/low frequency relation inverted (spectrum inversion)")

# 数字下变频到基带 (低中频 3 MHz 混频 + 理想低通)
n = np.arange(Ns)
ddc = xs * np.exp(-2j * np.pi * 3e6 * n / fs)
Xc = np.fft.fft(ddc)
fcfft = np.fft.fftfreq(Ns, 1 / fs)
Xc[np.abs(fcfft) > 1.2e6] = 0
bb_iq = np.fft.ifft(Xc)

fig, axs = plt.subplots(3, 1, figsize=(9, 9))
axs[0].plot(f_orig / 1e6, P_orig, lw=0.8)
axs[0].set_xlim(2395, 2415); axs[0].set_ylim(-80, 2)
axs[0].axvspan(fL / 1e6, fH / 1e6, color="g", alpha=0.15, label="signal band [2404,2406]")
axs[0].set_title("Original passband spectrum (sim @ 4816 MHz)"); axs[0].set_ylabel("dB")
axs[0].legend(loc="upper right"); axs[0].grid(alpha=0.3)
axs[1].plot(f_samp / 1e6, P_samp, lw=0.8)
axs[1].set_xlim(0, 4); axs[1].set_ylim(-80, 2)
axs[1].axvspan(2, 4, color="g", alpha=0.15, label="aliased band -> low-IF [2,4] MHz")
axs[1].axvline(2.6, color="r", ls="--", lw=0.8); axs[1].axvline(3.8, color="r", ls="--", lw=0.8)
axs[1].set_title("After bandpass sampling @ fs = 8 MHz (n = 602, no aliasing into [0,2) MHz)")
axs[1].set_ylabel("dB"); axs[1].legend(loc="upper right"); axs[1].grid(alpha=0.3)
Pc = np.abs(np.fft.fft(bb_iq * np.hanning(Ns))) ** 2
Pc = 10 * np.log10(Pc / np.max(Pc) + 1e-12)
axs[2].plot(np.fft.fftshift(fcfft) / 1e6, np.fft.fftshift(Pc), lw=0.8)
axs[2].set_xlim(-4, 4); axs[2].set_ylim(-80, 2)
axs[2].set_title("After DDC (mix 3 MHz) + lowpass -> complex baseband"); axs[2].set_xlabel("MHz"); axs[2].set_ylabel("dB")
axs[2].grid(alpha=0.3)
fig.tight_layout(); fig.savefig(OUT / "bandpass_fold.png", dpi=130); plt.close(fig)

# =====================================================================
# 实验 3: 噪声折叠 —— 前级 BPF 的必要性量化
# =====================================================================
print()
print("=" * 72)
print("Experiment 3: noise folding (with/without front-end bandpass filter)")
print("=" * 72)
# 40 MHz 连续宽带噪声: 先生成基带 [0.05, 20] MHz 再调制到 fc (搬移到 [2385, 2425] MHz)
nbb = band_limited_noise(N, fs_hi, (0.05e6, 20e6), rng)
nb = nbb * np.cos(2 * np.pi * fc * t)                    # 占据 [2385, 2425] MHz, 谱平坦
sig_power = np.mean(x ** 2)
# 缩放: 噪声在信号带 [fL,fH] 内 (占噪声总宽 2/40) 的功率 = 信号功率 -> 原始带内 SNR = 0 dB
noise_scaled = nb * np.sqrt(sig_power / (np.mean(nb ** 2) * (B / 40e6)))
xn = x + noise_scaled

def inband_power_after_sampling(sig):
    s = sig[::dec]
    Xs = np.fft.rfft(s * np.hanning(len(s)))
    f = np.fft.rfftfreq(len(s), 1 / fs)
    m = (f >= 2.1e6) & (f <= 3.9e6)
    return np.sum(np.abs(Xs[m]) ** 2)

p_sig_only = inband_power_after_sampling(x)
p_no_bpf = inband_power_after_sampling(xn)

Xf = np.fft.rfft(xn)
ff = np.fft.rfftfreq(N, 1 / fs_hi)
bpf_mask = (ff >= fL) & (ff <= fH)
Xf[~bpf_mask] = 0
xn_bpf = np.fft.irfft(Xf, N)
p_with_bpf = inband_power_after_sampling(xn_bpf)

rise_no = 10 * np.log10(p_no_bpf / p_sig_only)
rise_yes = 10 * np.log10(p_with_bpf / p_sig_only)
print("  scenario: flat wideband noise 40 MHz (fc±20 MHz), original in-band SNR = 0 dB, fs = 8 MHz")
print(f"  without front-end BPF: in-band power (signal + folded noise) relative to pure signal {rise_no:+6.2f} dB "
      f"(theory: real sampling receives positive-band + mirror-band folds, in-band noise ≈ 10·S -> +10.4 dB)")
print(f"  with front-end BPF:  in-band power relative to pure signal {rise_yes:+6.2f} dB "
      f"(theory: only in-band positive-band + mirror-band folds remain, in-band noise ≈ 2·S -> +4.8 dB)")
print("  conclusion: front-end BPF is a prerequisite for bandpass sampling —— the assumption holds; out-of-band folded noise must be reduced from ~10·S to ~2·S")

fig, axs = plt.subplots(2, 1, figsize=(9, 6.5))
_, Pn1 = db_spectrum(xn[::dec], fs)
_, Pn2 = db_spectrum(xn_bpf[::dec], fs)
axs[0].plot(f_samp / 1e6, Pn1, lw=0.8)
axs[0].set_xlim(0, 4); axs[0].set_ylim(-80, 2)
axs[0].set_title(f"Sampled WITHOUT front-end BPF: noise folds everywhere (+{rise_no:.1f} dB in-band)")
axs[0].set_ylabel("dB"); axs[0].grid(alpha=0.3)
axs[1].plot(f_samp / 1e6, Pn2, lw=0.8)
axs[1].set_xlim(0, 4); axs[1].set_ylim(-80, 2)
axs[1].axvspan(2, 4, color="g", alpha=0.15)
axs[1].set_title(f"Sampled WITH front-end BPF ({rise_yes:+.1f} dB in-band, only mirror-band fold)")
axs[1].set_xlabel("MHz"); axs[1].set_ylabel("dB"); axs[1].grid(alpha=0.3)
fig.tight_layout(); fig.savefig(OUT / "bandpass_noise.png", dpi=130); plt.close(fig)

# =====================================================================
# 实验 4: 孔径抖动 —— 直接 RF 采样的时钟指标
# =====================================================================
print()
print("=" * 72)
print("Experiment 4: aperture-jitter SNR ceiling for direct 2.4 GHz sampling")
print("=" * 72)
sigma_j = np.logspace(-13, -10, 50)                     # 0.1 ps .. 100 ps
snr_j = -20 * np.log10(2 * np.pi * fc * sigma_j)
snr_req = 26.0                                           # 802.15.4 链路需求 (假设: -85 dBm 灵敏度, 2 MHz BW)
for sj in (1e-12,):
    print(f"  σj = {sj*1e12:.0f} ps -> SNR ceiling { -20*np.log10(2*np.pi*fc*sj):5.1f} dB (requirement ≈ {snr_req:.0f} dB)")
print("  conclusion: 1 ps jitter leaves only ~36 dB SNR ceiling, only ~10 dB margin over the 26 dB requirement;")
print("        direct RF sampling needs <1 ps-class clock —— a heavy analog design burden, another reason to choose zero-IF architecture")

fig, ax = plt.subplots(figsize=(8, 5))
ax.plot(sigma_j * 1e12, snr_j, lw=1.5)
ax.axhline(snr_req, color="r", ls="--", lw=1, label=f"required SNR ≈ {snr_req:.0f} dB (802.15.4 link budget)")
ax.axvline(1, color="g", ls=":", lw=1, label="1 ps")
ax.set_xscale("log"); ax.set_xlabel("rms aperture jitter σj (ps)"); ax.set_ylabel("SNR ceiling (dB)")
ax.set_title(f"Aperture jitter SNR ceiling, sampling carrier @ {fc/1e6:.0f} MHz\nSNR = -20·log10(2π·fc·σj)")
ax.legend(); ax.grid(True, which="both", alpha=0.3)
fig.tight_layout(); fig.savefig(OUT / "bandpass_jitter.png", dpi=130); plt.close(fig)

print()
print("figure output: " + str(OUT))
