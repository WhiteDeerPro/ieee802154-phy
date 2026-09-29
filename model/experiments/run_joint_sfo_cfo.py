# -*- coding: utf-8 -*-
"""
run_joint_sfo_cfo.py —— 同源 ε 联合验证: CFO 估计 → 消旋 + 逆 SFO 重采样
========================================================================
场景: 127B 长帧, ε=40ppm (最坏) → CFO=96kHz 且 SFO=40ppm 同源注入。
RX: 前导块间差分估 CFO → 消旋 + 用 eps_est=CFO/f_c 换算的 SFO 做逆重采样 → BER。
对照: 只消旋不修 SFO (上轮实测 7.5e-3)。
运行: python model/experiments/run_joint_sfo_cfo.py
输出: 终端 4 组结论 ([A] 只消旋 / [B] 消旋+逆 SFO 重采样 / [C] 无损伤对照
      / [D] CFO 估计 → ε 换算精度) —— 与迁移前一致, 不落盘文件。

链路 (配方 + 损伤段 + 接收段修正, 编排见 model/chains.py / baseband/link.py):
  载荷 → 组帧 → 扩频 → O-QPSK ─[SFO(ε) → CFO(FC·ε) → AWGN]─
       → CFO 估计+消旋 → [逆 SFO 重采样] → MF → 同步(窗 corr[:8·SPS]) → 解扩
"""

import sys
from pathlib import Path

# 本脚本位于 model/experiments/ —— 库模块 (chains / measure / phy_802154 / visualize)
# 在上一级的 model/, 而 Python 只自动把脚本自身目录加入 sys.path, 故显式引导。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

import chains
import measure
import phy_802154 as phy
from baseband import impairments as imp
from baseband.link import stage

SPS = phy.SPS
FS = 16e6               # 复基带采样率 (CFO 旋转步长)
FC = 2.4e9              # 载波频率 (ε ⇄ CFO 换算基准)
L = 127                 # PSDU 字节数 (长帧, 40ppm 帧内积累最坏)
SNR_DB = -1.0
PPM = 40.0              # 同源 ε: SFO 与 CFO 由同一个 ppm 导出
N_FRAMES = 400
SYNC_WIN = 8 * SPS - 1  # 同步搜索窗 = 帧首 8 符号 (corr[:8·SPS] 取 argmax)


# ---------------------------------------------------------------------------
# 接收段: CFO 估计 + 消旋 + 逆 SFO 重采样 (插在匹配滤波之前)
# ---------------------------------------------------------------------------

def cfo_once_stage():
    """CFO 估计 + 消旋 (作用在波形上, 必须在匹配滤波之前)。

    ⚠ 保留迁移前的历史行为: CFO **只在首帧估计一次**, 其后各帧全部复用同一个
    估计值 —— 原 ``rx_chain`` 把估计值写回了形参 ``cfo_est``, 于是 ``if cfo_est
    is None`` 只在首帧成立。这不是有意设计, 但它决定了每帧的消旋量与逆 SFO
    重采样比率, 改成逐帧重估会改变数值, 故逐字保留。
    估计值写入 ``meta['cfo_est']``, 供逆 SFO 阶段动态求值。
    """
    cache = {}

    def apply(sig):
        if "f" not in cache:                        # 仅首帧估计
            cache["f"] = phy.estimate_cfo_preamble_diff(sig.rx)
        sig.meta["cfo_est"] = cache["f"]
        sig.rx = phy.correct_cfo(sig.rx, cache["f"])

    apply.__name__ = "cfo_est(once)+derotate"
    return apply


def inv_sfo_stage():
    """逆 SFO 重采样: ppm 取 −eps_est = −f_est_cfo/f_c·1e6 (同源 ε 约束), 运行到该阶段才求值。"""
    return stage(imp.add_sfo, name="inv_sfo(-eps_est)",
                 ppm=lambda sig: -sig.meta["cfo_est"] / FC * 1e6)


# ---------------------------------------------------------------------------
# 链路配方
# ---------------------------------------------------------------------------

def impaired_chain(fix_sfo: bool, noise_rng):
    """损伤链: SFO(40ppm) → CFO(96kHz) → AWGN; 接收段先估计消旋, fix_sfo 时再逆重采样。

    noise_rng 被 AWGN 阶段持有 —— 传入与迁移前同一个 Generator 才能保证噪声序列
    逐样点一致; 链路对象跨帧复用 (阶段本身无状态)。
    """
    imps = [imp.sfo_stage(PPM),                    # 采样率偏差 (δf 与 CFO 同源)
            imp.cfo_stage(FC * PPM / 1e6, fs=FS),  # 载波频偏 = FC·ε = 96 kHz
            chains.awgn(SNR_DB, noise_rng)]        # AWGN
    chain = chains.link(imps, full=True, sync="window", win=SYNC_WIN)
    pre = [cfo_once_stage()]                       # 估计+消旋: 插在匹配滤波之前
    if fix_sfo:
        pre.append(inv_sfo_stage())                # 联合修正: 再按 eps_est 逆重采样
    chain.rx[:0] = pre
    return chain


def clean_chain(noise_rng):
    """无损伤对照链: 只有 AWGN (同一接收段, 无损伤因子也无需修正)。"""
    return chains.link([chains.awgn(SNR_DB, noise_rng)], full=True,
                       sync="window", win=SYNC_WIN)


def run_frames(chain, rng) -> float:
    """跑 N_FRAMES 帧 → BER (比特误码 / 总比特)。

    统计口径 = ``measure.frame_bit_errors`` (同步失败帧按整帧计错)。每帧先取
    PSDU 再跑链, 保证 rng 消耗顺序 (PSDU → AWGN) 与迁移前完全一致。
    """
    bit_err = bit_tot = 0
    for _ in range(N_FRAMES):
        psdu = bytes(rng.integers(0, 256, size=L).tolist())
        e, t = measure.frame_bit_errors(chain.run(payload=psdu))
        bit_err += e
        bit_tot += t
    return bit_err / bit_tot


def main():
    rng = np.random.default_rng(555)               # [A]/[B] 共用一条帧流 (与迁移前一致)
    print(f"Scenario: PSDU {L}B, ε={PPM:.0f} ppm → CFO {FC*PPM/1e6/1e3:.1f} kHz + "
          f"SFO {PPM:.0f} ppm, SNR {SNR_DB} dB, {N_FRAMES} frames")

    ber_nofix = run_frames(impaired_chain(False, rng), rng)
    print(f"[A] derotate only (no SFO fix):   BER = {ber_nofix:.2e}")

    ber_fix = run_frames(impaired_chain(True, rng), rng)
    print(f"[B] derotate + inverse SFO resampling:   BER = {ber_fix:.2e}")

    rng_ideal = np.random.default_rng(555)         # 无损伤对照: 独立同种子帧流
    ber_ideal = run_frames(clean_chain(rng_ideal), rng_ideal)
    print(f"[C] no-impairment baseline:          BER = {ber_ideal:.2e}")

    # CFO 估计 → ε 换算精度 (理想单帧, 无噪声)
    y0 = imp.add_cfo(phy.modulate_oqpsk(phy.symbols_to_chips(
        phy.tx_symbols(bytes(8)))), FC * PPM / 1e6)
    f = phy.estimate_cfo_preamble_diff(y0)
    print(f"\nCFO estimation residual {abs(f - FC*PPM/1e6)/1e3:.3f} kHz "
          f"→ ε conversion accuracy {abs(f - FC*PPM/1e6)/FC*1e6:.3f} ppm (requirement ±40 ppm bound)")


if __name__ == "__main__":
    main()
