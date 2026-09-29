# -*- coding: utf-8 -*-
"""
run_sfo_fix.py —— SFO 修复方案对比 (ref 自拟方案验证)
======================================================
场景: 127B 长帧, 同源 ε → CFO + SFO 联合注入。
方案:
  [A] 只消旋 (基准缺陷): 不修 SFO
  [B] 连续逆重采样 (性能上界): 时变分数延迟 np.interp (RTL = Farrow 插值器, 贵)
  [C] 码片域 8 相位阶梯切换 (自拟, RTL 友好): 采样位置 = 峰 + round(eps_est·t_m·fs),
      逐码片阶梯步进 1 采样 (0.125 码片/步), 复用 preamble_sync 8 相位结构,
      RTL = 相位累加器 + 比较器, 无插值器

链路 (由 chains 库组装, 见 model/chains.py):
  载荷 → 组帧(完整链) → 扩频 → O-QPSK 成形
       → SFO(ppm) → CFO(同源: fc = ε·FC) → AWGN
       → CFO 前导差分估纠 → [B] 连续逆重采样 / [C] 码片域阶梯修正
       → 匹配滤波 → 前导同步 → 定时采样 → 解扩

关键点 (参数依赖前序阶段结果): 逆重采样量 eps_est = cfo_est/FC·1e6 由估计阶段写进
``sig.meta``, 逆 SFO 阶段用 ``stage(..., ppm=lambda sig: ...)`` 在运行时求值 ——
即「阶段参数依赖链上前面阶段结果」, 无需在脚本里手工串联。
比特误码统计统一走 ``measure.frame_bit_errors`` (同步失败帧按整帧计错)。

运行: python model/experiments/run_sfo_fix.py  →  out/impairments/imp_sfo_fix.png
"""

import sys
from pathlib import Path

# 本脚本位于 model/experiments/ —— 库模块 (chains / measure / phy_802154 / visualize)
# 在上一级的 model/, 而 Python 只自动把脚本自身目录加入 sys.path, 故显式引导。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import chains
import measure
import phy_802154 as phy
from baseband import impairments as imp
from baseband.link import stage

OUT = Path(__file__).resolve().parent.parent / "out" / "impairments"
OUT.mkdir(parents=True, exist_ok=True)

SPS = phy.SPS
FS = 16e6
FC = 2.4e9
L = 127
SNR_DB = -1.0
N_FRAMES = 200
SYNC_WIN = 8 * SPS              # 同步窗: 帧首 8 码片 (64 采样), 与既有 run_sfo/run_quant 同窗口

PPM_GRID = [20, 40, 80]


# ---------------------------------------------------------------------------
# 接收段局部阶段 (制式细节: 保留迁移前的采样边界行为)
# ---------------------------------------------------------------------------

def extract(mf, p, n_chips, off=None):
    """码片软值采样: idx = 峰 + m·sps + (m 奇 ? sps/2 : 0) + sps-1。

    off 为逐码片采样偏移 ([C] 阶梯修正用); 帧尾越界用 ``np.clip`` 保护
    (不做补零) —— 与迁移前逐字一致。
    """
    m = np.arange(n_chips)
    idx = p + m * SPS + np.where(m % 2, SPS // 2, 0) + (SPS - 1)
    if off is not None:
        idx = idx + off
    idx = np.clip(idx, 0, len(mf) - 1)          # 帧尾保护
    return np.where(m % 2 == 0, mf[idx], mf[idx] * (-1j))


def stage_cfo_est():
    """前导块间差分 CFO 估计 + 消旋; 估计值写入 ``meta['cfo_est']`` 供后续阶段取用。

    用 ``phy.estimate_cfo_preamble_diff`` 单独组阶段 (chains.stage_cfo_correct 的
    'preamble_diff' 分支把该函数的返回值当元组解包, 与实现不符 -> 见报告「共享库缺口」)。
    """
    def apply(sig):
        f = float(phy.estimate_cfo_preamble_diff(sig.rx))
        sig.meta["cfo_est"] = f
        sig.rx = phy.correct_cfo(sig.rx, f)
    apply.__name__ = "cfo_est+derotate(preamble_diff)"
    apply.__wrapped__ = phy.estimate_cfo_preamble_diff
    return apply


def stage_sample(method):
    """定时采样 + [C] 码片域阶梯修正: ``meta['mf']`` / ``meta['sync']`` → ``rx_chips``。

    method='step' 读 ``meta['cfo_est']`` 算 eps_est = cfo_est/FC·1e6, 逐码片偏移
    ``round(eps_est·1e-6·(m·chip_dur)·fs)`` —— 偏移随码片序号线性累积;
    40ppm 下约每 3125 码片 (≈98 符号) +1 采样, 127B 整帧 (8640 码片) 累计约 2.8 采样。
    'none'/'interp' 不做码片域偏移。
    """
    def apply(sig):
        n_chips = len(sig.symbols) * 32
        off = None
        if method == "step":
            eps = sig.meta["cfo_est"] / FC * 1e6
            m = np.arange(n_chips)
            off = np.round(eps * 1e-6 * (m * 0.5e-6) * FS).astype(int)
        sig.rx_chips = extract(sig.meta["mf"], sig.meta["sync"], n_chips, off)
    apply.__name__ = f"sample_chips({method})"
    return apply


# ---------------------------------------------------------------------------
# 链路配方 (扫参点内复用同一对象; 阶段无状态, rng 由闭包持有以保证可复现)
# ---------------------------------------------------------------------------

def build_chain(method, ppm, rng):
    """按方案组装链路: tx(完整链) + [SFO, CFO, AWGN] + 接收段 ([B] 插逆重采样)。"""
    c = chains.Chain(name=f"sfo_fix[{method}@{ppm}ppm]")
    c.then_tx(*chains.tx_stages(full=True, whiten=True))
    c.then_channel(imp.sfo_stage(ppm),
                   imp.cfo_stage(FC * ppm / 1e6),      # 同源 ε: CFO = ε·FC
                   chains.awgn(SNR_DB, rng))
    c.then_rx(stage_cfo_est())
    if method == "interp":
        # 连续逆重采样 (性能上界): ppm 由前序阶段的 cfo_est 动态求值, MF/同步随后重算
        c.then_rx(stage(imp.add_sfo, name="inv_sfo",
                        ppm=lambda sig: -sig.meta["cfo_est"] / FC * 1e6))
    c.then_rx(chains.stage_matched_filter(),
              chains.stage_sync("window", win=SYNC_WIN - 1),
              stage_sample(method),
              chains.stage_despread())
    return c


def run(method, ppm, rng):
    """method: 'none' | 'interp' | 'step' —— 返回该点 BER (固定 N_FRAMES 帧, 无早停)。"""
    chain = build_chain(method, ppm, rng)
    err = tot = 0
    for _ in range(N_FRAMES):
        psdu = bytes(rng.integers(0, 256, size=L).tolist())
        sig = chain.run(payload=psdu)
        e, t = measure.frame_bit_errors(sig)        # 同步失败帧按整帧计错
        err += e
        tot += t
    return err / tot


def main():
    rng = np.random.default_rng(555)

    print(f"PSDU {L}B, SNR {SNR_DB} dB, {N_FRAMES} frames/point")
    results = {}
    for method, name in [("none", "[A] derotate only"), ("interp", "[B] continuous inverse resampling"),
                         ("step", "[C] stepped phase switching")]:
        b = [run(method, p_, rng) for p_ in PPM_GRID]
        results[name] = b
        print(f"{name:20s} " + "  ".join(f"@{p_:>3}ppm {x:.2e}" for p_, x in zip(PPM_GRID, b)))

    # 阶梯示意 (打印)
    print("\n[C] step illustration (40ppm): per-chip sample offset = round(ε·t_m·fs)")
    m = np.arange(0, 240, 20)
    off = np.round(40e-6 * (m * 0.5e-6) * FS).astype(int)
    print("  chip index: " + " ".join(f"{v:>4}" for v in m))
    print("  sample offset: " + " ".join(f"{v:>4}" for v in off))

    fig, ax = plt.subplots(figsize=(10, 5.5))
    x = np.arange(len(PPM_GRID))
    w = 0.26
    for i, (name, b) in enumerate(results.items()):
        ax.bar(x + (i - 1) * w, b, width=w, label=name)
    ax.set_xticks(x)
    ax.set_xticklabels([f"{p_} ppm" for p_ in PPM_GRID])
    ax.set_yscale("log")
    ax.set_ylabel("BER")
    ax.set_title(f"SFO fix scheme comparison (PSDU {L}B, SNR {SNR_DB} dB, same-source CFO+SFO)")
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3, axis="y")
    out = OUT / "imp_sfo_fix.png"
    fig.savefig(out, dpi=130)
    print(f"\nsaved: {out}")


if __name__ == "__main__":
    main()
