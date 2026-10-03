#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""run_adc_gear.py v2 —— ADC 档位调节：鸡生蛋的不对称解 + 单调场景验证。

设计原则（验证对象）:
  · 进入低档（降精度省电）**仅由高精度状态发起**（先看清、再降；禁止低档自行降档）;
  · 离开低档（升精度）：失败类信号（FCS/同步失败——低精度下依然可信）或 TTL 复查;
  · TTL 复查回高档重评——"保持低档"同样需要高精度证据。

模型（示意+锚点; 锚点=RTL 实测: snr20 全档 60/60、2bit 断 23/60、snr6 全档 ~15-16/60）:
  snr_eff = -10log10(10^(-snr_env/10) + 10^(-SQNR/10)), SQNR=6.02N+1.76
  P_frame = logistic(snr_eff)  [校准 P(20)=0.99, P(6)=0.25]; bits<=2 地板 0.38。

能耗: 每拍 = P_dig(1.0) + P_adc{12:1, 8:0.25, 4:0.0625}（每降4bit≈4× 中值口径）。
场景: 单调下降（20→2dB, -2dB/100帧）+ 保持段 + **回升段**（恢复, 验证重降档）。
"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'model' / 'out' / 'adc_gear'
OUT.mkdir(parents=True, exist_ok=True)

GEARS = [12, 8, 4]
P_ADC = {12: 1.0, 8: 0.25, 4: 0.0625}
P_DIG = 1.0
K_FAIL = 3
TTL = 200


def sqnr(bits):
    return 6.02 * bits + 1.76


def snr_eff(snr_env, bits):
    if bits <= 2:
        return snr_env
    return -10 * np.log10(10 ** (-snr_env / 10) + 10 ** (-sqnr(bits) / 10))


def p_frame(snr_env, bits):
    se = snr_eff(snr_env, bits)
    p = 1.0 / (1.0 + np.exp(-(se - 8.699) / 2.457))
    if bits <= 2:
        p = min(p, 0.38)
    return p


def est_snr(snr_env, rng):
    return snr_env + rng.normal(0, 0.5)


def run_policy(policy, snr_seq, rng, th8=18.0, th4=20.0):
    gear, conf_fail, ttl = 12, 0, 0
    e_sum, n_fail = 0.0, 0
    seg_fail = {'hi': 0, 'mid': 0, 'lo': 0}
    seg_n = {'hi': 0, 'mid': 0, 'lo': 0}
    for snr in snr_seq:
        seg = 'hi' if snr >= 14 else ('mid' if snr >= 8 else 'lo')
        seg_n[seg] += 1
        if policy == 'oracle':
            cand = [g for g in GEARS if p_frame(snr, g) >= 0.95]
            gear = min(cand) if cand else 12
        elif policy == 'gear':
            if gear == 12:
                e = est_snr(snr, rng)
                if e > th4:
                    gear = 4
                elif e > th8:
                    gear = 8
                ttl = 0
            else:
                ttl += 1
                if conf_fail >= K_FAIL:
                    gear = 8 if gear == 4 else 12
                    conf_fail = 0
                    ttl = 0
                elif ttl >= TTL:
                    gear = 12                      # 复查 = 无条件回顶（低档不能自评;
                    ttl = 0                        # 回顶后由常规评估重新降档）
        ok = rng.random() < p_frame(snr, gear)
        if not ok:
            conf_fail += 1
            n_fail += 1
            seg_fail[seg] += 1
        else:
            conf_fail = 0
        e_sum += P_DIG + P_ADC[gear]
    return dict(avg_p=e_sum / len(snr_seq), n_fail=n_fail,
                seg_fail=seg_fail, seg_n=seg_n)


def scenario():
    return ([20.0] * 300
            + list(np.repeat(np.arange(18.0, 1.9, -2.0), 100))
            + [2.0] * 300
            + [12.0] * 100 + [20.0] * 200)          # 回升段（末段回顶峰, 验证"重新降档"）


def main():
    snr_seq = scenario()
    print(f"场景: {len(snr_seq)} 帧（20→2dB 单调下降 + 回升至 12dB）; 20 seeds 平均")
    print("口径: 12bit 每拍总功率=2.0（P_dig 1.0 + P_adc 1.0）为基准\n")

    configs = [
        ("gear(默认 th8=18/th4=20)", dict(th8=18.0, th4=20.0)),
        ("gear(保守 th8=20/th4=22)", dict(th8=20.0, th4=22.0)),
        ("gear(激进 th8=16/th4=18)", dict(th8=16.0, th4=18.0)),
    ]
    policies = ['fix12', 'oracle']
    rows = {}
    for seed in range(20):
        rng = np.random.default_rng(seed)
        for p in policies:
            rows.setdefault(p, []).append(run_policy(p, snr_seq, rng))
        for name, kw in configs:
            rows.setdefault(name, []).append(run_policy('gear', snr_seq, rng, **kw))

    print(f"{'策略':<28}{'平均功耗':>9}{'/fix12':>8}{'总失败':>8}{'高段失败':>9}{'中段失败':>9}")
    base = np.mean([r['avg_p'] for r in rows['fix12']])
    for name in ['fix12'] + [c[0] for c in configs] + ['oracle']:
        rs = rows[name]
        ap = np.mean([r['avg_p'] for r in rs])
        nf = np.mean([r['n_fail'] for r in rs])
        hf = np.mean([r['seg_fail']['hi'] for r in rs])
        mf = np.mean([r['seg_fail']['mid'] for r in rs])
        print(f"{name:<28}{ap:>9.3f}{ap/base:>8.3f}{nf:>8.1f}{hf:>9.1f}{mf:>9.1f}")
    print("\n（高段=环境≥14dB：此段失败=策略引入; 低段(<8dB)环境本身不可收, 失败正常）")

    # 轨迹（默认配置）
    rng = np.random.default_rng(0)
    trace, gear, cf, ttl = [], 12, 0, 0
    for snr in snr_seq:
        if gear == 12:
            e = est_snr(snr, rng)
            gear = 4 if e > 20.0 else (8 if e > 18.0 else 12)
            ttl = 0
        else:
            ttl += 1
            if cf >= K_FAIL:
                gear, cf, ttl = (8 if gear == 4 else 12), 0, 0
            elif ttl >= TTL:
                e = est_snr(snr, rng)
                if gear == 8 and e > 20.0:
                    gear = 4
                elif e <= 16.0:
                    gear = 12
                ttl = 0
        ok = rng.random() < p_frame(snr, gear)
        cf = 0 if ok else cf + 1
        trace.append(gear)
    line = ' '.join(str(trace[i]) for i in range(0, len(trace), 100))
    print(f"轨迹（默认配置; 每100帧采样; 末段为回升至 20dB——验证重新降档）:\n  {line}")

    with open(OUT / 'summary.txt', 'w', encoding='utf-8') as f:
        f.write("policy,avg_p,fix12_ratio,total_fail,hi_seg_fail,mid_seg_fail\n")
        for name in ['fix12'] + [c[0] for c in configs] + ['oracle']:
            rs = rows[name]
            ap = np.mean([r['avg_p'] for r in rs])
            f.write(f"{name},{ap:.4f},{ap/base:.3f},"
                    f"{np.mean([r['n_fail'] for r in rs]):.1f},"
                    f"{np.mean([r['seg_fail']['hi'] for r in rs]):.1f},"
                    f"{np.mean([r['seg_fail']['mid'] for r in rs]):.1f}\n")
    print(f"\n（摘要 → {OUT}/summary.txt）")


if __name__ == '__main__':
    main()
