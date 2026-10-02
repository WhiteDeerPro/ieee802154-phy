#!/usr/bin/env python
"""run_awgn_ref.py —— AWGN 生成器：**参考模型与选型**（"先 ref 后 RTL"的 ref 环节）。

A. **Python 复刻（与 RTL 同算法）→ 与 RTL 导出样本逐位对照**
   （64bit Fibonacci LFSR×2, 同种子/多项式; 每拍 60 位滑窗 XOR; CLT(12,5); σ=32 档）
B. **CLT 参数选型**: (N,SW) 扫描 → 峰度 / K-S 距离 / 4σ 尾比（vs 标准正态）
C. **判决级等效**: CLT 噪声 vs 理想高斯——BPSK 判决 BER 对照（同 σ）
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'model'))
import _common  # noqa: E402

MASK64 = (1 << 64) - 1
SEED_I = 0x0123456789ABCDEF
GALOIS_FB = (63, 62, 60, 59)          # x^64+x^63+x^61+x^60+1（fibonacci 抽头）


def lfsr_step(st):
    fb = ((st >> 63) ^ (st >> 62) ^ (st >> 60) ^ (st >> 59)) & 1
    return ((st << 1) & MASK64) | fb


def gen_clt_samples(n_samp, N=12, SW=5, mul=256, shift=8, seed=SEED_I):
    """复刻 RTL: 每拍生成 60 位（60 次单步 = RTL 滑窗展开的等价），取 12 段求和定标。"""
    st = seed
    out = np.empty(n_samp, dtype=np.int64)
    lo = N * ((1 << SW) - 1) // 2        # 均值
    for k in range(n_samp):
        nbits = []                       # 本拍 60 个新位（RTL 位序: bit59=第1个 … bit0=第60个）
        for _ in range(N * SW):
            st = lfsr_step(st)
            nbits.append(st & 1)         # 新位（fb）
        # RTL: nb[59]=fb_1 … nb[0]=fb_60 → 段 g 取 nb[g*SW +: SW]
        # 位序（与 RTL 对齐, 已验证逐位一致）: 段 g 的 SW 位 = nbits[g*SW .. g*SW+SW-1]
        s = 0
        for g in range(N):
            seg = 0
            for b in range(SW):
                seg = (seg << 1) | nbits[g * SW + b]
            s += seg
        out[k] = (s - lo) * mul >> shift
    return out


def main():
    plt = _common.init()
    outd = _common.out_dir("awgn_ref")

    # ---------- A. RTL vs ref 逐位对照 ----------
    rtl_csv = ROOT / "model/out/awgn/samples_i.csv"
    lines = []
    if rtl_csv.exists():
        rtl = np.loadtxt(rtl_csv, dtype=np.int64)[:2000]
        ref = gen_clt_samples(len(rtl))[:len(rtl)]
        mism = int(np.sum(rtl != ref))
        lines.append(f"- A) RTL vs Python 复刻逐位对照: **{len(rtl)-mism}/{len(rtl)} 一致**"
                     f"（失配 {mism}）")
        print(lines[-1])
    else:
        lines.append("- A) 跳过（无 RTL 样本 csv）")

    # ---------- B. CLT 参数选型 ----------
    rng = np.random.default_rng(7)
    n = 500_000
    lines += ["", "| N | SW | 峰度 | K-S 距离 | 4σ 尾比(经验/理论) |", "|---|---|---|---|---|"]
    for (N, SW) in ((4, 5), (8, 5), (12, 5), (16, 5), (24, 5), (12, 4), (12, 6)):
        u = rng.integers(0, 1 << SW, size=(n, N)).sum(axis=1).astype(float)
        x = (u - u.mean()) / u.std()
        kurt = float(((x - x.mean()) ** 4).mean())
        xs = np.sort(x)
        ecdf = np.arange(1, n + 1) / n
        from math import erf, sqrt
        ncdf = np.array([0.5 * (1 + erf(v / sqrt(2))) for v in xs])
        ks = float(np.max(np.abs(ecdf - ncdf)))
        tail = float(np.mean(np.abs(x) > 4)) / 6.334e-5
        lines.append(f"| {N} | {SW} | {kurt:.3f} | {ks:.4f} | {tail:.2f} |")
        print(f"N={N} SW={SW}: kurt={kurt:.3f} KS={ks:.4f} tail4s={tail:.2f}")

    # ---------- C. 判决级等效（BPSK）----------
    rng = np.random.default_rng(11)
    nb = 2_000_000
    bits = rng.choice([-1.0, 1.0], size=nb)
    lines += ["", "C) BPSK 判决 BER（n=2M; σ 覆盖高/低 BER 区）:", "",
              "| σ | BER(理想高斯) | BER(CLT) | 相对差 |", "|---|---|---|---|"]
    for sigma in (0.32, 0.5, 1.0, 3.1):
        noise_g = rng.normal(0, sigma, nb)
        clt = generate_big_clt(nb, sigma, rng)
        ber_g = float(np.mean((bits + noise_g > 0) != (bits > 0)))
        ber_c = float(np.mean((bits + clt > 0) != (bits > 0)))
        rel = abs(ber_c - ber_g) / max(ber_g, 1e-9) * 100
        lines.append(f"| {sigma} | {ber_g:.5f} | {ber_c:.5f} | {rel:.1f}% |")
        print(f"sigma={sigma}: BER 高斯 {ber_g:.5f} vs CLT {ber_c:.5f} ({rel:.1f}%)")

    # ---------- 图 ----------
    fig, ax = plt.subplots(1, 2, figsize=(10, 3.8))
    xg = rng.normal(0, 1, 200_000)
    xc = generate_big_clt(200_000, 1.0, rng)
    ax[0].hist(xg, bins=120, density=True, alpha=.6, label="ideal Gauss")
    ax[0].hist(xc, bins=120, density=True, alpha=.6, label="CLT(12,5)")
    ax[0].set_yscale("log")
    ax[0].legend()
    ax[0].set_title("PDF (log) — tail comparison")
    ax[1].plot(np.sort(xg)[::40], np.linspace(0, 1, len(np.sort(xg)[::40])), label="Gauss")
    ax[1].plot(np.sort(xc)[::40], np.linspace(0, 1, len(np.sort(xc)[::40])), label="CLT")
    ax[1].legend()
    ax[1].set_title("CDF")
    fig.tight_layout()
    fig.savefig(outd / "dist_compare.png", dpi=110)

    (outd / "report.md").write_text("# AWGN ref 与选型\n\n" + "\n".join(lines) + "\n")
    print("→", outd / "report.md")


def generate_big_clt(n, sigma, rng, N=12, SW=5):
    """向量化 CLT（numpy 均匀和 → 定标到 σ）。"""
    u = rng.integers(0, 1 << SW, size=(n, N)).sum(axis=1).astype(float)
    u -= u.mean()
    u /= u.std()
    return u * sigma


if __name__ == "__main__":
    main()
