#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""power_est.py —— 无时钟树盲估：两项模型 + k 区间敏感性（排序保真检验）。

模型（每模块）:
    W_i = (cells_i - dff_i) * alpha_dat_i + k * dff_i * alpha_clk_i
  - alpha_dat: 数据活动节拍（STATS 实测口径, notes §44）
  - alpha_clk: 时钟门控状态（ACTIVE=1; LISTEN 不在允许集 => 整项归零）
  - k: 每个 dff 的等效活动成本（时钟树+翻转），单位=每 cell 组合活动。
       推荐物理区间 [2,8]（时钟树占动态 20-40% 的典型域）；[1,16] 作极端透明扫描。

承诺（无需时钟树）: 不追求绝对值; 要求 —— 在 k 区间内:
  · "A >> B"（>=5x）的排序不翻转;
  · 翻转只允许出现在"接近平局"（<=3x）的模块对。
边界（如实）: 绝对值可能与真实含时钟树报告差数倍；模块排序由本表承担，绝对数不承诺。
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
K_MAIN = [2, 4, 8]        # 推荐物理区间
K_EXTREME = [1, 16]       # 极端透明扫描
K_DEFAULT = 4
RATIO_BIG = 5.0
RATIO_TIE = 3.0
ALLOW = {"rx_matched_filter", "preamble_detect"}   # LISTEN 唯一允许集


def w_of(cells, dff, a_dat, k, a_clk=1.0):
    return (cells - dff) * a_dat + k * dff * a_clk


def rank(k, listen=False, a_dat_override=None):
    rows = []
    for name, cells, dff, a_dat, _ in MODULES:
        if listen and name not in ALLOW:
            rows.append((name, 0.0))
            continue
        ad = a_dat if a_dat_override is None else a_dat_override.get(name, a_dat)
        rows.append((name, w_of(cells, dff, ad, k)))
    return sorted(rows, key=lambda r: -r[1])


def flips(klist, listen=False, a_dat_override=None):
    ranks = {k: dict(rank(k, listen, a_dat_override)) for k in klist}
    out = []
    names = [m[0] for m in MODULES]
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            ni, nj = names[i], names[j]
            gt = [1 if ranks[k][ni] > ranks[k][nj] else 0 for k in klist]
            if len(set(gt)) > 1:
                rr = []
                for k in klist:
                    hi, lo = max(ranks[k][ni], ranks[k][nj]), max(min(ranks[k][ni], ranks[k][nj]), 1e-9)
                    rr.append(hi / lo)
                out.append((ni, nj, min(rr), max(rr)))
    return out


def main():
    print("=== 两项模型盲估：k 区间排序表（ACTIVE）===\n")
    ranks = {k: rank(k) for k in K_MAIN + K_EXTREME}
    names = [n for n, _ in ranks[K_DEFAULT]]
    hdr = "  ".join(f"k={k}" + ("*" if k in K_MAIN else "") for k in [1, 2, 4, 8, 16])
    print(f"{'模块':<22} {hdr}")
    for nm in names:
        vals = []
        for k in [1, 2, 4, 8, 16]:
            d = dict(rank(k))
            vals.append(f"{d[nm]:8.0f}")
        print(f"{nm:<22} " + " ".join(vals))
    print("  （* = 推荐物理区间 [2,8]）")

    print("\n=== 排序翻转检测 ===")
    print("[推荐区间 k∈2..8]")
    fs = flips(K_MAIN)
    if not fs:
        print("  无翻转 ✓")
    for ni, nj, wmin, wmax in fs:
        sev = "!! 数量级对翻转" if wmax >= RATIO_BIG else ("（接近平局, 允许）" if wmax < RATIO_TIE else "（中等, 注意）")
        print(f"  {ni} <-> {nj}: 相对比 [{wmin:.1f}x, {wmax:.1f}x] {sev}")
    print("[极端扫描 k∈1..16]")
    fs = flips(K_MAIN + K_EXTREME)
    if not fs:
        print("  无翻转 ✓")
    for ni, nj, wmin, wmax in fs:
        sev = "!! 数量级对翻转" if wmax >= RATIO_BIG else ("（接近平局, 允许）" if wmax < RATIO_TIE else "（中等, 注意）")
        print(f"  {ni} <-> {nj}: 相对比 [{wmin:.1f}x, {wmax:.1f}x] {sev}")

    print(f"\n=== k={K_DEFAULT} 默认排序（ACTIVE, 占比）===")
    tot = sum(w for _, w in ranks[K_DEFAULT])
    for i, (nm, w) in enumerate(ranks[K_DEFAULT], 1):
        print(f"{i:>2}. {nm:<22} {w:8.0f}   {100*w/tot:5.1f}%")

    print("\n=== LISTEN 允许态（不在允许集 -> 整项归零）===")
    rl = rank(K_DEFAULT, listen=True)
    tot_l = sum(w for _, w in rl)
    for nm, w in rl:
        if w > 0:
            print(f"   {nm:<22} {w:8.0f}   {100*w/tot_l:5.1f}%")
    print(f"   允许态合计 {tot_l:.0f}; ACTIVE 合计 {tot:.0f}; 比 {100*tot_l/tot:.1f}%")

    print("\n=== pbuf 使用模型敏感性（α_dat: 1.0 设计 / 0.013 实测无clr）===")
    for k in [2, 4, 8]:
        d1 = dict(rank(k)); d2 = dict(rank(k, a_dat_override={"preamble_buf": 0.013}))
        r1 = [n for n, _ in rank(k)].index('preamble_buf') + 1
        r2 = [n for n, _ in rank(k, a_dat_override={"preamble_buf": 0.013})].index('preamble_buf') + 1
        print(f"  k={k}: pbuf {d1['preamble_buf']:7.0f} (设计, 第{r1}位)  vs  {d2['preamble_buf']:5.0f} (实测模型, 第{r2}位)")


if __name__ == "__main__":
    main()
