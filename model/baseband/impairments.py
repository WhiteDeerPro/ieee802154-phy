# -*- coding: utf-8 -*-
"""
baseband.impairments —— 信道损伤注入 (制式无关)

作用在复基带采样流上; 默认采样率/过采样为常见基带取值, 调用方可按需覆盖。
作用顺序约定: 多径 -> CFO -> 定时偏差 (发送/信道/接收采样钟综合等效)。
"""

import numpy as np

from .link import stage

FS = 16e6   # 默认复基带采样率
SPS = 8     # 默认每码片采样数


def add_cfo(x, cfo_hz, fs=FS, t0=0.0):
    """载波频偏: 复指数旋转 x[n] *= exp(j·2π·Δf·(n−t0)/fs)。

    t0 为相位零点 (采样序号; 默认 0 —— 从起点起算, 与原行为一致)。
    需要多段信号的**初始相位对齐**时传入: 例如眼图对比中让各调制的相位漂移
    从第一个叠加窗起算, 否则相同的漂移会落在不同的 cos 区间上, 幅度损失
    相差数倍而无法并排比较。
    """
    n = np.arange(len(x)) - t0
    return x * np.exp(2j * np.pi * cfo_hz * n / fs)


def add_timing_offset(x, eps_chips, sps=SPS):
    """分数定时偏差 (码片), FFT 域理想分数延迟。eps > 0 表示接收采样点滞后。"""
    if eps_chips == 0:
        return x
    N = len(x)
    f = np.fft.fftfreq(N)
    return np.fft.ifft(np.fft.fft(x) * np.exp(-2j * np.pi * f * eps_chips * sps))


def add_multipath(x, gains, delays_chips, sps=SPS):
    """抽头延迟线多径; delays_chips 可为分数 (FFT 延迟); gains 可复 (Rayleigh)。"""
    y = np.zeros_like(x)
    for g, d in zip(gains, delays_chips):
        y += g * add_timing_offset(x, d, sps)
    return y


# 预置多径场景 (增益归一化使总平均功率 ≈ 1; 延迟单位 = 码片)
SCENARIOS = {
    "awgn":        dict(gains=[1.0], delays=[0.0]),
    "los_2tap":    dict(gains=[0.9487, 0.3162], delays=[0.0, 0.25]),   # 二径, 功率比 1:0.111
    "indoor_3tap": dict(gains=[0.8018, 0.4809, 0.2886], delays=[0.0, 0.5, 1.0]),  # 指数衰减
    "bad_3tap":    dict(gains=[0.7071, 0.7071, 0.0], delays=[0.0, 0.5, 0.0]),     # 等幅双径 (深衰落)
}


def rayleigh_taps(n_taps, delay_max_chips, rms_chips=0.5, rng=None, los_first=True):
    """生成随机复抽头 (Rayleigh 幅度 + 均匀相位), 指数延迟功率剖面, 总功率归一化 ≈ 1。"""
    if rng is None:
        rng = np.random.default_rng()
    delays = np.sort(rng.uniform(0, delay_max_chips, n_taps))
    if los_first:
        delays[0] = 0.0
    power = np.exp(-delays / rms_chips) if rms_chips > 0 else np.ones(n_taps)
    gains = (rng.standard_normal(n_taps) + 1j * rng.standard_normal(n_taps)) * np.sqrt(power)
    gains = gains / np.sqrt(np.sum(np.abs(gains) ** 2))
    return gains, delays


def add_dc_offset(x, dc_i, dc_q):
    """DC 偏移 / LO 泄漏 (相对信号 RMS=1)。"""
    return x + complex(dc_i, dc_q)


def add_iq_imbalance(x, gain_imb_db, phase_imb_deg):
    """I/Q 不平衡: I 轨增益 g, Q 轨混入 sinφ·I 且缩放 cosφ。gain_imb_db > 0 表 I 增益偏大。"""
    g = 10 ** (gain_imb_db / 20)
    p = np.radians(phase_imb_deg)
    I = x.real * g
    Q = np.sin(p) * x.real + np.cos(p) * x.imag
    return I + 1j * Q


def quantize(x, bits, full_scale=1.0):
    """ADC 均匀量化 (mid-tread); bits=None 或 ≥32 时不量化 (浮点对照)。"""
    if bits is None or bits >= 32:
        return x
    step = 2.0 * full_scale / (2 ** bits)
    q = np.round(np.asarray(x) / step) * step
    return np.clip(q, -full_scale, full_scale - step)


def add_sfo(x, ppm):
    """采样率偏差 (SFO): 接收以 fs·(1+ppm/1e6) 采样, 线性插值重采样。"""
    if ppm == 0:
        return x
    x = np.asarray(x)
    n = np.arange(len(x))
    tn = n / (1.0 + ppm / 1e6)
    yr = np.interp(tn, np.arange(len(x)), x.real)
    yi = np.interp(tn, np.arange(len(x)), x.imag)
    return yr + 1j * yi


def add_jitter(x, rms_ps, fs=FS, rng=None):
    """采样孔径抖动 (aperture jitter): 采样时刻的**逐样本随机**偏移。

    与 SFO 的区别: SFO 是系统性采样率偏差 (误差随 n 线性累积, 帧尾最差);
    jitter 是随机误差 (不累积, 但抬高底噪)。

    一阶模型 − 时间误差 Δt 产生电压误差 Δv ≈ (dv/dt)·Δt:
        x_jit[n] ≈ x[n] + Δt_n · x'(n·T),   Δt_n ~ N(0, σ_t²)
    对满幅正弦, 由此得解析上限 ``SNR_jitter = −20·log10(2π·f·σ_t)`` (ADI MT-007):
    每倍频程恶化 6 dB、与量化位数无关 —— 这正是直接射频采样难做的原因
    (参见 bandpass_sampling_demo 的孔径抖动实验)。

    rms_ps: 抖动 RMS, 单位 ps。采样率越高、信号斜率越大, 同样 σ_t 伤害越大。
    """
    if rms_ps == 0:
        return x
    if rng is None:
        rng = np.random.default_rng()
    x = np.asarray(x, dtype=complex)
    dt = rng.standard_normal(len(x)) * (rms_ps * 1e-12)
    return x + dt * (np.gradient(x) * fs)          # (dv/dt)·Δt


def add_image_interference(x, irr_db, f_if_hz=0.0, fs=FS, phase=0.0):
    """镜像频率干扰 (CW 单音): 在 −f_if 处注入干扰。

    与 ``add_iq_imbalance`` 的区别: 本函数是**外部干扰源**落在镜像频率上,
    而 IQ 不平衡是本机自身的镜像产生机制。零中频接收机下变频时, 镜像频率上的
    干扰会与有用信号落到同一基带上, 只能靠镜像抑制比 (IRR) 衰减。

    irr_db   干扰相对有用信号的抑制量 (dB): 0 = 等功率, 越大干扰越弱
    f_if_hz  干扰相对本振的偏移 (Hz), 注入在 −f_if 侧; 0 = 直流镜像
    """
    x = np.asarray(x, dtype=complex)
    n = np.arange(len(x))
    sig_rms = np.sqrt(np.mean(np.abs(x) ** 2) + 1e-30)
    amp = sig_rms * 10 ** (-irr_db / 20)
    return x + amp * np.exp(-2j * np.pi * f_if_hz * n / fs + 1j * phase)


# ---------------------------------------------------------------------------
# 阶段工厂: 把上面的纯函数接进 baseband.link 的链路编排
# ---------------------------------------------------------------------------
# 约定: 阶段作用于 sig.rx (接收波形), 列表顺序 = 损伤作用顺序。
# 建议顺序 多径 → AWGN → CFO → 定时 → DC/IQ → 镜像 → 量化 → SFO;
# 具体由制式配方的 impairments=[...] 列表决定 (见 phy_802154 / run 脚本)。
#
# 新增一种损伤只需:
#   1) 写纯函数 add_xxx(x, ...) -> x'
#   2) 加一行 xxx_stage(...) -> stage(add_xxx, name=..., ...)
# 之后即可在任何链路里以 impairments=[imp.xxx_stage(...)] 使用。

def cfo_stage(hz, fs=FS):
    """载波频偏: 星座图整体旋转 (不纠正则星座点绕原点打转)。"""
    return stage(add_cfo, name=f"cfo({hz / 1e3:g}kHz)", cfo_hz=hz, fs=fs)


def timing_stage(eps_chips, sps=SPS):
    """分数定时偏差: 采样偏离峰值 → 星座点收缩并带相位旋转, 眼图闭合。"""
    return stage(add_timing_offset, name=f"timing({eps_chips:g}chip)",
                 eps_chips=eps_chips, sps=sps)


def multipath_stage(gains, delays_chips, sps=SPS):
    """多径: 抽头延迟线 (频率选择性衰落, 码间串扰)。"""
    return stage(add_multipath, name=f"multipath({len(gains)}tap)",
                 gains=gains, delays_chips=delays_chips, sps=sps)


def dc_stage(dc_i, dc_q):
    """零偏 / LO 泄漏: 星座图整体平移。"""
    return stage(add_dc_offset, name=f"dc({dc_i:g},{dc_q:g})",
                 dc_i=dc_i, dc_q=dc_q)


def iq_stage(gain_imb_db, phase_imb_deg):
    """I/Q 不平衡: 星座图出现镜像副本, EVM 恶化。"""
    return stage(add_iq_imbalance, name=f"iq({gain_imb_db:g}dB,{phase_imb_deg:g}deg)",
                 gain_imb_db=gain_imb_db, phase_imb_deg=phase_imb_deg)


def image_stage(irr_db, f_if_hz=0.0, fs=FS):
    """镜像频率干扰: 外部干扰源落在镜像频率。"""
    return stage(add_image_interference, name=f"image(IRR={irr_db:g}dB)",
                 irr_db=irr_db, f_if_hz=f_if_hz, fs=fs)


def quantize_stage(bits, full_scale=1.0):
    """ADC 量化: 接收采样位宽不足 → 量化噪声抬高底噪。"""
    return stage(quantize, name=f"adc({bits}bit)", bits=bits, full_scale=full_scale)


def sfo_stage(ppm):
    """采样率偏差: 帧内定时漂移, 帧尾星座图逐渐恶化。"""
    return stage(add_sfo, name=f"sfo({ppm:g}ppm)", ppm=ppm)


def jitter_stage(rms_ps, fs=FS):
    """采样孔径抖动: 逐样本随机的采样时刻偏移 —— 抬高底噪, 不随帧长累积,
    与 SFO (系统性、累积性) 互补。"""
    return stage(add_jitter, name=f"jitter({rms_ps:g}ps)", rms_ps=rms_ps, fs=fs)


def scenario_stage(name, sps=SPS):
    """按预置场景名生成多径阶段 (见 SCENARIOS)。"""
    sc = SCENARIOS[name]
    return multipath_stage(sc["gains"], sc["delays"], sps=sps)


def custom_stage(fn, name=None, **kw):
    """把任意 ``fn(x, **kw) -> x'`` 接入链路 —— 为尚未建模的损伤留的通用入口。"""
    return stage(fn, name=name or getattr(fn, "__name__", "custom"), **kw)
