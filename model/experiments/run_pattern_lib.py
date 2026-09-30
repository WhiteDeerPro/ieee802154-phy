#!/usr/bin/env python
"""run_pattern_lib.py —— 模式库验证: 多设备帧流下的匹配/发现/淘汰

场景 (用户提出的架构): 网络拓扑稳定 ⇒ 一个节点只与受限数量的对端通信
⇒ 它看到的物理模式是**有限集且切换不频繁**。于是接收链可以从"每帧盲估"
改成"模式库 + 匹配"。

本实验: 造 K 个设备 (不同 CFO = 不同"旋性"), 把它们的帧**随机交错**成一个流,
逐帧喂给 PatternLibrary, 统计:
  · match / discover / fail 的比例 (成本分布)
  · 模式表何时收敛到 K 个 (冷启动时长)
  · 误配率 (匹配到的模式 cfo 与实际设备 cfo 相差超容差)
  · 模式的 hits 分布 (复用次数 ⇒ 摊销成本)

输出: model/out/pattern_lib/report.md
"""
import random
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tb" / "rx_chain_e2e"))
sys.path.insert(0, str(ROOT / "model"))

import _common                      # noqa: E402
import mc_gen                       # noqa: E402
import phy_802154 as phy            # noqa: E402
from upper.patterns import PatternLibrary, load_reference   # noqa: E402

OUT = _common.out_dir("pattern_lib")       # model/out/pattern_lib/（已创建）
SNR = 20
DEV_CFOS = [0.0, 50e3, 150e3, 350e3]        # 4 台设备
FRAMES_PER_DEV = 25
TOL_HZ = 8e3                                # 判"匹配到正确设备"的容差
SEED = 7


def gen_device(cfo, n_frames, seed):
    pdir = OUT / "_sig" / f"{int(cfo)}"
    pdir.mkdir(parents=True, exist_ok=True)
    meta = mc_gen.gen_point(SNR, n_frames, 20, 8.0, seed, 400, 600, pdir,
                            gap_jitter=16, cfo_hz=cfo)
    raw = np.fromfile(pdir / "mem.bin", dtype=">u4")
    i = (raw & 0xFFF).astype(np.int64)
    q = ((raw >> 12) & 0xFFF).astype(np.int64)
    i = np.where(i >= 2048, i - 4096, i)
    q = np.where(q >= 2048, q - 4096, q)
    mf = phy.matched_filter(i + 1j * q)
    fs = np.load(pdir / "frames.npz")["frame_start"]
    return mf, fs


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    cref = load_reference()
    devs = []
    for k, cfo in enumerate(DEV_CFOS):
        mf, fs = gen_device(cfo, FRAMES_PER_DEV, seed=SEED + k)
        devs.append((cfo, mf, fs))
    print(f"生成 {len(devs)} 台设备 x {FRAMES_PER_DEV} 帧; 真值旋性: "
          + ", ".join(f"{c/1e3:+.0f}k" for c, _, _ in devs))

    # 随机交错帧流 (模拟"哪个设备在说话"是随机的)
    stream = []
    for d, (cfo, mf, fs) in enumerate(devs):
        for f0 in fs:
            stream.append((d, cfo, mf, int(f0)))
    random.Random(SEED).shuffle(stream)

    lib = PatternLibrary(cref, sps=8, fs=16e6, max_patterns=len(devs) + 2,
                         match_th=0.45, age_frames=10_000, disc_offsets=24)
    actions, wrong, hits_log, n_pat_log = [], [], [], []
    print(f"\n{'帧':>4} {'动作':>9} {'真值':>8} {'匹配到':>16} {'分数':>6} 模式数")
    print("-" * 66)
    for idx, (d, cfo_true, mf, f0) in enumerate(stream):
        p, s, act = lib.observe(mf, f0, frame_idx=idx)
        actions.append(act)
        if p is not None:
            err = abs(p.cfo - cfo_true)
            wrong.append(err > TOL_HZ)
            if idx < 14:
                print(f"{idx:>4} {act:>9} {cfo_true/1e3:>7.0f}k {str(p):>16} "
                      f"{s:>6.2f} {len(lib.patterns)}")
        n_pat_log.append(len(lib.patterns))
        hits_log.append(p.hits if p is not None else 0)

    n = len(stream)
    n_match = actions.count("match")
    n_disc = actions.count("discover")
    n_fail = actions.count("fail")
    n_wrong = int(np.sum(wrong))
    conv = next((i for i, v in enumerate(n_pat_log) if v >= len(devs)), None)
    print("-" * 66)
    print(f"总计 {n} 帧: match {n_match} ({n_match/n:.0%})  discover {n_disc}  fail {n_fail}")
    print(f"模式表收敛到 {len(devs)} 个: 第 {conv} 帧" if conv is not None else "未收敛")
    print(f"误配 (|Δcfo| > {TOL_HZ/1e3:.0f}k): {n_wrong}/{n} ({n_wrong/n:.1%})")
    print("\n最终模式表:")
    for p in lib.patterns:
        print(f"  {p}")

    md = ["# 模式库: 多设备帧流下的匹配 / 发现 / 淘汰", "",
          f"{len(devs)} 台设备（真值旋性 {', '.join(f'{c/1e3:+.0f} kHz' for c,_,_ in devs)}），"
          f"各 {FRAMES_PER_DEV} 帧随机交错，共 {n} 帧；SNR +{SNR} dB。",
          f"匹配门限 {lib.match_th}，误配判据 |Δcfo| ≤ {TOL_HZ/1e3:.0f} kHz。", "",
          "## 成本分布", "",
          f"- **match（便宜路径）**：{n_match}/{n} = **{n_match/n:.1%}**",
          f"- **discover（FFT，昂贵路径）**：{n_disc}/{n} = {n_disc/n:.1%}",
          f"- fail：{n_fail}",
          f"- 模式表收敛到 {len(devs)} 个：**第 {conv} 帧**" if conv is not None else "- 未收敛",
          f"- **误配率**：{n_wrong}/{n} = {n_wrong/n:.1%}", "",
          "## 最终模式表", ""]
    for p in lib.patterns:
        md.append(f"- `cfo={p.cfo/1e3:+.1f} kHz, phase={p.phase}, hits={p.hits}, "
                  f"conf={p.conf:.2f}`")
    md += ["", "## 解读", "",
           f"前 {n_disc} 次失配各自触发一次 FFT（{n_disc} x 64 µs 量级，若由节点固件承担），",
           "之后全部走 O(N) 归一化相关的便宜匹配。每个模式被复用 "
           f"{int(np.mean([p.hits for p in lib.patterns]))} 次左右 —— 这正是"
           "\"不确定度 x 复用次数\"里那个摊销因子。"]
    (OUT / "report.md").write_text("\n".join(md) + "\n")
    print(f"\n[report] {OUT/'report.md'}")


if __name__ == "__main__":
    main()
