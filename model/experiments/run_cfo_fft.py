#!/usr/bin/env python
"""run_cfo_fft.py —— 单帧 CFO 估计: 256 点 FFT（模式发现的核心工具）

背景（docs/08 I-7 与 report §10）:
  资源投入应与"不确定度 x 复用次数"成正比。CFO 是唯一同时"高不确定度
  (±500 kHz) x 高复用次数(数千帧)"的参数, 却用了最便宜的差分累加 —— 代价是
  冷启动要 ~15 帧碰运气。

本实验: 用已知前导去调制后做 256 点 FFT (码片级, 等效采样率 = 码片率 2 MHz)。
  z[m] = v[m] · conj(c_ref[m])       (v = 码片峰值软值, c_ref = 理想前导 ROM)
  z 应是纯旋转 A·e^{j·w·8m}  => FFT 峰位给出 w, 无模糊 ±1 MHz, 分辨率 7.8 kHz。

两个关键细节 (踩过的坑):
  1. **抽取相位**必须选对 (16 个候选里只有 1~2 个落在码片幅度峰上);
     选择判据用 **谱锐度(峰值/次峰)** 而不是平均幅度 —— 幅度大不代表相位对,
     实测用幅度选会让所有点退化到 10/20。
  2. 谱锐度同时是**质量指标**: 锐度低的帧估计不可信, 可由上层拒绝或重试。

输出: model/out/cfo_fft/report.md
"""
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tb" / "rx_chain_e2e"))
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "model" / "experiments"))

import mc_gen            # noqa: E402
import phy_802154 as phy  # noqa: E402

OUT = ROOT / "model" / "out" / "cfo_fft"
N_FRAMES = 20
SNR = 20
CFOS = [0.0, 10e3, 50e3, 100e3, 200e3, 449e3]
FS = 16e6
NCHIP = 256
SPS = 8
BIN_HZ = FS / (NCHIP * SPS)     # 7.8125 kHz


def load_cref():
    """解析 rtl/rx/c_ref_rom.svh -> (256,) 复数码片软值。"""
    txt = (ROOT / "rtl" / "rx" / "c_ref_rom.svh").read_text()
    out = {}
    for name in ("C_REF_I", "C_REF_Q"):
        m = re.search(name + r"\s*\[0:255\]\s*=\s*'\{(.*?)\};", txt, re.S)
        vals = re.findall(r"16'sh([0-9A-Fa-f]+)", m.group(1))
        v = np.array([int(x, 16) for x in vals], dtype=np.int64)
        v = np.where(v >= 2**15, v - 2**16, v)
        out[name] = v.astype(np.float64)
    return out["C_REF_I"] + 1j * out["C_REF_Q"]


def gen(snr, cfo_hz, n_frames=N_FRAMES, seed=1):
    pdir = OUT / "_sig" / f"{snr}_{int(cfo_hz)}"
    pdir.mkdir(parents=True, exist_ok=True)
    mc_gen.gen_point(snr, n_frames, 20, 8.0, seed, 400, 600, pdir,
                     gap_jitter=16, cfo_hz=cfo_hz)
    raw = np.fromfile(pdir / "mem.bin", dtype=">u4")
    i = (raw & 0xFFF).astype(np.int64)
    q = ((raw >> 12) & 0xFFF).astype(np.int64)
    i = np.where(i >= 2048, i - 4096, i)
    q = np.where(q >= 2048, q - 4096, q)
    fs = np.load(pdir / "frames.npz")["frame_start"]
    return i + 1j * q, fs


WIN = np.hanning(NCHIP)


def fft_cfo(mf, f0, cref):
    """扫 24 个抽取相位, 用**众数**汇总 (而非谱锐度)。

    实测 (逐 offset 扫描): 正确的估计值会在连续 11 个 offset (码片峰所在的一段)
    重复出现, 而错误值散落在边缘且每种只出现 2~5 次。谱锐度与正确性**不相关**
    (449 kHz 时锐度仅 1.0 但估计正确; 200 kHz 时锐度最高的 off=0 反而错),
    因此用众数表决。这也可看作"帧内多假设"——与跨帧累积同一思路。
    """
    ests = []
    for off in range(24):                    # 3 个码片范围
        idx = f0 + off + SPS * np.arange(NCHIP)
        if idx[-1] >= len(mf):
            continue
        z = mf[idx] * np.conj(cref) * WIN
        sp = np.abs(np.fft.fft(z)) ** 2
        k = int(np.argmax(sp))
        kh = k if k <= NCHIP // 2 else k - NCHIP
        ests.append(kh * BIN_HZ)
    if not ests:
        return None, 0.0
    bins = np.round(np.array(ests) / BIN_HZ).astype(int)
    vals, cnt = np.unique(bins, return_counts=True)
    j = int(np.argmax(cnt))
    return vals[j] * BIN_HZ, cnt[j] / len(bins)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    cref = load_cref()
    print(f"c_ref 载入: {len(cref)} 项, |c_ref| 中位 {np.median(np.abs(cref)):.0f}")
    print(f"FFT bin -> CFO: {BIN_HZ/1e3:.4f} kHz; 无模糊 ±{NCHIP//2*BIN_HZ/1e6:.2f} MHz\n")
    rows = []
    print(f"{'CFO真值':>9} | {'估计中位':>10} {'误差':>8} {'抖动':>8} {'众数占比':>9} "
          f"{'全部命中':>9} {'众数占比>0.3 里命中':>18}")
    print("-" * 86)
    for cfo in CFOS:
        v, fs = gen(SNR, cfo)
        mf = phy.matched_filter(v)
        ests, rats = [], []
        for f0 in fs:
            f0 = int(f0)
            if f0 < 64 or f0 + 24 + SPS * NCHIP >= len(mf):
                continue
            e, r = fft_cfo(mf, f0, cref)
            if e is None:
                continue
            ests.append(e); rats.append(r)
        ests = np.array(ests); rats = np.array(rats)
        good = np.abs(ests - cfo) <= 8e3
        hi = rats > 0.3
        med = np.median(ests) if len(ests) else float("nan")
        jit = (ests.max() - ests.min()) if len(ests) else 0
        hr = f"{int(np.sum(good & hi))}/{int(np.sum(hi))}" if hi.any() else "0/0"
        print(f"{cfo/1e3:>7.1f}k | {med/1e3:>10.1f} {(med-cfo)/1e3:>8.1f} "
              f"{jit/1e3:>8.1f} {np.median(rats):>9.2f} "
              f"{int(good.sum()):>4}/{len(ests):<4} {hr:>18}")
        rows.append((cfo, med, jit, float(np.median(rats)), int(good.sum()), len(ests), hr))

    md = ["# 单帧 CFO 估计: 256 点 FFT（码片级）", "",
          f"SNR = +{SNR} dB，{N_FRAMES} 帧/点；`z[m] = v[m]·conj(c_ref[m])` 后 256 点 FFT。",
          f"bin = {BIN_HZ/1e3:.4f} kHz，无模糊 ±{NCHIP//2*BIN_HZ/1e6:.2f} MHz（覆盖 ±500 kHz 需求）。",
          "抽取相位用**众数表决**（正确值在连续 11 个 offset 上重复出现）；众数占比可作质量指标。", "",
          "| CFO 真值 | 估计中位 | 误差 | 帧间抖动 | 众数占比中位 | 全部命中 | 众数占比>0.3 里命中 |",
          "|---|---|---|---|---|---|---|"]
    for cfo, med, jit, r, ng, n, hr in rows:
        md.append(f"| {cfo/1e3:.0f} kHz | {med/1e3:.1f} kHz | {(med-cfo)/1e3:+.1f} kHz | "
                  f"{jit/1e3:.1f} kHz | {r:.2f} | {ng}/{n} | {hr} |")
    md += ["", "对照：差分法（`cfo_est`）在同样条件下需要 ~15 帧才碰运气收敛一次",
           "（report §9.5：前 14 次估计全废）。"]
    (OUT / "report.md").write_text("\n".join(md) + "\n")
    print(f"\n[report] {OUT/'report.md'}")


if __name__ == "__main__":
    main()
