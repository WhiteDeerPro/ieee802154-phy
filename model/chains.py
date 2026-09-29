# -*- coding: utf-8 -*-
"""
chains —— 链路库: 把制式原语组装成可运行的通信链路
====================================================

每个 ``*_stages()`` / ``link()`` 返回 ``baseband.link`` 的阶段或链路 ——
「码流 → 波形 → 信道 → 恢复码流」的完整配方。run 脚本只负责**挑配方、
扫参数、喂 measure**, 不再重写帧循环与同步逻辑。

802.15.4 (O-QPSK + DSSS) 配方:

  ``tx_stages()``   发送段:  payload → 组帧 → 扩频 → 成形        (观测点 payload/bits/symbols/chips/tx)
  ``rx_stages()``   接收段:  rx → [校正] → MF → 同步 → 采样 → [均衡] → 解扩 → [解帧]
  ``link()``        完整链:  tx_stages + impairments + rx_stages
  ``simulate()``    一站式:  跑一帧并返回 ``Signal``

换制式 = 在本文件另写一组同形状的函数 (例: ``qam_link()`` —— 无扩频则跳过
``stage_spread``, 有编码则加 ``stage_fec``), ``baseband.link`` / ``measure`` /
``visualize`` 三层均无需改动。

阶段是普通函数 ``fn(sig) -> None``, 可在链上任意位置插入或替换::

    from baseband import impairments as imp
    from baseband.link import probe

    chain = chains.link(
        impairments=[imp.multipath_stage([1, .5], [0, .5]),
                     imp.cfo_stage(50e3),
                     chains.awgn(-1.0, rng)],
        cfo="two_stage", dc_iq=True, deframe=True)
    sig = chain.run(payload=psdu)
    print(measure.format_report(measure.link_report(sig)))
"""

import numpy as np

import measure
import phy_802154 as phy
from baseband import impairments as imp
from baseband.link import Chain, Signal, probe, stage
from phy_802154 import (CHIP, PREAMBLE_SYMS, SPS, chip_peak_index,
                        known_preamble_chips, preamble_chips, rx_deframe_symbols,
                        sample_chips, tx_frame_bytes, build_ppdu, ppdu_symbols,
                        tx_symbols)

# SHR = 前导 (4 B = 8 符号) + SFD (1 B = 2 符号); 解帧自 PHR 符号起
SHR_SYMBOLS = 10
# 半正弦脉冲能量 Σh² = sps/2 —— AWGN 与均衡噪声方差都以此标定
PULSE_ENERGY = float(np.sum(phy.half_sine(SPS) ** 2))


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def symbols_to_bits(symbols) -> np.ndarray:
    """符号 → 码流 (每符号 4 bit, 逐符号 LSB-first 展开)。

    与 ``measure.ber(..., bits_per_sym=4)`` 的比特误码统计等价 —— 未编码制式
    的「码流」就是「符号流」的比特展开。
    """
    s = np.asarray(symbols, dtype=np.int64).reshape(-1)
    out = np.empty(len(s) * 4, dtype=np.uint8)
    for k in range(4):
        out[k::4] = (s >> k) & 1
    return out


def eq_noise_var(chip_snr_db: float, sps: int = SPS) -> float:
    """码片 SNR (dB) → 码片软值域噪声方差 (与 MMSE 均衡的信道抽头尺度一致)。

    波形域 σw² = Σh² / 10^(SNR/10); 经匹配滤波后码片域方差 = σw² · Σh²。
    """
    es_h = float(np.sum(phy.half_sine(sps) ** 2))
    return (es_h / 10 ** (chip_snr_db / 10)) * es_h


def awgn(snr_db: float, rng=None):
    """802.15.4 标定的 AWGN 信道阶段 (pulse_energy = Σh² = sps/2)。

    传入同一 ``rng`` 可保证跨配置可复现 —— 扫参实验应固定它。
    """
    from baseband.channels import awgn_stage
    return awgn_stage(snr_db, PULSE_ENERGY, rng=rng)


# ---------------------------------------------------------------------------
# 发送段阶段
# ---------------------------------------------------------------------------

def stage_frame(whiten: bool = True, full: bool = True):
    """组帧: ``payload`` → ``bits`` + ``symbols``。

    full=True  完整链 (SHR + 白化(PHR+PSDU+FCS)), 与 RTL ``tx_framer`` 一致;
    full=False 简化链 (SHR + PHR + PSDU, 无白化/FCS) —— 仅用于 AWGN 性能评估。
               白化不改变 AWGN 下的误码率, 但对 RTL 比对必须用完整链。
    """
    def apply(sig):
        psdu = sig.payload
        syms = tx_symbols(psdu) if full else ppdu_symbols(psdu)
        sig.symbols = syms
        sig.bits = symbols_to_bits(syms)
    apply.__name__ = f"frame({'full' if full else 'simple'}"
    apply.__name__ += f"{',whiten' if whiten and full else ''})"
    apply.__wrapped__ = tx_frame_bytes if full else build_ppdu
    return apply


def stage_spread():
    """扩频: ``symbols`` → ``chips`` (16×32 码片表映射)。"""
    def apply(sig):
        sig.chips = phy.symbols_to_chips(sig.symbols)
    apply.__name__ = "spread(16x32)"
    return apply


def stage_modulate(sps: int = SPS):
    """O-QPSK 半正弦成形: ``chips`` → ``tx``。"""
    def apply(sig):
        sig.tx = phy.modulate_oqpsk(sig.chips, sps)
    apply.__name__ = f"oqpsk(sps={sps})"
    return apply


# ---------------------------------------------------------------------------
# 接收段阶段
# ---------------------------------------------------------------------------

def stage_cfo_correct(est: str = "two_stage", sps: int = SPS, lib=None,
                      f0_rough: int = 0):
    """CFO 估计 + 消旋 (作用于波形, 须在匹配滤波之前)。

    est: 'two_stage'     粗搜步进 + 前导相位差分精估 (无模糊, 默认)
         'periodogram'   周期图谱峰估计
         'preamble_diff' 纯前导相位差分估计
         'pattern'       用模式库 (baseband.patterns): **命中则直接取参数的旋性**,
                         失配才走 FFT 发现 —— "记忆/匹配由节点承担"的 ref 形态。
    lib / f0_rough  仅 est='pattern' 用: 模式库实例 + 帧起点粗估计
                         (模式库自己扫抽取相位, 只要 ±1 码片内即可)。
    估计值写入 ``meta['cfo_est']``, 消旋结果覆盖 ``rx``;
    est='pattern' 时额外写 ``meta['pattern'/'pattern_act'/'pattern_conf']``。
    """
    def apply(sig):
        mf0 = phy.matched_filter(sig.rx, sps)
        if est == "pattern" and lib is not None:
            p, s, act = lib.observe(mf0, f0_rough)
            f = p.cfo if p is not None else 0.0
            sig.meta["pattern"] = p
            sig.meta["pattern_act"] = act
            sig.meta["pattern_conf"] = float(s)
        elif est == "periodogram":
            f = phy.estimate_cfo_periodogram(sig.rx)
        elif est == "preamble_diff":
            f = phy.estimate_cfo_preamble_diff(sig.rx)        # 单值 (非元组)
        else:
            f, _ = phy.estimate_cfo_fine(sig.rx, phy.estimate_cfo_coarse(mf0))
        sig.meta["cfo_est"] = float(f)
        sig.rx = phy.correct_cfo(sig.rx, f)
    apply.__name__ = f"cfo_est+derotate({est})"
    return apply


def stage_matched_filter(sps: int = SPS):
    """匹配滤波: ``rx`` → ``meta['mf']`` (接收机内部量, 不作为链路观测点)。"""
    def apply(sig):
        sig.meta["mf"] = phy.matched_filter(sig.rx, sps)
    apply.__name__ = "matched_filter"
    return apply


def stage_sync(mode: str = "honest", win: int = 64, sps: int = SPS):
    """前导同步: ``meta['mf']`` → ``meta['sync']`` (帧起点对齐点) + ``meta['sync_fail']``。

    mode: 'honest' 前导模板全搜索 (真实同步器)
          'genie'  真值 p=0 —— 隔离同步器, 只测数据通路容限
          'window' 限定 [−win, +win] 样本内搜索 (assisted 同步, 只在帧首附近找)
    """
    def apply(sig):
        mf = sig.meta["mf"]
        if mode == "genie":
            p = 0
        else:
            k, _, corr = phy.correlate_preamble(mf, return_corr=True)
            if mode == "window":
                lo, hi = max(0, -win), min(len(corr) - 1, win)
                p = lo + int(np.argmax(corr[lo:hi + 1]))
            else:
                p = k
            sig.meta["corr"] = corr
        sig.meta["sync"] = p
        # 帧尾越界 = 同步误判 (帧被截断); 统计口径由实验层决定
        # (measure.frame_bit_errors 默认按整帧计错, 与既有脚本一致)
        n_sym = len(sig.symbols) if sig.symbols is not None else 0
        sig.meta["sync_fail"] = bool(
            n_sym and chip_peak_index(p, n_sym * 32 - 1, sps) >= len(mf))
    apply.__name__ = f"sync({mode})"
    return apply


def stage_sample_chips(derail: bool = True, sps: int = SPS):
    """定时采样: ``meta['mf']`` + ``meta['sync']`` → ``rx_chips``。

    derail=False 保留原始复数码片, 供 DC/IQ 校正先作用于未归位值。
    """
    def apply(sig):
        n_chips = len(sig.symbols) * 32
        sig.rx_chips = sample_chips(sig.meta["mf"], sig.meta["sync"], n_chips,
                                    sps, derail=derail)
    apply.__name__ = f"sample_chips{'[raw]' if not derail else ''}"
    return apply


def stage_dc_iq_correct(sps: int = SPS):
    """DC 偏移 / I/Q 不平衡联合 LS 校正 (前导码片为参考, 整帧应用)。

    估计值写入 ``meta['dc_iq']`` = (alpha, beta, d); 逆变换后重新归位奇数码片。
    """
    def apply(sig):
        p = sig.meta["sync"]
        mf = sig.meta["mf"]
        r_pre = phy.extract_preamble_chips(mf, p, sps=sps)
        s_pre = known_preamble_chips(derail=False)
        alpha, beta, d = phy.estimate_dc_iq(r_pre, s_pre)
        sig.meta["dc_iq"] = (alpha, beta, d)
        n_chips = len(sig.symbols) * 32
        w = phy.apply_dc_iq_correction(
            sample_chips(mf, p, n_chips, sps, derail=False), alpha, beta, d)
        m = np.arange(len(w))
        sig.rx_chips = np.where(m % 2 == 0, w, w * (-1j))
    apply.__name__ = "dc_iq_correct"
    return apply


def stage_equalize(n_taps: int, noise_var: float, sps: int = SPS):
    """多径均衡: 前导 LS 信道估计 → 频域 MMSE 均衡 (作用在 ``rx_chips`` 上)。

    均衡补偿了信道相位, 之后应配相干解扩 (``stage_despread(coherent=True)``)。
    """
    def apply(sig):
        chips_pre = sig.rx_chips[:PREAMBLE_SYMS * 32]
        h = phy.estimate_channel_ls(chips_pre, preamble_chips(), n_taps)
        sig.meta["channel"] = h
        sig.rx_chips = phy.equalize_mmse(sig.rx_chips, h, noise_var)
    apply.__name__ = f"equalize({n_taps}tap,mmse)"
    return apply


def stage_despread(coherent: bool = False):
    """解扩 + 星座软值: ``rx_chips`` → ``rx_soft`` / ``soft_ref`` / ``rx_symbols``。

    coherent=False  非相干 |·| 检测 (免疫相位旋转, 有 CFO 残余时用)
    coherent=True   相干实部检测 (相位已由均衡/消旋对齐时用)

    星座软值以**发送符号对应的 PN 序列**为参考做复相关, 理想值 = 1+0j,
    因此星座点偏离原点的程度就是 EVM, 相位旋转 (CFO 残余) 与幅度收缩
    (定时偏差) 都在图上一目了然。
    """
    def apply(sig):
        chips = sig.rx_chips
        n_sym = len(chips) // 32
        sig.rx_symbols = (phy.despread_coherent(chips) if coherent
                          else phy.despread_chips(chips)).astype(int)[:n_sym]
        ref = sig.symbols[:n_sym] if sig.symbols is not None else sig.rx_symbols
        sig.rx_soft = measure.soft_values_from_chips(
            chips[:n_sym * 32], CHIP, ref)
        sig.soft_ref = np.ones(n_sym, dtype=complex)
        sig.rx_bits = symbols_to_bits(sig.rx_symbols)
    apply.__name__ = f"despread({'coherent' if coherent else 'noncoherent'})"
    return apply


def stage_deframe(shr_symbols: int = SHR_SYMBOLS):
    """解帧: ``rx_symbols`` → ``rx_payload`` (+ ``meta['fcs_ok']`` / ``meta['phr_len']``)。

    去白化 (PN9 自逆) → PHR 长度 → PSDU → FCS 校验; 与 RTL ``rx_deframer`` 功能镜像。
    """
    def apply(sig):
        psdu, ok, phr_len = rx_deframe_symbols(sig.rx_symbols[shr_symbols:])
        sig.rx_payload = psdu
        sig.meta["fcs_ok"] = bool(ok)
        sig.meta["phr_len"] = phr_len
    apply.__name__ = "deframe"
    return apply


# ---------------------------------------------------------------------------
# 预置配方
# ---------------------------------------------------------------------------

def tx_stages(whiten: bool = True, full: bool = True, sps: int = SPS):
    """802.15.4 发送段阶段列表 (可选地插入自定义阶段, 见 ``baseband.link.stage``)。"""
    return [stage_frame(whiten=whiten, full=full),
            stage_spread(),
            stage_modulate(sps=sps)]


def rx_stages(sync: str = "honest", win: int = 64, cfo=None, dc_iq: bool = False,
              n_taps: int = 0, noise_var: float = 1.0, coherent: bool = False,
              deframe: bool = False, sps: int = SPS, pattern_lib=None,
              f0_rough: int = 0):
    """802.15.4 接收段阶段列表。

    各选项对应一个可独立开关的接收机功能, 顺序即信号流顺序:

      cfo       None | 'two_stage' | 'periodogram' | 'preamble_diff' | 'pattern'
                非 None 时在匹配滤波前做 CFO 估计 + 消旋
                ('pattern' 需配 pattern_lib: 模式库提供旋性, 见 baseband.patterns)
      sync      'honest' | 'genie' | 'window'   同步策略 (见 stage_sync)
      dc_iq     True 时做 DC/IQ 联合 LS 校正 (前导为参考)
      n_taps    >0 时做前导 LS 信道估计 + MMSE 均衡 (多径场景)
      coherent  解扩检测方式 (有均衡/消旋对齐时用 True)
      deframe   True 时解白化 + PHR + FCS 校验, 填 rx_payload
    """
    stages = []
    if cfo:
        stages.append(stage_cfo_correct(cfo, sps=sps, lib=pattern_lib,
                                        f0_rough=f0_rough))
    stages.append(stage_matched_filter(sps=sps))
    stages.append(stage_sync(sync, win=win, sps=sps))
    stages.append(stage_sample_chips(derail=not dc_iq, sps=sps))
    if dc_iq:
        stages.append(stage_dc_iq_correct(sps=sps))
    if n_taps:
        stages.append(stage_equalize(n_taps, noise_var, sps=sps))
    stages.append(stage_despread(coherent=coherent))
    if deframe:
        stages.append(stage_deframe())
    return stages


def link(impairments=(), *, full: bool = True, whiten: bool = True,
         name: str = "802.15.4", **rx_kw) -> Chain:
    """完整链路 = 发送段 + 损伤段 + 接收段。

    impairments 是 ``baseband.impairments`` 的 ``*_stage()`` 列表, 列表顺序即
    损伤作用顺序 (建议 多径 → AWGN → CFO → 定时 → DC/IQ → 镜像 → 量化 → SFO)。
    full/whiten 选发送链规模 (见 ``stage_frame``); rx_kw 透传给 ``rx_stages``。
    """
    c = Chain(name=name)
    c.then_tx(*tx_stages(whiten=whiten, full=full))
    c.then_channel(*impairments)
    c.then_rx(*rx_stages(**rx_kw))
    return c


def simulate(payload, impairments=(), *, full: bool = True, whiten: bool = True,
             **rx_kw) -> Signal:
    """一站式: 组链并跑一帧, 返回 ``Signal`` (所有观测点 + meta)。

    等价于 ``link(impairments, **rx_kw).run(payload=payload)``。
    """
    return link(impairments, full=full, whiten=whiten, **rx_kw).run(payload=payload)
