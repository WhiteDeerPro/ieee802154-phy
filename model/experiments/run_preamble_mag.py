#!/usr/bin/env python
"""run_preamble_mag.py —— 前导检测判据对比: 实部 vs 复相关模 (方案 C)

背景（实测，见 model/out/cfo_trigger/report.md §4/§8.4）:
  `preamble_sync` 的块间自相关判据是 `Re(Σ v_m·conj(v_{m-1}))`，块 = 1 符号 = 256 采样，
  故该量带 **cos(ω·256)** 因子:
      CFO =   0 kHz → ω·256 =  0.00 rad → cos = +1.00  正常
      CFO = 100 kHz → ω·256 = 10.05 rad → cos = -0.81  反相 ⇒ 永不过正门限
      CFO = 450 kHz → ω·256 = 45.20 rad → cos = +0.36  小但为正 ⇒ 反而能检测
  这正是"检出率随 CFO 非单调"的来源。

本实验: 把判据换成复相关的**模** |Re| + |Im|（L1，免开方、免除法），看是否 CFO 免疫。

口径: `ph_thresh=0`（浮点 MF 输入下绝对门限无意义，只考察归一化判据 + 轴判别）；
      `sfd_thresh=0`（本实验只看"锁定"这一步）。
输出: model/out/preamble_mag/report.md
"""
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

OUT = ROOT / "model" / "out" / "preamble_mag"
N_FRAMES = 20
CFOS = [0.0, 10e3, 50e3, 100e3, 200e3, 450e3]
SNR = 20


def gen(snr, cfo_hz, n_frames=N_FRAMES, seed=1):
    """复用 MC 平台的损伤链（AWGN + CFO + 12bit ADC）。"""
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


def run_one(mf_i, mf_q, fs, mag_mode, pre=200, post=3200):
    """**逐帧独立**跑镜像 (锁定后需 frame_done 才退锁，多帧会串)。

    返回 (有效锁定数, 偏移列表)。偏移 = 锁定采样 − 真值帧起点，
    名义值 ≈ k_blocks×256 + 常数（本口径只用于横向对比两种判据）。
    """
    fs = np.asarray(fs)
    offs = []
    for f0 in fs:
        a = max(0, int(f0) - pre)
        b = min(len(mf_i), int(f0) + post)
        r = phy.preamble_sync_mirror(mf_i[a:b], mf_q[a:b], ph_thresh=0,
                                     sfd_thresh=0, mag_mode=mag_mode)
        if r["detect"] and r["lock_events"]:
            offs.append(int(r["lock_events"][0][0] + a - int(f0)))
    return len(offs), offs, 0


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    print(f"{'CFO':>9} | {'Re-only':^28} | {'mag (方案C)':^28}")
    print(f"{'':>9} | {'锁定数':>6} {'偏移中位':>8} {'抖动':>6}  {'ok':>3} | "
          f"{'锁定数':>6} {'偏移中位':>8} {'抖动':>6}  {'ok':>3}")
    print("-" * 78)
    for cfo in CFOS:
        v, fs = gen(SNR, cfo)
        mf = phy.matched_filter(v)
        mi = np.rint(mf.real).astype(np.int64)
        mq = np.rint(mf.imag).astype(np.int64)
        rec = {}
        for mode in (False, True):
            n, offs, ph = run_one(mi, mq, fs, mode)
            med = float(np.median(offs)) if offs else float("nan")
            jit = (max(offs) - min(offs)) if offs else 0
            rec[mode] = dict(n=n, n_good=n, med=med, jit=jit, ph=ph)
        a, b = rec[False], rec[True]
        print(f"{cfo/1e3:>7.1f}k | {a['n_good']:>6} {a['med']:>8.1f} {a['jit']:>6}  "
              f"{'Y' if a['n_good']>=N_FRAMES*0.8 else ' ' :>3} | "
              f"{b['n_good']:>6} {b['med']:>8.1f} {b['jit']:>6}  "
              f"{'Y' if b['n_good']>=N_FRAMES*0.8 else ' ' :>3}")
        rows.append((cfo, a, b))

    md = ["# 前导检测判据: 实部 vs 复相关模（方案 C）", "",
          f"SNR = +{SNR} dB（码片），{N_FRAMES} 帧/点；判据 `ph_thresh=0`（只看归一化 + 轴判别）。",
          "块间自相关 `Re(Σ v_m·conj(v_{m-1}))` 带 `cos(ω×256)` 因子，"
          "`mag_mode` 改用 `|Re| + |Im|`。", "",
          "| CFO | Re-only 有效锁定 | Re-only 偏移 | mag 有效锁定 | mag 偏移 |",
          "|---|---|---|---|---|"]
    for cfo, a, b in rows:
        md.append(f"| {cfo/1e3:.0f} kHz | {a['n_good']}/{N_FRAMES} | {a['med']:.1f} | "
                  f"{b['n_good']}/{N_FRAMES} | {b['med']:.1f} |")
    (OUT / "report.md").write_text("\n".join(md) + "\n")
    print(f"\n[report] {OUT/'report.md'}")


if __name__ == "__main__":
    main()
