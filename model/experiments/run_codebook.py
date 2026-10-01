# -*- coding: utf-8 -*-
"""run_codebook.py —— 码本对比：PN（802.15.4）vs Walsh-Hadamard

问题（用户提出）：把数据部分的扩频码换成 Walsh-Hadamard（完全正交），
能否让解扩的多址干扰下降、甚至支持 CDMA 式（同频）分离？

机制分析：
  解扩时的干扰项 = Σ_k (码字积) · e^{jΔω·k·T_chip}
  · Δf 大 → 指数项在窗内快转, 自我抵消（与码无关, 这就是 run_dapping 的机制）
  · Δf≈0 → 指数项恒定 → 干扰 = **码字互相关**：PN 为 ±8/32=0.25, Walsh 为 0
  ⇒ Walsh 的价值正是"同频也能分"（真正的码分多址）; 代价是不兼容标准,
     且 Walsh 自相关差 → 前导/SFD 仍必须保留 PN（本实验即如此）。

实验：两设备波形叠加。SHR（前导+SFD）都用 PN；数据符号分别用 PN / Walsh 码本。
      各自消旋后解扩, 统计**数据符号**的正确率。
运行: python model/experiments/run_codebook.py
输出: model/out/codebook/report.md
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from scipy.linalg import hadamard

import _common
import phy_802154 as phy

FS = 16e6
SPS = phy.SPS
SCALE = 8.0
SNR_DB = 18.0
N_FRAMES = 30
N_SHR = 10                          # 前导+SFD 的符号数（与 chains.SHR_SYMBOLS 一致）
DELTA_KHZ = [0, 5, 10, 25, 50, 100]

PN_CODES = np.asarray(phy.CHIP, dtype=float)               # 16×32, ±1
W_CODES = hadamard(32)[:16].astype(float)                  # 取 Hadamard 前 16 行


def gen_wave(psdu, cfo_hz, data_codes):
    """SHR 用 PN, 数据符号用 data_codes。"""
    syms = phy.ppdu_symbols(psdu)
    shr = np.concatenate([PN_CODES[s] for s in syms[:N_SHR]])
    dat = np.concatenate([data_codes[s] for s in syms[N_SHR:]])
    chips = np.concatenate([shr, dat])
    x = phy.modulate_oqpsk(chips) * SCALE          # 吃**码片**（±1）→ 复基带
    # 补零到足够长度: 浮点版尾部比定点版短, 而采样需要 idx 到位
    need = (N_SHR + 46) * 32 * SPS + 64
    if len(x) < need:
        x = np.pad(x, (0, need - len(x)))
    t = np.arange(len(x)) / FS
    return x * np.exp(2j * np.pi * cfo_hz * t), syms[N_SHR:]


def despread_data(x_derot, data_codes):
    """消旋后 → MF → 采样码片 → 数据段按自定义码本解扩。"""
    mf = phy.matched_filter(x_derot, sps=SPS)
    n_all = N_SHR + 46                                  # 56 个符号（20B PSDU）
    m = np.arange(n_all * 32)
    idx = m * SPS + np.where(m % 2, SPS // 2, 0) + (SPS - 1)
    v = mf[idx].astype(complex)
    raw = np.where(m % 2 == 0, v, v * (-1j))            # 去交错（奇片 -j 归位）
    raw = raw[N_SHR * 32:]                              # 只取数据段
    n_sym = len(raw) // 32
    R = raw[:n_sym * 32].reshape(n_sym, 32) @ data_codes.T
    return np.abs(R).argmax(1)


def run_point(delta_hz, data_codes, rng):
    hit = tot = 0
    for _ in range(N_FRAMES):
        psdu_a = bytes(rng.integers(0, 256, 20).tolist())
        psdu_b = bytes(rng.integers(0, 256, 20).tolist())
        xa, ref_a = gen_wave(psdu_a, 50e3, data_codes)
        xb, _ = gen_wave(psdu_b, 50e3 + delta_hz, data_codes)
        n = max(len(xa), len(xb))
        r = np.pad(xa, (0, n - len(xa))) + np.pad(xb, (0, n - len(xb)))
        p_sig = np.mean(np.abs(xa) ** 2)
        sigma = np.sqrt(p_sig / (10 ** (SNR_DB / 10)) / 2)
        r = r + (rng.standard_normal(n) + 1j * rng.standard_normal(n)) * sigma

        t = np.arange(n) / FS
        x_derot = r * np.exp(-2j * np.pi * 50e3 * t)     # 通道 A 消旋
        got = despread_data(x_derot, data_codes)
        m = min(len(got), len(ref_a))
        hit += int((got[:m] == np.asarray(ref_a[:m])).sum())
        tot += m
    return hit / max(tot, 1)


def main():
    rng = np.random.default_rng(2026)
    lines = ["# 码本对比：PN（802.15.4）vs Walsh-Hadamard（两设备叠加, 数据符号正确率）\n",
             f"- 每点 {N_FRAMES} 帧, SNR {SNR_DB} dB; SHR 一律用 PN; 只换**数据段**码本",
             "- 判据：通道 A 消旋后, 数据段 46 个符号的解扩正确率\n",
             "| Δf (kHz) | PN 码本 | Walsh 码本 | 增益 |", "|---|---|---|---|"]
    for d in DELTA_KHZ:
        ra = run_point(d * 1e3, PN_CODES, rng)
        rw = run_point(d * 1e3, W_CODES, rng)
        lines.append(f"| {d} | {ra:.1%} | {rw:.1%} | {rw - ra:+.1%} |")
        print(f"  Δf={d:4d} kHz   PN={ra:.1%}   Walsh={rw:.1%}")
    lines.append("\n**解读**：Δf 小（同频/近频）时 PN 的码字互相关（0.25）漏成干扰；"
                 "Walsh 的完全正交把它压掉 —— 这正是「同频也能分」的码分思路。"
                 "Δf 大时两者都会被积分窗自我抵消, 差别收窄。")
    out = _common.out_dir("codebook") / "report.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告: {out}")


if __name__ == "__main__":
    main()
