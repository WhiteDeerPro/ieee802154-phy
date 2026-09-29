# -*- coding: utf-8 -*-
"""
run_link_report.py —— 一条链路的完整观测报告（「实验实例」规范示范）
=======================================================================
按 ``model/instance.py`` 的规范, 一次实验产出一个**自包含目录**
``out/link_report/``, 内含该链路的全套观测:

    基带码片段 / 符号 / 时域波形(基带码→发射→信道→接收) / 频谱 / 能量谱 /
    滤波器响应 / 加噪对照(全带 vs 带内) / 星座图 / 眼图 / report.md

链路: 基带码 → 符号映射 → 脉冲成形 → [信道: AWGN + CFO] → 匹配滤波 → 判决

**改参数**只动下面的 ``SPEC``。成形脉冲可选:
    "rc"        升余弦 (β 可调) —— 跨符号重叠, 频带受控
    "sine"      「乘法器输出」式: 一个符号含整数个正弦周期 (sps 点/周期)
    "half_sine" 半正弦 (与 802.15.4 同族)
三者都归一到单位能量, 便于横向比较。

运行: python model/experiments/run_link_report.py
输出: model/out/link_report/
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

import instance
import measure
from baseband import channels as ch, impairments as imp, modulation as mod

FS = 16e6

SPEC = dict(
    scheme="psk", order=4,      # 调制: psk/ask/qam × 阶数 (psk×4 = QPSK)
    pulse="rc", rolloff=0.35,    # 成形: "rc" | "sine" | "half_sine"
    sps=10,                      # 每符号采样数 (sine 时即每正弦周期点数)
    n_sym=400,                   # 符号数
    snr_db=16.0,                 # 信道 SNR (波形口径)
    band_bw_hz=2.0e6,            # 带限噪声的带宽 (对照全带白噪声)
    # 为何不默认加 CFO: 本实例是无前导的通用调制, 接收端没有可用的频偏估计参考;
    # 而 CFO 的伤害随帧长累积 —— 400 符号 @ sps=10 已跨 257 us, 1 kHz 就累积
    # 1.6 rad 相位漂移 (QPSK 判决直接崩)。带前导的制式 (如 802.15.4) 走 chains 库。
    cfo_hz=0.0,
    seed=2026,
)


def mod_name(scheme, order):
    """人读的调制名: psk/4 → QPSK, qam/16 → 16QAM …"""
    if scheme == "psk":
        return {2: "BPSK", 4: "QPSK", 8: "8PSK"}.get(order, f"{order}PSK")
    return f"{order}{scheme.upper()}"


def build_pulse(kind, sps, rolloff=0.35):
    """成形脉冲, 一律归一到**单位能量** —— 否则不同脉冲的接收幅度不可比。"""
    if kind == "rc":
        h = mod.raised_cosine(beta=rolloff, sps=sps)
    elif kind == "sine":
        h = mod.sine_burst(cycles=1, sps_per_cycle=sps)
    elif kind == "half_sine":
        h = mod.half_sine(sps)
    else:
        raise ValueError(f"未知成形 {kind!r}")
    return h / np.sqrt(np.sum(h ** 2))


def main():
    rng = np.random.default_rng(SPEC["seed"])
    seed = SPEC["seed"]
    sps, cfo = SPEC["sps"], SPEC["cfo_hz"]

    # ---------- 发送 ----------
    const = mod.constellation(SPEC["scheme"], SPEC["order"])
    n_bit = int(round(np.log2(const.size)))
    bits = rng.integers(0, 2, size=SPEC["n_sym"] * n_bit)
    idx = bits.reshape(-1, n_bit) @ (1 << np.arange(n_bit))     # 低比特先
    syms = mod.map_symbols(idx, const)

    pulse = build_pulse(SPEC["pulse"], sps, SPEC["rolloff"])
    tx, _ = mod.pulse_shape(syms, pulse, sps)
    # 脉冲峰值序号 (sine_burst 的峰不在中心, 故不用 (len-1)//2); 成形 + 匹配各一次
    peak = len(pulse) - 1        # 成形 + 匹配滤波两次卷积的支撑中心

    # ---------- 信道 ----------
    full = ch.awgn_waveform(tx, SPEC["snr_db"], np.random.default_rng(seed + 1))
    band = ch.awgn_band_limited(tx, SPEC["snr_db"], SPEC["band_bw_hz"], FS,
                                rng=np.random.default_rng(seed + 2))
    rx = imp.add_cfo(band, cfo) if cfo else band

    # ---------- 接收 ----------
    mf = mod.matched_filter(rx, pulse)
    soft = mf[peak::sps][:len(syms)]
    dec = const[np.argmin(np.abs(soft[:, None] - const[None, :]), axis=1)]
    ser = float(np.mean(dec != syms))
    evm = measure.evm(soft, syms)
    snr_est = measure.snr_m2m4(soft)   # 用在**符号采样点**; 过采样波形非恒模会严重低估

    # ---------- 实例产出 ----------
    inst = instance.Instance(
        "link_report", fs=FS,
        title=f"{mod_name(SPEC['scheme'], const.size)} @ {SPEC['pulse']} 成形链路观测")

    inst.bits(bits, label="基带码 (符号索引按低比特先展开)")
    inst.symbols(syms)
    inst.waveform({
        "① 发射波形 (成形后, 单位能量脉冲)": tx,
        "② 信道波形 (带内噪声 + 全带噪声 + CFO)": rx,
        "③ 接收机: 匹配滤波输出": mf[:len(tx)],
    }, n_show=6 * sps)

    inst.spectrum(rx, title=f"{inst.title} —— 接收信号幅度谱")
    inst.energy(rx)

    inst.filters({
        f"成形脉冲: {SPEC['pulse']} (sps={sps})": pulse,
        "对比: 半正弦": build_pulse("half_sine", sps),
        "对比: 正弦脉冲 (1 周期)": build_pulse("sine", sps),
    }, fs=FS)

    inst.noise(tx, full, band, fs=FS, n_show=6 * sps)

    inst.constellation(soft, ref=const)
    inst.eye(mf, period=sps, first_peak=peak, n_traces=40)

    # ---------- 分析结论 ----------
    inst.table("链路参数", [
        f"调制 {mod_name(SPEC['scheme'], const.size)} ({n_bit} bit/符号), "
        f"成形 {SPEC['pulse']}, sps={sps}, 符号数 {SPEC['n_sym']}",
        f"信道: AWGN {SPEC['snr_db']:g} dB (波形口径) + 带内噪声对照"
        + (f" + CFO {cfo/1e3:g} kHz" if cfo else ""),
        f"采样率 {FS/1e6:g} MHz, 符号率 {FS/sps/1e3:g} ksym/s, "
        f"帧长 {len(tx)/FS*1e6:.1f} us",
    ])
    inst.table("测量结果", [
        f"SER = {ser:.3e}   (判决: 最近星座点)",
        f"EVM = {evm:.3f}  ({20*np.log10(max(evm,1e-12)):.1f} dB)",
        f"盲 SNR 估计 (M2M4 @ 符号采样点) = {snr_est:.1f} dB  "
        f"(信道真值 {SPEC['snr_db']:g} dB)",
    ])
    inst.note(f"星座为 {const.size} 点 {mod_name(SPEC['scheme'], const.size)}, "
              f"每个符号携带 {n_bit} bit —— 星座图上应看到 {const.size} 个聚类。")
    inst.note("频谱给出幅度分布, 能量谱给出绝对功率密度 (dB/Hz) —— 纵轴口径"
              "不同, 不要混用。")
    inst.note("加噪对照: 全带白噪声的带外部分经匹配滤波后被滤掉, 故两者解调性能"
              "接近; 但**频谱图差别显著**, 且带通采样时只有带外噪声会折叠进带内。")
    inst.note("filters.png 对比三种成形: 升余弦频带受控但跨符号重叠; 正弦脉冲完全"
              "落在符号内 (无 ISI 尾巴) 但频谱旁瓣高; 半正弦介于两者之间。")
    path = inst.close()

    print(f"✓ 实例产出: {inst.dir}")
    print(f"  {len(inst.produced)} 个文件, report: {path.name}")
    print(f"  SER={ser:.3e}  EVM={evm:.3f}  SNR_est={snr_est:.1f} dB")


if __name__ == "__main__":
    main()
