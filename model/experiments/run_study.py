# -*- coding: utf-8 -*-
"""
run_study.py —— 自由组合实验: 调制 × 成形滤波器 × 非理想效应
================================================================
每个组合是一个**实验实例**, 产出到 ``out/<实例名>/`` (规范见 ``model/instance.py``)。
改下面的 ``STUDY`` 即可增减实验 —— 这是本脚本唯一需要动的地方。

分组:
  mod_*    调制方式对比   (BPSK / QPSK / 16-QAM / 正弦脉冲-PSK)
  pulse_*  成形滤波器对比 (升余弦 / 半正弦 / 整数周期正弦)
  eff_*    非理想效应对比 (理想 / AWGN / CFO / 抖动 / 多径)
  std_*    制式链           (802.15.4 O-QPSK+DSSS, 含扩频码自相关)

关于「失同步」: CFO 表征的是**载波**失同步 (相位旋转); 定时失同步由 SFO/jitter
表征。真正把频率/相位锁回去要靠环路 (PLL / Costas / 早迟门), 本项目只在带前导的
制式链 (``chains``) 里做了前导差分估计 —— 环路实现留作 RTL 课题, 此处仅作表征。

运行: python model/experiments/run_study.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

import chains
import instance
import measure
import phy_802154 as phy
from baseband import channels as ch, impairments as imp, modulation as mod

FS = 16e6
SPS = 10                      # 每符号采样数 (= sine 成形的每周期点数)
N_SYM = 400
SNR_DB = 16.0
CFO_HZ = 200.0                # 见下: 帧长 250 us 下 200 Hz 累积 0.31 rad, 可见而不毁判决
JITTER_PS = 20.0
SEED = 2026

# 各非理想效应的注入函数 (作用在**发送波形**上, 未列出的即不注入)
EFFECTS = {
    "awgn":      lambda w, rng: ch.awgn_waveform(w, SNR_DB, rng),
    "cfo":       lambda w, rng: imp.add_cfo(w, CFO_HZ),
    "jitter":    lambda w, rng: imp.add_jitter(w, JITTER_PS, FS, rng),
    "multipath": lambda w, rng: imp.add_multipath(w, [1.0, 0.5], [0.0, 1.5]),
}

PULSES = {
    "rc":        lambda sps: mod.raised_cosine(beta=0.35, sps=sps),
    "half_sine": lambda sps: mod.half_sine(sps),
    "sine":      lambda sps: mod.sine_burst(cycles=1, sps_per_cycle=sps),
}

# (实例名, 标题, scheme, order, pulse, [效应...])
STUDY = [
    # ---- 调制方式对比 (固定升余弦 + AWGN) ----
    ("mod_bpsk",     "BPSK",        "psk", 2,  "rc", ["awgn"]),
    ("mod_qpsk",     "QPSK",        "psk", 4,  "rc", ["awgn"]),
    ("mod_16qam",    "16-QAM",      "qam", 16, "rc", ["awgn"]),
    ("mod_sine_psk", "正弦脉冲-PSK", "psk", 4,  "sine", ["awgn"]),
    # ---- 成形滤波器对比 (固定 QPSK + AWGN) ----
    ("pulse_half_sine", "半正弦成形",   "psk", 4, "half_sine", ["awgn"]),
    ("pulse_sine",      "整数周期正弦", "psk", 4, "sine",      ["awgn"]),
    # ---- 非理想效应对比 (固定 QPSK + 升余弦) ----
    ("eff_ideal",   "理想信道",     "psk", 4, "rc", []),
    ("eff_awgn",    f"AWGN {SNR_DB:g} dB", "psk", 4, "rc", ["awgn"]),
    ("eff_cfo",     f"CFO {CFO_HZ:g} Hz",  "psk", 4, "rc", ["awgn", "cfo"]),
    ("eff_jitter",  f"抖动 {JITTER_PS:g} ps", "psk", 4, "rc", ["awgn", "jitter"]),
    ("eff_multipath", "多径 (2 径)", "psk", 4, "rc", ["awgn", "multipath"]),
]


def mod_name(scheme, order):
    if scheme == "psk":
        return {2: "BPSK", 4: "QPSK", 8: "8PSK"}.get(order, f"{order}PSK")
    return f"{order}{scheme.upper()}"


def unit_energy(h):
    """归一到单位能量 —— 不同成形的接收幅度才可比。"""
    return h / np.sqrt(np.sum(np.asarray(h) ** 2))


def run_case(name, title, scheme, order, pulse_kind, effects):
    """跑一个组合, 产出实例目录。"""
    rng = np.random.default_rng(SEED)
    const = mod.constellation(scheme, order)
    n_bit = int(round(np.log2(const.size)))
    bits = rng.integers(0, 2, size=N_SYM * n_bit)
    syms = mod.map_symbols(bits.reshape(-1, n_bit) @ (1 << np.arange(n_bit)), const)

    pulse = unit_energy(PULSES[pulse_kind](SPS))
    tx, _ = mod.pulse_shape(syms, pulse, SPS)
    peak = len(pulse) - 1        # 成形 + 匹配滤波两次卷积的支撑中心

    # 信道: 逐层注入 (列表顺序即作用顺序)
    wave = tx
    labels = []
    for e in effects:
        wave = EFFECTS[e](wave, np.random.default_rng(SEED + len(labels) + 1))
        labels.append(e)

    mf = mod.matched_filter(wave, pulse)
    soft = mf[peak::SPS][:len(syms)]
    dec = const[np.argmin(np.abs(soft[:, None] - const[None, :]), axis=1)]
    ser = float(np.mean(dec != syms))
    evm = measure.evm(soft, syms)
    snr_est = measure.snr_m2m4(soft)

    inst = instance.Instance(f"study/{name}", fs=FS,
                             title=f"{title} —— {mod_name(scheme, const.size)}"
                                   f" @ {pulse_kind} 成形"
                                   + (f", {', '.join(labels)}" if labels else ", 无损伤"))
    inst.bits(bits)
    inst.symbols(syms)
    inst.waveform({
        "① 发射波形 (成形后)": tx,
        "② 信道输出 (含损伤: " + (", ".join(labels) or "无") + ")": wave,
        "③ 接收机: 匹配滤波输出": mf[:len(tx)],
    }, n_show=8 * SPS, symbol_period=SPS)
    inst.spectrum(wave)
    inst.energy(wave)
    inst.filters({f"成形脉冲: {pulse_kind}": pulse}, fs=FS)
    if "awgn" in effects:
        clean = tx
        for e in effects:
            if e != "awgn":
                clean = EFFECTS[e](clean, np.random.default_rng(SEED + 9))
        inst.noise(clean, ch.awgn_waveform(clean, SNR_DB, np.random.default_rng(5)),
                   ch.awgn_band_limited(clean, SNR_DB, 2.0e6, FS,
                                        rng=np.random.default_rng(5)), fs=FS)
    inst.constellation(soft, ref=const)
    inst.eye(mf, period=SPS, first_peak=peak, n_traces=40, ideal=syms.real)
    inst.table("组合配置", [
        f"调制 {mod_name(scheme, const.size)} ({n_bit} bit/符号), 成形 {pulse_kind}, sps={SPS}",
        f"非理想效应: {', '.join(labels) if labels else '无 (理想信道)'}",
        f"符号数 {N_SYM}, 帧长 {len(tx)/FS*1e6:.1f} us, 符号率 {FS/SPS/1e3:g} ksym/s",
    ])
    inst.table("测量结果", [
        f"SER = {ser:.3e}", f"EVM = {evm:.3f} ({20*np.log10(max(evm,1e-12)):.1f} dB)",
        f"盲 SNR (M2M4 @ 符号采样点) = {snr_est:.1f} dB",
    ])
    if "cfo" in effects:
        drift = 2 * np.pi * CFO_HZ * len(tx) / FS
        inst.note(f"CFO {CFO_HZ:g} Hz 在 {len(tx)/FS*1e6:.0f} us 帧内累积 "
                  f"{drift:.2f} rad 相位旋转 —— 星座图呈扇形/环形扩散即为「载波失同步」"
                  f"的可视化。要把它拉回来需要环路 (PLL/Costas), 本项目只在带前导的"
                  f"制式链里做了前导差分估计。")
    if "jitter" in effects:
        inst.note(f"抖动 {JITTER_PS:g} ps 抬高底噪但不随帧长累积 (与 SFO 相反); "
                  f"解析上限 SNR = -20log10(2*pi*f*sigma_t), 每倍频程恶化 6 dB。")
    if "multipath" in effects:
        inst.note("多径 (2 径, 延迟 1.5 码片) 造成 ISI —— 眼图闭合、星座点径向弥散; "
                  "解多径需均衡 (见 run_multipath_eq)。")
    inst.note(f"眼图看的是**采样级**波形, 故 AWGN 下毛边明显是正常的 —— 判决时"
              f"符号级 SNR 高于此 (见测量结果), 单看眼图毛边不等于性能差。")
    inst.close()
    print(f"  ✓ {name:<18} {inst.title}   SER={ser:.2e}  "
          f"眼高={getattr(inst, 'eye_height', float('nan')):.3f}")


def run_std_chain():
    """制式链 (802.15.4 O-QPSK + DSSS): 含扩频码自相关与 PRN 正交性。"""
    psdu = bytes(np.random.default_rng(SEED).integers(0, 256, size=20).tolist())
    syms = phy.ppdu_symbols(psdu)
    chips = phy.symbols_to_chips(syms)
    tx = phy.modulate_oqpsk(chips)
    rx = phy.add_awgn(tx, 10.0, rng=np.random.default_rng(SEED + 7))

    inst = instance.Instance("study/std_oqpsk_dsss", fs=FS,
                             title="802.15.4 O-QPSK + DSSS 制式链 (chip SNR 10 dB)")
    inst.bits(chains.symbols_to_bits(syms), label="符号流按每符号 4 bit 展开")
    inst.symbols(syms)
    inst.code_autocorr(phy.CHIP[0], title="前导码片序列 CHIP[0] 的自相关")
    inst.waveform({
        "① 发射波形 (扩频 + O-QPSK 半正弦)": tx,
        "② 信道输出 (chip SNR 10 dB)": rx,
        "③ 接收机: 匹配滤波输出": phy.matched_filter(rx),
    }, n_show=8 * 32 * phy.SPS)
    inst.spectrum(rx)
    inst.energy(rx)
    inst.filters({"半正弦成形 (Σh²=4)": phy.half_sine(phy.SPS)}, fs=FS)
    inst.noise(tx, phy.add_awgn(tx, 10.0, rng=np.random.default_rng(3)),
               ch.awgn_band_limited(tx, 10.0, 2.0e6, FS, rng=np.random.default_rng(3)),
               fs=FS, n_show=6 * phy.SPS)
    mf = phy.matched_filter(rx)
    p, _ = phy.correlate_preamble(mf)
    inst.constellation(measure.soft_values_from_chips(
        phy.sample_chips(mf, p, len(syms) * 32), phy.CHIP, syms))

    # 16 个 PN 序列的准正交性 (互相关矩阵)
    G = phy.CHIP @ phy.CHIP.T
    off = np.abs(G - np.diag(np.diag(G)))
    inst.table("扩频码性质", [
        f"16 个 32 码片 PN 序列: 自相关 = {np.diag(G)[0]:.0f}",
        f"最大互相关 = {off.max():.0f}  (相对自相关 {off.max()/np.diag(G)[0]:.1%})",
        "-> 准正交, 16 路非相干解扩可区分 (扩频增益 10log10(32/4) = 9.03 dB)",
    ])
    inst.note("DSSS 的扩频增益把 chip 级的 SNR 抬到符号级: chip SNR 10 dB 时"
              "符号 SNR 约 25 dB, 所以眼图看着毛、判决却几乎无误 —— 两回事。")
    inst.close()
    print(f"  ✓ std_oqpsk_dsss    自相关主峰 {np.diag(G)[0]:.0f}, "
          f"最大互相关 {off.max():.0f}")


def main():
    print(f"自由组合实验: {len(STUDY) + 1} 个实例 -> out/study/\n")
    for case in STUDY:
        run_case(*case)
    run_std_chain()
    print(f"\n全部完成。实例目录: model/out/study/")


if __name__ == "__main__":
    main()
