# -*- coding: utf-8 -*-
"""draw_dual_eye.py —— 双通道消旋的眼图矩阵（矫正前 × 通道A/B，两台设备）

排布（2 行 × 3 列）：
   行 = 两台设备（设备1 CFO=+100 kHz / 设备2 CFO=-100 kHz，交替发帧）
   列 = 矫正前（MF 原始） / 通道 A 消旋后 / 通道 B 消旋后
阅读方式：对角线亮 = 每个通道只对那些"参数与自己匹配"的设备开眼。

数据：/tmp/eye_mf.txt（RTL 实测的**采样级 MF 输出**，tb/rx_dual_mc 的 +EYEDUMP）。
消旋在 Python 侧做（与 RTL 的码片级消旋数学等价：复乘与抽取可交换），
相位零点按各自帧起点重取（常数相位差无关，眼图只看相对旋转）。
"""
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.rcParams["font.sans-serif"] = ["Noto Sans CJK SC", "Noto Sans CJK TC",
                                  "Noto Serif CJK SC", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

ROOT = Path("/home/host/Desktop/workspace/communication")
sys.path.insert(0, str(ROOT / "model"))
import phy_802154 as phy          # noqa: E402

FS = 16e6
PERIOD = 16                        # 偶数码片间隔 = 2*SPS
N_TR = 400
CHIP_RATE = 2e6

D = Path(sys.argv[1] if len(sys.argv) > 1 else
         str(ROOT / "model/out/dual_mc/eye2"))
raw = np.loadtxt("/tmp/eye_mf.txt", dtype=np.int64)
k = raw[:, 0]
mf = raw[:, 1] + 1j * raw[:, 2]
gt = np.load(D / "frames.npz")
fs = gt["frame_start"]
psdu = gt["psdu"]
# 理想参考波形（同一批 mc_gen 参数：scale=8，无 CFO/无噪）
REF = []
_h = phy.fixed_half_sine()
for f in range(len(fs)):
    syms = phy.tx_symbols(bytes(psdu[f]))
    i_t, q_t = phy.modulate_oqpsk_fixed(syms.tolist())
    _nr = min(len(i_t), len(q_t))
    ri = np.convolve(np.array(i_t[:_nr], float) * 8, _h)[:_nr]
    rq = np.convolve(np.array(q_t[:_nr], float) * 8, _h)[:_nr]
    REF.append(ri + 1j * rq)
cfos = [100e3, -100e3]
names = ["设备1  +100 kHz", "设备2  -100 kHz"]

out = ROOT / "model/out/dual_mc"
out.mkdir(parents=True, exist_ok=True)
fig, axes = plt.subplots(2, 3, figsize=(14.5, 7.2), sharex=True, sharey=True)

for row in range(2):
    lo = int(fs[row]) + 1200                     # 从前导中段起折叠
    hi = int(fs[row + 1]) if row + 1 < len(fs) else len(mf)
    sel = (k >= lo) & (k < hi)
    kk = k[sel]
    seg = mf[sel]
    for col, (cfo, tag) in enumerate([
            (None, "矫正前（MF 原始, 未消旋）"),
            (cfos[0], "通道 A 消旋后（参数 +100 kHz）"),
            (cfos[1], "通道 B 消旋后（参数 −100 kHz）")]):
        ax = axes[row][col]
        if cfo is None:
            w = seg
            metric = "眼开度 n/a"
        else:
            # 帧内局部旋转（相位零点任意 → 只看频率是否匹配）
            t = kk - kk[0]
            w = seg * np.exp(-1j * 2 * np.pi * cfo * t / FS)
            # 定量: 与本帧理想波形的归一化匹配度（按帧内偏移切片对齐）
            off0 = int(kk[0] - fs[row])
            rb = 0.0
            for d in range(0, 24):
                rs_ = REF[row][off0 + d:off0 + d + len(w)]
                mm = min(len(w), len(rs_))
                if mm < 4000:
                    continue
                rb = max(rb, abs(np.vdot(rs_[:mm], w[:mm]))
                         / max(np.linalg.norm(w[:mm]) * np.linalg.norm(rs_[:mm]), 1e-9))
            metric = f"理想匹配度 {rb:.2f}"
        start = 0
        segs = []
        for s in range(N_TR):
            a = start + s * PERIOD
            if a + PERIOD > len(w):
                break
            segs.append(w[a:a + PERIOD].real)
        segs = np.array(segs)
        xs = np.arange(PERIOD) / PERIOD
        for tr in segs:
            ax.plot(xs, tr, color="tab:blue", alpha=0.06, lw=0.6)
        ax.plot(xs, segs.mean(axis=0), color="tab:red", lw=1.6)
        ax.grid(alpha=0.25)
        ax.set_title(f"{names[row]}\n{tag}\n{metric}", fontsize=9)
        if col == 0:
            ax.set_ylabel("I (MF 输出)")
        if row == 1:
            ax.set_xlabel("码片周期内相位 (16 采样折叠)")

fig.suptitle("双通道消旋眼图矩阵 —— 每通道只对参数匹配的设备开眼"
             "（RTL 采样级 MF 输出 + 理想消旋；每格 %d 条迹线）" % N_TR, fontsize=11)
fig.tight_layout(rect=(0, 0, 1, 0.95))
png = out / "eye_dual.png"
fig.savefig(png, dpi=130)
print(f"-> {png}")

# —— 定量汇总 ——
print("\n与理想波形的匹配度 r/上限（1.00 = 完全对齐；无消旋时会明显掉）:")
hdr = "".join(f"{c:>22}" for c in ["矫正前", "通道A消旋", "通道B消旋"])
print(f"{'':>16}{hdr}")
for row in range(2):
    lo = int(fs[row]) + 1200
    hi = int(fs[row + 1]) if row + 1 < len(fs) else len(mf)
    sel = (k >= lo) & (k < hi)
    kk, seg = k[sel], mf[sel]
    vals = []
    for cfo in [None, cfos[0], cfos[1]]:
        w = seg if cfo is None else seg * np.exp(-1j * 2 * np.pi * cfo * (kk - kk[0]) / FS)
        off0 = int(kk[0] - fs[row])
        best_r = 0.0
        for d in range(0, 24):            # 扫描 MF 群延迟（0..24 采样）
            ref_seg = REF[row][off0 + d:off0 + d + len(w)]
            m = min(len(w), len(ref_seg))
            if m < 4000:
                continue
            rr = (abs(np.vdot(ref_seg[:m], w[:m]))
                  / max(np.linalg.norm(w[:m]) * np.linalg.norm(ref_seg[:m]), 1e-9))
            best_r = max(best_r, rr)
        vals.append(best_r)
    print(f"{names[row]:>16}" + "".join(f"{v:>22.3f}" for v in vals))
