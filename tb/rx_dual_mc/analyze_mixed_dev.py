#!/usr/bin/env python
"""analyze_mixed_dev.py —— 混合设备实验的深度分析（符号层）

输入: model/out/dual_mc/<tag>/{rot.txt, events.txt, frames.npz, meta.json}
方法:
  1. 对每个定界帧（FSTA/B），按 RTL 同口径取"FST 后第一片"起的 32 片窗口，
     与 16 个 PN 码字复相关（与 despreader 同构）→ 判决符号 + 复软值;
  2. 与真值 tx_syms 比对 → 符号正确率（验证对齐与解扩质量）;
  3. 对每帧的复软值序列去帧初相后做线性拟合 → **残余频偏直测**
     （斜率 rad/符号 → Hz; 符号时长 32 码片 = 16 µs）;
  4. 导出星座数据（每帧软值 + 判决 + 真值）到 npz, 供绘图。

输出: <tag>/analysis.json + <tag>/const.npz
用法: python tb/rx_dual_mc/analyze_mixed_dev.py --tag mixdev1
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "model"))
import phy_802154 as phy  # noqa: E402

CHIP = phy.CHIP
SYM_LEN = 32
SYM_TIME = SYM_LEN * 0.5e-6      # 16 µs（2 Mchip/s）


def load_rot(path):
    rec = {"A": [], "B": []}
    for l in open(path):
        p = l.split()
        if len(p) == 4:
            rec[p[0]].append((int(p[1]), int(p[2]), int(p[3])))
    out = {}
    for ch in ("A", "B"):
        a = np.asarray(rec[ch], dtype=np.int64)
        out[ch] = (a[:, 0], a[:, 1].astype(float) + 1j * a[:, 2].astype(float))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="mixdev1")
    ap.add_argument("--out-root", default=str(ROOT / "model/out/dual_mc"))
    a = ap.parse_args()
    d = Path(a.out_root) / a.tag

    gt = np.load(d / "frames.npz")
    fs = gt["frame_start"].astype(np.int64)
    tx = gt["tx_syms"]; tl = gt["tx_lens"]
    meta = json.load(open(d / "meta.json"))
    rot = load_rot(d / "rot.txt")

    # FST → 帧归属
    fst = {"A": [], "B": []}
    for l in open(d / "events.txt"):
        p = l.split()
        if p and p[0] in ("FSTA", "FSTB"):
            fst[p[0][3]].append(int(p[1]))

    N = len(fs)
    per_frame = []
    const_frames = []       # 星座: (帧号, dev, 通道, 软值(复), 判决, 真值)

    for ch in ("A", "B"):
        ks, vs = rot[ch]
        for k_fst in fst[ch]:
            f = int(np.searchsorted(fs, k_fst, "right")) - 1
            if not (0 <= f < N):
                continue
            K = int(tl[f])
            i0 = int(np.searchsorted(ks, k_fst, "left"))
            seg = vs[i0:i0 + K * SYM_LEN]
            if len(seg) < K * SYM_LEN:
                continue
            z = seg.reshape(K, SYM_LEN)
            s = z @ CHIP.T                       # (K,16)
            mag = np.abs(s)
            best = np.argmax(mag, axis=1)
            soft = s[np.arange(K), best]
            true = tx[f, :K]
            n_ok = int((best == true).sum())
            # 残余频偏直测: 相位展开 → 去常数（减首值）→ 线性拟合斜率
            ph0 = np.unwrap(np.angle(soft))
            ph0 = ph0 - ph0[0]
            slope = np.polyfit(np.arange(K), ph0, 1)[0] if K > 2 else 0.0
            f_est = slope / (2 * np.pi * SYM_TIME)
            per_frame.append(dict(f=f, ch=ch, dev=int(f % 4), K=K,
                                  sym_ok=n_ok, sym_rate=n_ok / K,
                                  f_est_hz=float(f_est)))
            const_frames.append((f, ch, soft, best, true))

    # 汇总
    devs = {}
    for m, name in enumerate(["dev1", "dev2", "dev3", "dev4"]):
        rows = [r for r in per_frame if r["dev"] == m]
        if not rows:
            continue
        devs[name] = dict(
            frames=len(rows),
            sym_rate=float(np.mean([r["sym_rate"] for r in rows])),
            f_est_med=float(np.median([r["f_est_hz"] for r in rows])),
        )
    res = dict(per_frame=per_frame, devs=devs)
    (d / "analysis.json").write_text(json.dumps(res, indent=2))

    # 星座数据导出
    if const_frames:
        maxK = max(len(c[2]) for c in const_frames)
        soft = np.zeros((len(const_frames), maxK), complex)
        pred = np.full((len(const_frames), maxK), -1, np.int16)
        true = np.full((len(const_frames), maxK), -1, np.int16)
        fidx = np.zeros(len(const_frames), np.int64)
        chmap = np.zeros(len(const_frames), np.int8)
        for i, (f, ch, s, b, t) in enumerate(const_frames):
            soft[i, :len(s)] = s; pred[i, :len(s)] = b; true[i, :len(s)] = t
            fidx[i] = f; chmap[i] = 0 if ch == "A" else 1
        np.savez_compressed(d / "const.npz", soft=soft, pred=pred, true=true,
                            frame=fidx, ch=chmap)

    print(f"[analyze] 定界帧 {len(per_frame)} 个")
    for name, v in devs.items():
        print(f"  {name}: 帧={v['frames']:3d} 符号正确率={v['sym_rate']*100:5.1f}% "
              f"残余频偏中位={v['f_est_med']/1e3:+7.1f} kHz")
    print(f"[analyze] -> {d}/analysis.json, const.npz")


if __name__ == "__main__":
    main()
