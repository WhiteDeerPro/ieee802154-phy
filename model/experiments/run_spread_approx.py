# -*- coding: utf-8 -*-
"""验证: 解扩的 16 路 argmax 可否用"免乘法幅度近似"代替精确 |s|²

背景: despreader 的末片功率检测 pwr = I²+Q² 需要 32 个乘法器 (16 路 × I/Q)。
经典替代: |z| ≈ α·max(|I|,|Q|) + β·min(|I|,|Q|) (alpha-max-plus-beta-min),
         β=1/2 → 右移; β=3/8 → 两次右移相加 (项目 preamble_detect 的用法)。

关键点: argmax 只需要**保序**, 不需要精确值 —— 近似误差只在两路功率
极为接近时才可能翻转判决, 而那种情况本就是"判决边界"。

方法: 用真实链路 (chains) 生成 rx_chips, 自实现解扩, 对比各判据的符号错误率。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

import chains
import phy_802154 as phy

PSDU = bytes(range(20))
N_FRAMES = 60
CH = np.asarray(phy.CHIP, dtype=float)       # (16, 32), ±1


def approx_mag(S, beta):
    """|z| ≈ max + beta·min（beta=0.5 一次右移；0.375 = 1/4+1/8）。"""
    a = np.abs(S.real)
    b = np.abs(S.imag)
    m = np.maximum(a, b)
    n = np.minimum(a, b)
    if beta == 0.5:
        return m + 0.5 * n
    if beta == 0.375:
        return m + 0.25 * n + 0.125 * n
    return m + beta * n


def run_snr(snr_db, rng):
    stats = {k: dict(sym_err=0, flip=0, n=0)
             for k in ("exact", "half", "three8", "quarter", "maxonly")}
    for _ in range(N_FRAMES):
        sig = chains.simulate(PSDU, [chains.awgn(snr_db, rng)],
                              sync="genie", deframe=True)
        chips = np.asarray(sig.rx_chips)
        ref = np.asarray(sig.symbols)
        n_sym = min(len(ref), len(chips) // 32)
        if n_sym == 0:
            continue
        R = chips[:n_sym * 32].reshape(n_sym, 32)
        S = R @ CH.T                                   # (n_sym, 16) 复数相关
        exact = (np.abs(S) ** 2).argmax(1)
        cands = {
            "exact": np.abs(S) ** 2,
            "half": approx_mag(S, 0.5),
            "three8": approx_mag(S, 0.375),
            "quarter": approx_mag(S, 0.25),
            "maxonly": np.maximum(np.abs(S.real), np.abs(S.imag)),
        }
        for name, metric in cands.items():
            d = metric.argmax(1)
            stats[name]["sym_err"] += int((d != ref[:n_sym]).sum())
            stats[name]["flip"] += int((d != exact).sum())
            stats[name]["n"] += n_sym
    return stats


def main():
    rng = np.random.default_rng(2026)
    lines = ["# 解扩功率检测：免乘法近似的影响（真实链路实测）\n",
             "- 16 路 argmax 判据对比；`flip` = 与精确判据的不一致率",
             "- 近似式 |z| ≈ max + β·min（β=1/2 一次右移；3/8 两次右移相加）\n",
             "| SNR | 判据 | 符号错误率 | 与精确判决不一致 |",
             "|---|---|---|---|"]
    for snr in (-10.0, -7.0, -5.0, -3.0, -1.0):
        st = run_snr(snr, rng)
        for name in ("exact", "half", "three8", "quarter", "maxonly"):
            s = st[name]
            n = max(s["n"], 1)
            lines.append(f"| {snr:+.0f} dB | {name} | {s['sym_err']/n:.4f} | "
                         f"{s['flip']/n:.4f} |")
            print(f"  [{snr:+.0f} dB] {name:8s} sym_err={s['sym_err']/n:.4f} "
                  f"flip={s['flip']/n:.4f}")
        lines.append("")

    import _common
    out = _common.out_dir("spread_approx") / "report.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告: {out}")


if __name__ == "__main__":
    main()
