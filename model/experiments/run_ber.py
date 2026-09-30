# -*- coding: utf-8 -*-
"""
run_ber.py —— 黄金模型 BER vs 码片SNR 仿真
==========================================
验证目标 (Phase 0 出口标准):
  1. 完整 TX/RX 链路在 AWGN 下的 BER 曲线
  2. 扩频增益 ≈ 9.03 dB: 我们的曲线应与 "QPSK 理论曲线左移 10·log10(8)" 重合,
     与无扩频 QPSK 理论曲线保持 ~9 dB 水平间距

链路 (由 chains 库组装, 见 model/chains.py):
  载荷 → 组帧(简化链) → 扩频 → O-QPSK 成形
       → AWGN → 匹配滤波 → 前导同步(honest) → 解扩 → 符号判决
  说明: 性能评估用**简化链** (无白化/FCS) —— 白化不改变 AWGN 下的误码率;
        与 RTL 的逐位比对请用 chains.simulate(..., full=True, deframe=True)。

运行: python model/experiments/run_ber.py
输出: model/out/ber/ber_curve.png, model/out/ber/ber_results.csv
"""

import csv
import math
import sys
from pathlib import Path

# 本脚本位于 model/experiments/ —— 库模块 (chains / measure / phy_802154 / visualize)
# 在上一级的 model/, 而 Python 只自动把脚本自身目录加入 sys.path, 故显式引导。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

import _common
import chains
import measure
import phy_802154 as phy
import visualize

plt = _common.init()                       # Agg 后端 + 中文字体
OUT = _common.out_dir("ber")               # model/out/ber/（已创建）

PSDU_LEN = 20                      # 字节
SNR_RANGE = range(-2, 8)           # 码片 SNR dB
MIN_ERRS = 100                     # 每点最少误码数 (统计可信度)
MAX_PKTS = 3000                    # 每点最多帧数 (运行时间上限)


def qpsk_ber(ebn0_lin):
    """相干 QPSK/BPSK 理论误码率 = 0.5·erfc(√γb)"""
    return 0.5 * math.erfc(math.sqrt(ebn0_lin))


def build_chain(chip_snr_db, rng):
    """本实验的链路: 简化链 + AWGN + 诚实前导同步。

    链路对象在扫参点内复用 (阶段无状态; rng 由闭包持有以保证可复现)。
    """
    return chains.link([chains.awgn(chip_snr_db, rng)], full=False, sync="honest")


def run_point(chip_snr_db, rng):
    """单点仿真: 返回 (误码数, 总比特数)

    统计口径见 measure.frame_bit_errors: 同步失败帧按整帧计错。

    注意: 与迁移前一致, **同步失败帧不参与早停判定** —— 这是原实现的
    ``continue`` 副作用 (非有意设计), 在此保留以确保与既有结果逐比特可比。
    若要让早停对所有帧生效 (行为更一致), 删掉下面那个 ``continue`` 即可。
    """
    chain = build_chain(chip_snr_db, rng)
    bit_err = bit_tot = 0
    for _ in range(MAX_PKTS):
        psdu = bytes(rng.integers(0, 256, size=PSDU_LEN).tolist())
        sig = chain.run(payload=psdu)
        e, t = measure.frame_bit_errors(sig)
        bit_err += e
        bit_tot += t
        if sig.meta.get("sync_fail"):
            continue
        if bit_err >= MIN_ERRS:
            break
    return bit_err, bit_tot


def main():
    phy._selftest()
    rng = np.random.default_rng(2026)
    rows = []
    print(f"{'ChipSNR(dB)':>12} {'BitErr':>8} {'TotBits':>10} {'BER':>12}")
    for snr in SNR_RANGE:
        be, bt = run_point(snr, rng)
        ber = be / bt if be else 0.0
        rows.append((snr, be, bt, ber))
        print(f"{snr:>10.1f} {be:>8d} {bt:>10d} {ber:>12.3e}")

    # ---- 绘图 ----
    xs = np.array([r[0] for r in rows], dtype=float)
    bers = np.array([r[3] for r in rows])
    xc = np.linspace(-2, 7, 100)
    # 扩频后每比特能量 = 8 x 码片能量 -> QPSK 理论以码片SNR+9.03dB 为横轴
    theory_spread = [qpsk_ber(8 * 10 ** (x / 10)) for x in xc]
    theory_plain = [qpsk_ber(10 ** (x / 10)) for x in xc]

    fig, ax = plt.subplots(figsize=(8.5, 6))
    # 零误码点画成「未测到误码」的上界标记, 而非固定下限 (否则会被误读成 error floor)
    visualize.plot_ber_curve(xs, bers, ax=ax, color="tab:blue",
                             label="Golden model (non-coherent 16-ary RX)",
                             lw=1.5, marker="o", ms=5)
    ax.semilogy(xc, theory_spread, "--", lw=1.2,
                label="Coherent QPSK theory + 9.03 dB (spreading-gain ref)")
    ax.semilogy(xc, theory_plain, ":", lw=1.2, color="gray",
                label="QPSK theory (no spreading, ref)")
    for x, b in zip(xs, bers):
        if b > 0:
            ax.annotate(f"{b:.1e}", (x, b), textcoords="offset points",
                        xytext=(6, -12), fontsize=8, color="tab:blue")
    ax.set_xlabel("Chip SNR after matched filter (dB)")
    ax.set_ylabel("BER")
    ax.set_title("IEEE 802.15.4 O-QPSK/DSSS Golden Model BER vs Chip SNR (AWGN, 20 B PSDU)")
    ax.legend(loc="lower left")
    fig.tight_layout()
    fig.savefig(OUT / "ber_curve.png", dpi=130)

    with open(OUT / "ber_results.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["chip_snr_db", "bit_errors", "total_bits", "ber"])
        w.writerows(rows)

    # ---- 结论判定: 扩频增益 = 无扩频 QPSK 所需码片SNR - 仿真所需码片SNR ----
    print()
    def qpsk_req_snr(target_ber):
        lo, hi = 0.0, 20.0
        for _ in range(60):
            mid = (lo + hi) / 2
            if qpsk_ber(10 ** (mid / 10)) > target_ber:
                lo = mid
            else:
                hi = mid
        return hi
    for target_ber in (1e-3, 1e-4):
        sim = next((r[0] for r in rows if r[3] and r[3] <= target_ber), None)
        if sim is not None:
            gain = qpsk_req_snr(target_ber) - sim
            print(f"BER={target_ber:g}: sim chip SNR ~ {sim:.1f} dB, "
                  f"QPSK w/o spreading needs {qpsk_req_snr(target_ber):.1f} dB -> "
                  f"spreading gain ~ {gain:.1f} dB (theory {phy.SPREADING_GAIN_DB:.2f} dB)")
    print(f"Outputs: {OUT / 'ber_curve.png'}, {OUT / 'ber_results.csv'}")


if __name__ == "__main__":
    main()
