# -*- coding: utf-8 -*-
"""
run_frontend.py —— 前端损伤闭环验证 (Feature 1 & 2)
=====================================================
验证 docs/03 §4 的算法机制是否满足设计需求:
  - CFO 50 kHz (双端 ppm 典型) 存在时, 两级估计后 BER 回到基线, 残差 ≤ 7 kHz
  - DC (0.35+0.30j) + I/Q 不平衡 (2 dB, 10°) 存在时, 联合 LS 校正后 BER 回到基线
  - 组合场景: CFO + DC + IQ 同时存在

链路 (由 chains 库组装, 见 model/chains.py):
  载荷 → 组帧(简化链) → 扩频 → O-QPSK 成形
       → AWGN → IQ 不平衡 → DC 偏移 → CFO          (信道段)
       → [两级 CFO 估计 + 消旋] → 匹配滤波 → 同步 → 采样 → [DC/IQ 联合 LS 校正] → 解扩
  注意: 损伤顺序 AWGN → IQ → DC → CFO 是本实验的既有约定 (区别于 chains
  docstring 的推荐顺序), 改动会改变各损伤的相互作用, 必须保持。

本脚本只负责「挑配方 + 扫参数 + 出报告」, 帧循环与同步/校正逻辑全在 chains 内。

运行: python model/experiments/run_frontend.py
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


PSDU_LEN = 20
N_FRAMES = 300
SNR = -1.0

# 损伤参数 (以信号 RMS=1 标定)
CFO_HZ = 50e3
DC_I, DC_Q = 0.35, 0.30
IQ_GAIN_DB, IQ_PHASE_DEG = 2.0, 10.0
MF_GAIN = 4.0                       # 匹配滤波峰值增益 Σh² (= SPS/2), 估计值归一化用


def build_chain(rng, use_cfo=False, use_dciq=False, correct=True):
    """按开关组装本场景的链路 (损伤顺序: AWGN → IQ → DC → CFO)。

    correct=True  接收机全功能: 两级 CFO 估计 + 消旋, window 同步 (帧起点在最前面,
                  只在帧首附近搜索), 需要时做 DC/IQ 联合 LS 校正;
    correct=False 校正全关: 不做 CFO 估计, honest 全搜索同步, 不校正 DC/IQ。
    """
    stages = [chains.awgn(SNR, rng)]
    if use_dciq:
        stages += [imp.iq_stage(IQ_GAIN_DB, IQ_PHASE_DEG),
                   imp.dc_stage(DC_I, DC_Q)]
    if use_cfo:
        stages.append(imp.cfo_stage(CFO_HZ))
    return chains.link(stages, full=False,
                       cfo=("two_stage" if (use_cfo and correct) else None),
                       sync=("window" if correct else "honest"),
                       win=8 * phy.SPS - 1,
                       dc_iq=(use_dciq and correct))


def run_scenario(rng, use_cfo=False, use_dciq=False, correct=True):
    """返回 (ber, sync_fail, cfo_res_list, est_list)

    链路对象在场景内复用 (阶段无状态; rng 由闭包持有以保证可复现);
    比特误码统计走 measure.frame_bit_errors —— 与迁移前同一口径:
    同步失败 (帧尾越界) 整帧按 4·n_sym 计错。
    """
    chain = build_chain(rng, use_cfo, use_dciq, correct)
    true_cfo = CFO_HZ if use_cfo else 0.0
    bit_err = bit_tot = sync_fail = 0
    cfo_res, ests = [], []
    for _ in range(N_FRAMES):
        psdu = bytes(rng.integers(0, 256, size=PSDU_LEN).tolist())
        sig = chain.run(payload=psdu)
        e, t = measure.frame_bit_errors(sig)
        bit_err += e
        bit_tot += t
        if use_cfo and correct:                 # 残差在同步判定之前收集 (与迁移前一致)
            cfo_res.append(abs(sig.meta["cfo_est"] - true_cfo))
        if sig.meta.get("sync_fail"):           # 同步失败帧不计估计量 (迁移前 continue 的副作用)
            sync_fail += 1
            continue
        if "dc_iq" in sig.meta:                 # DC/IQ 联合 LS 估计 (前导为参考)
            alpha, beta, d = sig.meta["dc_iq"]
            ests.append((abs(alpha) / MF_GAIN - 1, abs(beta) / MF_GAIN,
                         abs(d) / MF_GAIN))     # 归一化: MF 增益 Σh²=4
    return bit_err / bit_tot, sync_fail, cfo_res, ests


def main():
    phy._selftest()
    rng = np.random.default_rng(555)
    print(f"Fixed {N_FRAMES} frames/config, PSDU={PSDU_LEN} B, chip SNR={SNR} dB")
    print(f"Impairments: CFO={CFO_HZ/1e3:.0f} kHz, DC=({DC_I},{DC_Q}), IQ={IQ_GAIN_DB} dB/{IQ_PHASE_DEG}°\n")

    def show(name, r):
        ber, sf, cres, ests = r
        line = f"{name:<34} BER={ber:.3e}  sync fail={sf}"
        if cres:
            line += f"  CFO residual(mean/max)={np.mean(cres)/1e3:.2f}/{np.max(cres)/1e3:.2f} kHz"
        if ests:
            a, b, d = np.mean(ests, axis=0)
            line += f"  |α-1|={a:.3f} |β|={b:.3f} |d|={d:.3f}"
        print(line)
        return ber

    ref = show("1 baseline AWGN", run_scenario(rng))
    show("2 CFO 50k, no correction", run_scenario(rng, use_cfo=True, correct=False))
    show("3 CFO 50k, two-stage estimate+correct", run_scenario(rng, use_cfo=True))
    show("4 DC+IQ, no correction", run_scenario(rng, use_dciq=True, correct=False))
    show("5 DC+IQ, joint LS correction", run_scenario(rng, use_dciq=True))
    show("6 CFO+DC+IQ, all corrected", run_scenario(rng, use_cfo=True, use_dciq=True))

    print("\n== Verdict ==")
    print(f"CFO estimation residual requirement ≤ 7 kHz; scenario 6 combined result in table above (baseline BER reference {ref:.2e})")


if __name__ == "__main__":
    main()
