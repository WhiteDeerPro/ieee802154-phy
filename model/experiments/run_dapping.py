# -*- coding: utf-8 -*-
"""run_dapping.py —— 重叠波形实验：两设备信号叠加，双通道能否各自解出？

问题（用户提出）：两帧信息在时间上**完全重叠**（dapping 两个波形），两个通道
用不同参数，能否在短时间内同时解算出两条码流？

先厘清 CDMA 的类比（重要）：
  802.15.4 的 16 个扩频码字是**数据映射**（4bit → 32 码片），**所有设备共用同一套**
  —— 它不像 CDMA 那样给每个用户分配正交码。所以"用扩频码区分设备"在这里不成立。
  能起分离作用的是**旋性差（频率差）**：
      r(t) = sA(t)·e^{jωA t} + sB(t)·e^{jωB t} + n
  通道 A 用 ωA 消旋后, B 变成"每符号转 Δf·16µs 圈"的干扰 —— 转得越快、
  在解扩积分窗内自我抵消得越干净。这正是双通道架构的物理基础。

本实验扫描 Δf = |fB − fA|，测两通道各自的解帧成功率。
运行: python model/experiments/run_dapping.py
输出: model/out/dapping/report.md
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

import _common
import phy_802154 as phy

FS = 16e6
SPS = phy.SPS
SCALE = 8.0
SNR_DB = 18.0
N_FRAMES = 40
DELTA_KHZ = [0, 10, 25, 50, 100, 200, 300, 500]


SHR_SYMBOLS = 10        # 与 model/chains.py 常量一致（前导 8 + SFD + …）


def gen_wave(psdu, cfo_hz, rng):
    """一帧的复基带波形（含 CFO 与噪声前定标）。"""
    syms = phy.tx_symbols(psdu)
    i_f, q_f = phy.modulate_oqpsk_fixed(syms.tolist())
    n = max(len(i_f), len(q_f))
    i_f = np.pad(np.asarray(i_f, dtype=float), (0, n - len(i_f)))
    q_f = np.pad(np.asarray(q_f, dtype=float), (0, n - len(q_f)))
    x = (i_f + 1j * q_f) * SCALE
    t = np.arange(len(x)) / FS
    return x * np.exp(2j * np.pi * cfo_hz * t)


def demod(x, psdu_ref):
    """消旋后的波形 → 解帧；返回是否正确（align 固定 0：两路波形都从采样 0 开始）。"""
    mf = phy.matched_filter(x, sps=SPS)
    n_all = len(phy.tx_symbols(psdu_ref))              # 56（含 SHR）
    syms = phy.rx_despread(mf, 0, n_all, sps=SPS)
    got, fcs_ok, _ = phy.rx_deframe_symbols(syms[SHR_SYMBOLS:])
    return fcs_ok and (got == psdu_ref)


def run_point(delta_hz, rng):
    """一点 (Δf)：两设备波形叠加, 双通道各自消旋后解帧。"""
    ok_a = ok_b = 0
    for _ in range(N_FRAMES):
        psdu_a = bytes(rng.integers(0, 256, 20).tolist())
        psdu_b = bytes(rng.integers(0, 256, 20).tolist())
        cfo_a = 50e3
        cfo_b = 50e3 + delta_hz
        xa = gen_wave(psdu_a, cfo_a, rng)
        xb = gen_wave(psdu_b, cfo_b, rng)
        n = max(len(xa), len(xb))
        r = np.pad(xa, (0, n - len(xa))) + np.pad(xb, (0, n - len(xb)))
        # AWGN（相对单路信号的码片 SNR）
        p_sig = np.mean(np.abs(xa) ** 2)
        sigma = np.sqrt(p_sig / (10 ** (SNR_DB / 10)) / 2)
        r = r + (rng.standard_normal(n) + 1j * rng.standard_normal(n)) * sigma

        t = np.arange(n) / FS
        # 通道 A / B: 各自消旋
        for cfo, psdu_ref, is_a in ((cfo_a, psdu_a, True), (cfo_b, psdu_b, False)):
            x = r * np.exp(-2j * np.pi * cfo * t)
            hit = demod(x, psdu_ref)
            if is_a:
                ok_a += hit
            else:
                ok_b += hit
    return ok_a / N_FRAMES, ok_b / N_FRAMES


def main():
    rng = np.random.default_rng(2026)
    lines = ["# 重叠波形（dapping）：两设备信号叠加下的双通道分离\n",
             f"- 每点 {N_FRAMES} 帧, 两路信号同功率叠加, SNR {SNR_DB} dB, CFO_A = +50 kHz",
             "- 判据：各自消旋后能否解出**自己那一帧**（FCS + 载荷比对）\n",
             "| Δf (kHz) | 一帧转过的圈数* | 通道 A 解出率 | 通道 B 解出率 | 两路同时解出 |",
             "|---|---|---|---|---|"]
    for d in DELTA_KHZ:
        ra, rb = run_point(d * 1e3, rng)
        turns = d * 1e3 * 16e-6            # 符号周期 16 µs
        lines.append(f"| {d} | {turns:.2f} | {ra:.0%} | {rb:.0%} | {min(ra, rb):.0%} |")
        print(f"  Δf={d:4d} kHz  通道A={ra:.0%}  通道B={rb:.0%}")
    lines.append("\n\\* 干扰信号在**一个符号周期（16 µs）**内转过的圈数 —— 圈数越多，"
                 "解扩积分窗内自我抵消越充分。")
    out = _common.out_dir("dapping") / "report.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告: {out}")


if __name__ == "__main__":
    main()
