# -*- coding: utf-8 -*-
"""
run_multipath_eq.py —— 多径均衡 ref 层验证
=============================================
量化「解多径」在黄金模型层的收益边界。三条曲线对比:

  base      : 现状 —— 匹配滤波 → 非相干解扩 (32 码片扩频增益硬扛)
  base_coh  : 无均衡但相干解扩 (无 CFO 时的理想参考, 相位已知)
  eq        : 新增解多径链路 —— 前导 LS 信道估计 → 频域 MMSE 均衡 → 相干解扩

说明:
  · 均衡补偿了信道相位, 故均衡后用相干解扩 (取实部), 不再用非相干幅值检测。
  · base vs eq         = 新增解多径模块带来的端到端增益;
  · base_coh vs eq     = 纯均衡增益 (排除相干/非相干检测方式的差异)。

链路 (由 chains 库组装, 见 model/chains.py —— 本脚本只做配置 + 扫参 + 出图):
  载荷 → 组帧(简化链) → 扩频 → O-QPSK 成形
       → 多径 → AWGN → 匹配滤波 → genie 同步(p=0) → 采样 → [均衡] → 解扩
  · 均衡链: rx_stages(sync="genie", n_taps=L_TAPS, noise_var=chains.eq_noise_var(snr),
                       coherent=True);
  · 三种 mode 只差「是否均衡」与「解扩检测方式」, 见 rx_kw()。
  · 误码统计一律走 measure.frame_bit_errors (同步失败整帧计错), 与原脚本同口径。

扫描三个维度(码片 SNR = -1 dB, genie 同步, 500 帧/点):
  1. 等幅双径 延迟扫描 —— 深衰落陷波, 延迟 0.125 .. 1.5 码片
  2. 等幅 N 径 (0.5 码片间隔) —— 抽头数 2..5
  3. Rayleigh 复抽头 (随机信道逐帧) —— 指数延迟剖面, 最大延迟 0.5 .. 2.0 码片

运行: python model/experiments/run_multipath_eq.py  →  out/impairments/multipath_eq.png
"""

import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
from pathlib import Path

# 本脚本位于 model/experiments/ —— 库模块 (chains / measure / phy_802154 / visualize)
# 在上一级的 model/, 而 Python 只自动把脚本自身目录加入 sys.path, 故显式引导。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import _common
import chains
import measure
import phy_802154 as phy
import visualize
from baseband import impairments as imp

plt = _common.init()                          # Agg 后端 + 中文字体
OUT = _common.out_dir("impairments")          # model/out/impairments/（已创建）

PSDU_LEN = 20
N_FRAMES = 500
L_TAPS = 6                  # 信道估计抽头数 (覆盖 ≤1.5 码片延迟 + 分数延迟泄漏)
SNR_DB = -1.0               # 码片 SNR (解调需求点)


def rx_kw(mode, snr=SNR_DB):
    """三种 mode 的接收段配置 —— 只差「是否均衡」与「解扩检测方式」。

    noise_var 用 chains.eq_noise_var(码片SNR): 波形域 σw² = Σh²/10^(SNR/10),
    经匹配滤波后码片域方差 = σw²·Σh² (与 MMSE 均衡的信道抽头尺度一致) ——
    即原脚本 noise_var_from_snr 的语义, 现已由链路库统一提供。
    """
    if mode == "eq":
        return dict(sync="genie", n_taps=L_TAPS,
                    noise_var=chains.eq_noise_var(snr), coherent=True)
    return dict(sync="genie", coherent=(mode == "base_coh"))


def impairments(mp, snr, rng):
    """损伤段: 多径 (None = 纯 AWGN) → AWGN (按匹配滤波后码片 SNR 标定)。"""
    seg = [] if mp is None else [imp.multipath_stage(mp["gains"], mp["delays"])]
    return seg + [chains.awgn(snr, rng)]


def run_point(snr, rng, mp, mode, mp_getter=None):
    """一个扫参点的 BER。

    mp        静态多径 (扫参点内信道不变) —— 此时**链路对象复用**: 组一次链,
              循环只换载荷 (阶段无状态; rng 由闭包/阶段持有)。
    mp_getter 逐帧随机信道 (Rayleigh 维度) —— 每帧重建链路; 且必须在取载荷
              **之前**抽信道, 以保持与原实现相同的 rng 消耗顺序
              (原 ``_modulate_mf(snr, rng, mp_getter())`` 先算实参)。
    """
    bit_err = bit_tot = 0
    chain = None if mp_getter else chains.link(
        impairments(mp, snr, rng), full=False, **rx_kw(mode, snr))
    for _ in range(N_FRAMES):
        if mp_getter:
            mp = mp_getter()                       # 先抽信道 (消耗 rng)
            chain = chains.link(impairments(mp, snr, rng), full=False,
                                **rx_kw(mode, snr))
        psdu = bytes(rng.integers(0, 256, size=PSDU_LEN).tolist())
        sig = chain.run(payload=psdu)
        e, t = measure.frame_bit_errors(sig)
        bit_err += e
        bit_tot += t
    return bit_err / bit_tot


def sweep(label, xs, mp_factory, rng):
    """扫一个维度: 每个 x 依次跑三种 mode, 返回 (B, BC, E) 三组 BER。

    mp_factory(x) -> (mp, mp_getter): 静态多径给 (dict, None), 逐帧随机给 (None, getter)。
    """
    print(f"\n== {label} @ chip SNR=-1 dB (genie sync, 500 frames/point) ==")
    print(f"{'param':>10} {'baseline(non-coh)':>13} {'baseline(coh)':>13} {'equalized(coh)':>13}")
    B, BC, E = [], [], []
    for x in xs:
        mp, mp_getter = mp_factory(x)
        bb = run_point(SNR_DB, rng, mp, "base", mp_getter)
        bc = run_point(SNR_DB, rng, mp, "base_coh", mp_getter)
        be = run_point(SNR_DB, rng, mp, "eq", mp_getter)
        B.append(max(bb, 1e-9)); BC.append(max(bc, 1e-9)); E.append(max(be, 1e-9))
        print(f"{x:>10.3f} {bb:>13.3e} {bc:>13.3e} {be:>13.3e}")
    return B, BC, E


def main():
    phy._selftest()
    rng = np.random.default_rng(777)
    snr = SNR_DB

    # 扫描 1: 等幅双径延迟 (静态信道 → 链路复用)
    delays = [0.125, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5]
    b1, bc1, e1 = sweep("sweep1: equal-amplitude two-path delay", delays,
                        lambda d: (dict(gains=[1 / np.sqrt(2)] * 2, delays=[0.0, d]), None),
                        rng)

    # 扫描 2: 等幅 N 径 (0.5 码片间隔, 静态信道 → 链路复用)
    ns = [2, 3, 4, 5]
    b2, bc2, e2 = sweep("sweep2: equal-amplitude N paths (0.5 chip spacing)", ns,
                        lambda n: (dict(gains=[1 / np.sqrt(n)] * n,
                                        delays=[0.5 * k for k in range(n)]), None),
                        rng)

    # 扫描 3: Rayleigh 复抽头 —— **逐帧随机信道** (每帧产生新抽头), 故本维度
    # 每帧重建链路 (静态维度仍复用); 抽头须先于载荷抽取以保持 rng 顺序, 见 run_point。
    dmaxs = [0.5, 1.0, 1.5, 2.0]
    def ray_factory(dmax):
        def f():
            g, d = imp.rayleigh_taps(5, dmax, rms_chips=max(dmax / 3, 0.15), rng=rng)
            return dict(gains=g, delays=d)
        return (None, f)
    b3, bc3, e3 = sweep("sweep3: Rayleigh complex taps (5 taps, random per frame)", dmaxs,
                        ray_factory, rng)

    # ---- 图 ----
    def plot(ax, xs, B, BC, E, xlabel, title):
        for ys, lab in ((B, "baseline (non-coherent, current)"),
                        (BC, "baseline (coherent, no-CFO reference)"),
                        (E, "equalized (LS+MMSE, coherent)")):
            visualize.plot_ber_curve(xs, ys, ax=ax, label=lab)
        ax.set_xlabel(xlabel); ax.set_ylabel("BER")
        ax.set_title(title)
        ax.legend(fontsize=8)

    fig, axs = plt.subplots(1, 3, figsize=(16, 4.8))
    plot(axs[0], delays, b1, bc1, e1, "relative delay of equal two paths (chips)", "equal-amplitude two-path delay sweep (deep fading)")
    plot(axs[1], ns, b2, bc2, e2, "equal-amplitude tap count (0.5 chip spacing)", "equal-amplitude N paths")
    plot(axs[2], dmaxs, b3, bc3, e3, "Rayleigh max delay (chips)", "Rayleigh complex taps (5 taps)")

    fig.suptitle(f"Multipath equalization gain (ref golden model, chip SNR={snr} dB, genie sync, 500 frames/point)")
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(OUT / "multipath_eq.png", dpi=130)
    plt.close(fig)
    print(f"\nfigure saved: {OUT / 'multipath_eq.png'}")


if __name__ == "__main__":
    main()
