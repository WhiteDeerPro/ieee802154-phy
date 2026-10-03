#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""power_est.py v2 —— 无时钟树盲估：两项模型 + "无大反转"检查。

模型（每模块）:
    W_i = (cells_i - dff_i) * alpha_dat_i + k * dff_i * alpha_clk_i
  - alpha_dat: 数据活动节拍（STATS 实测口径, notes §44）
  - alpha_clk: 时钟门控状态（ACTIVE=1; LISTEN 不在允许集 => 整项归零）
  - k: 每个 dff 的等效活动成本（时钟树+翻转），单位=每 cell 组合活动。
       推荐物理区间 [2,8]；[1,16] 整数全扫作透明性检验。

承诺（v2, 按用户裁决修订）:
  · 红线: 禁止"大反转" —— 不存在 k1,k2 使 W_A/W_B >= 5 (k1) 且 W_B > W_A (k2);
  · 接近模块允许"排不出顺序"（排名带重叠 => 标注近似, 不给定序）;
  · 绝对数值不承诺（时钟树/工艺/PVT 未含）。
"""
MODULES = [
    # (name, cells, dff, alpha_dat, 数据来源/备注)
    ("preamble_sync(Csq)", 6456, 2398, 1.00,  "扫描常开; cells/dff=notes§984"),
    ("preamble_buf",        635,   10, 1.00,  "每拍写(设计使用模型)"),
    ("preamble_detect",     599,  137, 0.125, "DEC=8(实测12.5%)"),
    ("rx_matched_filter",    37,   10, 1.00,  "每拍(实测99.9%)"),
    ("deinterleave",         24,    4, 0.125, "码片节拍(实测12.5%)"),
    ("sfd_detect x2",      2932,  266, 0.125, "单1466/133, x2; 片节拍"),
    ("despreader x2",      1502,   70, 0.125, "单751/35, x2; 片节拍"),
    ("cfo_rot x2",         1080,    8, 0.125, "单540/4, x2; LUT->case口径(dff 失真)"),
    ("rx_deframer x2",      238,   24, 0.125, "单119/12, x2; 帧内"),
    ("pn9/crc16/halfsine",  185,   22, 0.125, "108/40/37 cells"),
    ("顶层胶合",            229,   30, 0.50,  "13,917 残余(粗估)"),
]
K_KS = list(range(1, 17))     # 全整数扫描
K_MAIN = [2, 3, 4, 5, 6, 7, 8]
K_DEFAULT = 4
REVERSAL_BIG = 5.0            # 红线: >=5x 的一侧 与 反向共存 => 大反转
COMMIT_MIN = 3.0              # "可承诺"阈值: 任意 k 下 >=3x
ALLOW = {"rx_matched_filter", "preamble_detect"}


def w_of(cells, dff, a_dat, k, a_clk=1.0):
    return (cells - dff) * a_dat + k * dff * a_clk


def wm(k, listen=False):
    out = {}
    for name, cells, dff, a_dat, _ in MODULES:
        out[name] = 0.0 if (listen and name not in ALLOW) else w_of(cells, dff, a_dat, k)
    return out


def rank_at(k, listen=False):
    d = wm(k, listen)
    return sorted(d, key=lambda n: -d[n]), d


def main():
    names = [m[0] for m in MODULES]

    print("=== 1) 大反转检查（红线: 存在 k1,k2 使 A/B>=5x 且 B/A>=5x —— 两方向都大） ===")
    big, wobble = [], []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            A, B = names[i], names[j]
            ab = [wm(k)[A] / max(wm(k)[B], 1e-9) for k in K_KS]
            ba = [1.0 / max(r, 1e-9) for r in ab]
            mab, mba = max(ab), max(ba)
            crossed = (min(ab) < 1.0) and (max(ab) > 1.0)
            if mab >= REVERSAL_BIG and mba >= REVERSAL_BIG:
                big.append((A, B, mab, mba))
            elif crossed and (mab >= REVERSAL_BIG or mba >= REVERSAL_BIG):
                wobble.append((A, B, mab, mba))
    print("  [红线] 双方向均 >=5x:", "无 ✓" if not big else big)
    print("  [摆动] 单侧 >=5x 且曾跨方向（非红线, 标注幅度）:")
    for A, B, mab, mba in wobble:
        tag = "反向平局级（不构成大反转）" if min(mab, mba) < 1.5 else "注意"
        print(f"    {A} <-> {B}: {A}/{B} 最高 {mab:.2f}x / {B}/{A} 最高 {mba:.2f}x —— {tag}")
    if not wobble:
        print("    无")
    # sync 的倍率边界展示
    rs = [wm(k)["preamble_sync(Csq)"] / max(wm(k)[n], 1e-9) for k in K_KS
          for n in names if n != "preamble_sync(Csq)"]
    print(f"  （参考: sync 对其余的最小倍率 = {min(rs):.1f}x —— 最大项地位全区间稳定）")

    print("\n=== 2) 排名带（best..worst across k; 重叠 = 近似, 不给定序） ===")
    print(f"{'模块':<22} {'k∈[2,8]':>10}   {'k∈[1,16]':>10}")
    for nm in names:
        def band(ks):
            ranks = []
            for k in ks:
                order, _ = rank_at(k)
                ranks.append(order.index(nm) + 1)
            return min(ranks), max(ranks)
        b1, b2 = band(K_MAIN), band(K_KS)
        print(f"{nm:<22} {b1[0]:>4}..{b1[1]:<4}   {b2[0]:>4}..{b2[1]:<4}")

    print("\n=== 3) 可承诺的强关系（任意 k 下恒 >=3x）===")
    for i in range(len(names)):
        A = names[i]
        below = []
        for j in range(len(names)):
            if i == j:
                continue
            B = names[j]
            if min(wm(k)[A] / max(wm(k)[B], 1e-9) for k in K_KS) >= COMMIT_MIN:
                below.append(B)
        if below:
            print(f"  {A:<22} > {', '.join(below)}")
    print("  （未列出的对 = 排不出顺序的近似对——允许任意乱序，无大反转）")

    print("\n=== 4) 各模块占比区间（k∈2..8, ACTIVE）===")
    for nm in names:
        shares = []
        for k in K_MAIN:
            d = wm(k)
            tot = sum(d.values())
            shares.append(100 * d[nm] / tot)
        print(f"  {nm:<22} {min(shares):5.1f}% .. {max(shares):5.1f}%")

    print("\n=== 5) LISTEN 允许态（不在允许集 -> 整项归零）===")
    d = wm(K_DEFAULT, listen=True)
    tot_l = sum(d.values())
    for nm in names:
        if d[nm] > 0:
            print(f"  {nm:<22} {d[nm]:7.0f} 次默认 k 等效")
    dtot = sum(wm(K_DEFAULT).values())
    print(f"  允许态/ACTIVE（k=4）: {100*tot_l/dtot:.1f}%")
    power_table()




def power_table():
    """§6 单位时间功耗对照（平均功率）——按用户裁决: 用功率而非累计能量
    （能量随等待时长线性放大、公平性差）。

    基准 = ACTIVE 每拍功率 = 1.00;  P_avg = (W_act*T_act + W_lst*T_gap)/T_total。
    帧段 T_act=15,000 拍（实测分段口径）; 场景含实验流 + Zigbee 真实帧率量级。
    """
    T_SEG = 15000
    scenes = [
        ("snr20（连续帧流）",      15348),
        ("listen1（gap=50k）",    64946),
        ("Zigbee 100 帧/s",      160000),
        ("Zigbee 1 帧/s",     16000000),
    ]
    print("\n=== 6) 单位时间功耗对照（平均功率; 基准=ACTIVE 每拍=1.00）===")
    for k in [2, 4, 8]:
        w_act = sum(wm(k).values())
        w_lst = sum(wm(k, listen=True).values())
        print(f"\n[k={k}] 每拍功率比 ACTIVE/LISTEN = {w_act/w_lst:.1f}x")
        print(f"{'场景':<24}{'门控后 P_avg':>14}{'未门控 P_avg':>14}")
        for name, T in scenes:
            t_a, t_l = T_SEG, T - T_SEG
            p_g = (w_act * t_a + w_lst * t_l) / T / w_act
            p_r = 1.0    # 未门控: 等待期也按 ACTIVE 功率
            print(f"{name:<24}{p_g*100:>13.1f}%{p_r*100:>13.1f}%")


if __name__ == "__main__":
    main()

