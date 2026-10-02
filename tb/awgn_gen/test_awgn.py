"""awgn_gen: 统计验证 —— 均值/σ/峰度/自相关（白性）/σ 可调/I-Q 独立 + 直方图与自相关图导出。"""
import sys
from pathlib import Path

import cocotb
import numpy as np
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer

W = 16
HALF = 1 << (W - 1)


async def reset(dut, mul=256, shift=8):
    """复位 + 启动时钟; σ = 32 * mul / 2^shift（默认 σ=32）。"""
    cocotb.start_soon(Clock(dut.clk, 62.5, unit="ns").start())
    dut.en.value = 0
    dut.cfg_mul.value = mul
    dut.cfg_shift.value = shift
    dut.rst_n.value = 0
    await Timer(200, unit="ns")
    dut.rst_n.value = 1
    await FallingEdge(dut.clk)
    dut.en.value = 1


async def sample(dut, n):
    """收集 n 对样本（out_dv=1 时读, 16bit 补码解读）。"""
    ii, qq = [], []
    while len(ii) < n:
        await FallingEdge(dut.clk)
        if int(dut.out_dv.value) == 1:
            vi = int(dut.out_i.value)
            vq = int(dut.out_q.value)
            if vi >= HALF:
                vi -= 1 << W
            if vq >= HALF:
                vq -= 1 << W
            ii.append(vi)
            qq.append(vq)
    return np.array(ii, dtype=np.int64), np.array(qq, dtype=np.int64)


def stats(x):
    m = x.mean()
    s = x.std()
    k = ((x - m) ** 4).mean() / s ** 4
    return float(m), float(s), float(k)


def autocorr(x, lag):
    a = x[:-lag].astype(float)
    b = x[lag:].astype(float)
    a = a - a.mean()
    b = b - b.mean()
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))


@cocotb.test()
async def awgn_statistics(dut):
    """σ=32 档: 65536 样本的统计一致性。"""
    await reset(dut)
    i, q = await sample(dut, 65536)
    mi, si, ki = stats(i)
    mq, sq, kq = stats(q)
    r1, r2, r3 = autocorr(i, 1), autocorr(i, 2), autocorr(i, 3)
    xc = float(np.corrcoef(i, q)[0, 1])
    dut._log.info(f"sigma_i={si:.2f} (exp 32) mean={mi:.3f} kurt={ki:.2f} "
                  f"ac1={r1:+.4f} ac2={r2:+.4f} ac3={r3:+.4f} corrIQ={xc:+.4f}")
    dut._log.info(f"sigma_q={sq:.2f} mean_q={mq:.3f} kurt_q={kq:.2f}")
    assert abs(si - 32) < 32 * 0.10, f"sigma_i={si:.2f}"
    assert abs(sq - 32) < 32 * 0.10, f"sigma_q={sq:.2f}"
    assert abs(mi) < 2.0 and abs(mq) < 2.0, "mean off"
    assert 2.5 < ki < 3.6, f"kurtosis_i={ki:.2f}"
    assert 2.5 < kq < 3.6, f"kurtosis_q={kq:.2f}"
    assert abs(r1) < 0.05 and abs(r2) < 0.05 and abs(r3) < 0.05, "autocorr"
    assert abs(xc) < 0.05, "i/q correlated"

    # 导出（样本 + 直方图/自相关图）——供人工复核与报告引用
    out = Path(__file__).resolve().parents[2] / "model" / "out" / "awgn"
    out.mkdir(parents=True, exist_ok=True)
    np.savetxt(out / "samples_i.csv", i[:8192], fmt="%d")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(1, 3, figsize=(13, 3.6))
    ax[0].hist(i, bins=90, density=True, alpha=0.75, label="RTL")
    xs = np.linspace(i.min(), i.max(), 500)
    ax[0].plot(xs, np.exp(-xs ** 2 / (2 * si ** 2)) / (si * np.sqrt(2 * np.pi)),
               "r-", lw=1.2, label="N(0, sigma)")
    ax[0].legend()
    ax[0].set_title(f"awgn_gen histogram (sigma={si:.1f})")
    lags = np.arange(1, 17)
    ax[1].bar(lags, [autocorr(i, int(l)) for l in lags])
    ax[1].set_title("autocorr lag 1..16")
    ax[2].plot(i[:600], lw=0.6)
    ax[2].set_title("time series (first 600)")
    fig.tight_layout()
    fig.savefig(out / "awgn_stats.png", dpi=110)


@cocotb.test()
async def awgn_sigma_tunable(dut):
    """σ 可调: (mul=64, shift=8) → σ=8。"""
    await reset(dut, mul=64, shift=8)
    i, q = await sample(dut, 16384)
    s = float(i.std())
    dut._log.info(f"sigma(64,8) = {s:.2f} (exp 8)")
    assert abs(s - 8) < 8 * 0.12, f"sigma={s:.2f}"
