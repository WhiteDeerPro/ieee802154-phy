# -*- coding: utf-8 -*-
"""
run_impairments.py —— 非理想性定量仿真 v2
============================================
方法论修正 (相对 v1):
  - 固定帧数, 不用早停 —— 避免同步失败帧 + 早停导致 BER 统计向上偏置
  - 同步/解扩分离度量:
      A. 解扩 CFO 容忍 (genie 同步 p=0): 数据通路对 CFO 的固有要求
      B. 同步 CFO 容忍 (honest 全搜索): 当前模板相关器的 CFO 崩塌点
      C. 定时偏差 (assisted 同步, 窗 ±1 码片): 定时跟踪的分辨率需求
      D. 多径场景 (assisted 同步): DSSS 固有容忍度
  输出 = Phase 2 同步器设计需求

链路 (由 chains 库组装, 见 model/chains.py):
  载荷 → 组帧(简化链) → 扩频 → O-QPSK 成形
       → [多径] → AWGN → [CFO] → [定时] → 匹配滤波 → 前导同步 → 解扩
  损伤项由 baseband.impairments 的 *_stage 工厂给出, 列表顺序即损伤作用顺序
  (与原脚本一致: 多径 → AWGN → CFO → 定时); AWGN 用按制式标定的 chains.awgn。
  sync 语义映射: 'genie'→sync="genie"; 'assist'→sync="window", win=phy.SPS;
                 'honest'→sync="honest"。
  误码统计口径 = measure.frame_bit_errors (同步失败整帧计错, 与原脚本一致)。

运行: python model/experiments/run_impairments.py
输出: model/out/impairments/imp_cfo.png, model/out/impairments/imp_eps.png
"""

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
from baseband import impairments as imp

plt = _common.init()                          # Agg 后端 + 中文字体
OUT = _common.out_dir("impairments")          # model/out/impairments/（已创建）

PSDU_LEN = 20
N_FRAMES = 600                    # 固定帧数: 600 x 216 bit = 129600 bit/配置
SYNC_WIN = phy.SPS                # assisted 同步搜索窗 ±1 码片
SNR = -1.0                        # 码片 SNR (dB)
SYNC_MODE = {"genie": "genie", "assist": "window", "honest": "honest"}


def run_config(chip_snr_db, rng, cfo_hz=0.0, eps=0.0, mp=None, sync="assist"):
    """返回 (ber, sync_fail, total_bits); sync: 'genie'|'assist'|'honest'

    链路对象在本扫参点内复用 (阶段无状态, rng 由闭包持有保证可复现);
    每帧只新建一次 Signal 上下文 —— 与迁移前的「每帧独立重算」逐比特等价。
    """
    stages = []
    if mp is not None:
        stages.append(imp.multipath_stage(mp["gains"], mp["delays"]))
    stages.append(chains.awgn(chip_snr_db, rng))
    if cfo_hz:
        stages.append(imp.cfo_stage(cfo_hz))
    if eps:
        stages.append(imp.timing_stage(eps))
    chain = chains.link(stages, full=False, sync=SYNC_MODE[sync], win=SYNC_WIN)

    bit_err = bit_tot = sync_fail = 0
    for _ in range(N_FRAMES):
        psdu = bytes(rng.integers(0, 256, size=PSDU_LEN).tolist())
        sig = chain.run(payload=psdu)
        e, t = measure.frame_bit_errors(sig)         # 同步失败帧整帧计错
        bit_err += e
        bit_tot += t
        sync_fail += bool(sig.meta["sync_fail"])
    return bit_err / bit_tot, sync_fail, bit_tot


def sweep(title, col, values, rng, fmt, cfg):
    """扫一组参数: 按迁移前的表格格式打印, 返回 BER 列表 (含 1e-9 绘图下限)。"""
    print(title)
    print(f"{col:>9} {'BER':>11} {'sync fail':>6}")
    bers = []
    for v in values:
        b, sf, _ = run_config(SNR, rng, **cfg(v))
        bers.append(max(b, 1e-9))
        print(f"{fmt(v):>9} {b:>11.3e} {sf:>6d}")
    return bers


def main():
    phy._selftest()
    rng = np.random.default_rng(777)

    # ---- A. 解扩 CFO 容忍 (genie 同步) ----
    cfos = [0, 5e3, 10e3, 15e3, 20e3, 30e3, 50e3, 100e3, 200e3]
    berA = sweep(f"== A. Despread CFO tolerance (genie sync) @ chip SNR={SNR} dB ==",
                 "CFO(kHz)", cfos, rng, fmt=lambda f: f"{f / 1e3:.0f}",
                 cfg=lambda f: dict(cfo_hz=f, sync="genie"))

    # ---- B. 同步 CFO 容忍 (honest 全搜索) ----
    berB = sweep(f"\n== B. Sync CFO tolerance (honest correlation) @ chip SNR={SNR} dB ==",
                 "CFO(kHz)", cfos, rng, fmt=lambda f: f"{f / 1e3:.0f}",
                 cfg=lambda f: dict(cfo_hz=f, sync="honest"))

    # ---- C. 定时偏差 (assisted 同步) ----
    epss = [0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.5]
    berC = sweep(f"\n== C. Timing offset (assisted sync) @ chip SNR={SNR} dB ==",
                 "eps(chip)", epss, rng, fmt=lambda e: f"{e:.2f}",
                 cfg=lambda e: dict(eps=e))

    # ---- D. 多径场景 (assisted 同步) ----
    print(f"\n== D. Multipath scenarios (assisted sync) ==")
    print(f"{'scenario':>12} {'SNR(dB)':>8} {'BER':>11} {'sync fail':>6}")
    for name, mp in imp.SCENARIOS.items():
        for s in (-2.0, -1.0, 0.0):
            b, sf, _ = run_config(s, rng, mp=mp)
            print(f"{name:>12} {s:>8.1f} {b:>11.3e} {sf:>6d}")

    # ---- 图 1: CFO ----
    fig, ax = plt.subplots(figsize=(8.5, 5.5))
    ax.semilogx(np.array(cfos[1:]) / 1e3, berA[1:], "o-", label="A: despread intrinsic tolerance (genie sync)")
    ax.semilogx(np.array(cfos[1:]) / 1e3, berB[1:], "s--", label="B: full link with sync (honest correlation)")
    ax.axvspan(40, 96, color="r", alpha=0.12)
    ax.text(60, 2e-1, "dual-end ±20 ppm\n≈ 40–96 kHz (assumed)", fontsize=8, color="r")
    ax.set_xlabel("CFO (kHz)"); ax.set_ylabel("BER")
    ax.set_title(f"CFO tolerance breakdown (@chip SNR={SNR} dB, fixed 600 frames)")
    ax.grid(True, which="both", alpha=0.3); ax.legend()
    fig.tight_layout(); fig.savefig(OUT / "imp_cfo.png", dpi=130); plt.close(fig)

    # ---- 图 2: eps ----
    fig, ax = plt.subplots(figsize=(8, 5.5))
    ax.semilogy(epss, berC, "o-")
    ax.set_xlabel("Static timing offset (chips)"); ax.set_ylabel("BER")
    ax.set_title(f"Timing offset impact (@chip SNR={SNR} dB, assisted sync ±1 chip window)")
    ax.grid(True, which="both", alpha=0.3)
    fig.tight_layout(); fig.savefig(OUT / "imp_eps.png", dpi=130); plt.close(fig)

    # ---- 设计需求 ----
    print("\n== Phase 2 sync design requirement derivation ==")
    tolA = max(f / 1e3 for f, b in zip(cfos, berA) if b <= 3e-3)
    tolB = max(f / 1e3 for f, b in zip(cfos, berB) if b <= 3e-3)
    tole = max(e for e, b in zip(epss, berC) if b <= 3e-3)
    print(f"Despread intrinsic CFO tolerance: {tolA:.0f} kHz -> CFO estimation residual target ≤ {tolA/3:.0f} kHz (1/3 margin)")
    print(f"Current template correlator sync CFO tolerance: {tolB:.0f} kHz -> sync algorithm must be CFO-robust (differential/post-detection correlation)")
    print(f"Timing offset tolerance: ±{tole:.2f} chips -> timing recovery resolution target ≤ {tole/4:.3f} chips")


if __name__ == "__main__":
    main()
