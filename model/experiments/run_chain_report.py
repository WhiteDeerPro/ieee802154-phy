#!/usr/bin/env python
"""run_chain_report.py —— 链路综合报告：多设备 × 多 SNR × 通道替换（分析/出图侧）

输入（需先跑数据，生成与仿真命令见 model/out/dual_mc/chain_report/report.md）:
  model/out/dual_mc/chain_report/      主流: 200 帧, 12 帧模板(4 设备 × 3 SNR) 循环,
                                       第 100 帧 SWAPK/SWAPB 替换 B 通道 -100k -> +125k
                                       （events.txt / rot.txt / frames.npz）
  model/out/dual_mc/chain_report_eye/  眼图流: dev1 三连帧 SNR 20/12/6 dB（eye_mf.txt）

输出（同目录）:
  per_table.txt / stats.json / despread_levels.png / eye_3snr.png

用法: python model/experiments/run_chain_report.py
"""
import numpy as np, json, collections, sys
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'model'))
import visualize as V                                  # noqa: E402
V.use_cjk_font()

rep  = ROOT / 'model/out/dual_mc/chain_report'
eyed = ROOT / 'model/out/dual_mc/chain_report_eye'
gt = np.load(rep / 'frames.npz')
fs = gt['frame_start'].astype(int); psdu = gt['psdu']; plens = gt['psdu_lens']
nfr = len(fs)
TIERS = [20.0, 12.0, 6.0]

def frame_of(k):
    f = int(np.searchsorted(fs, k, 'right')) - 1
    return f if 0 <= f < nfr else None

okA = {}; okB = {}; byA = collections.defaultdict(list); byB = collections.defaultdict(list)
for l in open(rep / 'events.txt'):
    p = l.split()
    if not p: continue
    t = p[0]; k = int(p[1]) if len(p) > 1 else -1
    f = frame_of(k)
    if f is None: continue
    if t == 'FRMA' and int(p[3]) == 1: okA[f] = True
    elif t == 'FRMB' and int(p[3]) == 1: okB[f] = True
    elif t == 'BYTA': byA[f].append(int(p[2]))
    elif t == 'BYTB': byB[f].append(int(p[2]))

ok = {f: (f in okA or f in okB) for f in range(nfr)}
ch = {f: ('A' if f in okA else ('B' if f in okB else '-')) for f in range(nfr)}

# ---- PER 表 ----
lines = []
def cell(dev, tier, a, b):
    idx = [f for f in range(a, b) if (f % 12) % 4 == dev and (f % 12) // 4 == tier]
    return sum(1 for f in idx if ok[f]), len(idx)

lines.append(f'总成功 = {sum(ok.values())}/{nfr}')
lines.append('PER（成功/总数）替换点=第100帧（B: -100k -> +125k）')
lines.append('dev     20dB(pre->post)   12dB(pre->post)   6dB(pre->post)')
rows = {}
for dev in range(4):
    line = f'dev{dev+1} '
    for tier in range(3):
        pre = cell(dev, tier, 0, 100); post = cell(dev, tier, 100, 200)
        rows[f'dev{dev+1}_{TIERS[tier]:g}dB_pre'] = pre
        rows[f'dev{dev+1}_{TIERS[tier]:g}dB_post'] = post
        line += f'  {pre[0]:>2}/{pre[1]:<2} -> {post[0]:>2}/{post[1]:<2} |'
    lines.append(line)

# ---- 码流对比 ----
bit_ok = bit_bad = frames_exact = frames_bad = 0
for f in range(nfr):
    if not ok[f]: continue
    bl = byA[f] if ch[f] == 'A' else byB[f]
    L = int(plens[f]); got = bytes(bl[:L]); exp = bytes(psdu[f][:L])
    if got == exp and len(bl) >= L: frames_exact += 1
    else: frames_bad += 1
    bit_ok += sum(1 for a, b in zip(got, exp) if a == b)
    bit_bad += sum(1 for a, b in zip(got, exp) if a != b)
lines.append(f'码流: 成功帧逐字节全对 {frames_exact}, 有差异 {frames_bad}; '
             f'字节正确 {bit_ok}/{bit_ok+bit_bad}')
lines.append(f'成功通道分布: {dict(collections.Counter(ch[f] for f in range(nfr) if ok[f]))}')

# ---- 解扩相干和电平 + 半径统计 ----
ks = []; vs = []
for l in open(rep / 'rot.txt'):
    p = l.split()
    if p and p[0] == 'A':
        ks.append(int(p[1])); vs.append(complex(int(p[2]), int(p[3])))
d = dict(zip(ks, vs))
pts = {t: [] for t in range(3)}
for f in range(nfr):
    dev = (f % 12) % 4; tier = (f % 12) // 4
    if dev == 1: continue
    lo = int(fs[f]) + 3000
    nxt = int(fs[f + 1]) - 200 if f + 1 < nfr else lo + 16000
    hi = min(lo + 16000, nxt)
    seq = [d[k] for k in range(lo, hi) if k in d]
    if len(seq) < 32 * 30: continue
    best = None
    for off in range(32):
        n = (len(seq) - off) // 32
        arr = np.array(seq[off:off + 32 * n]).reshape(n, 32).sum(1)
        s = np.mean(np.abs(arr)) if n else 0
        if best is None or s > best[0]: best = (s, off)
    off = best[1]; n = (len(seq) - off) // 32
    pts[tier].extend(np.array(seq[off:off + 32 * n]).reshape(n, 32).sum(1).tolist())

lines.append('解扩相干和 |Σ32片| 统计（A 通道）:')
for t in range(3):
    a = np.array(pts[t])
    if len(a):
        m = np.abs(a) / 1e5
        lines.append(f'  SNR {TIERS[t]:g} dB: n={len(a)} 中位|和|={np.median(m):.3f}e5 '
                     f'IQR=({np.percentile(m,25):.3f},{np.percentile(m,75):.3f})e5')

fig, axes = plt.subplots(1, 3, figsize=(13, 4.4))
for t, ax in enumerate(axes):
    a = np.array(pts[t])
    if len(a): ax.scatter(a.real/1e5, a.imag/1e5, s=6, alpha=0.5)
    ax.set_title(f'解扩相干和 {TIERS[t]:g} dB (n={len(a)})')
    ax.set_xlabel('I (×1e5)'); ax.set_ylabel('Q (×1e5)')
    ax.grid(alpha=0.3); ax.set_aspect('equal', adjustable='datalim')
fig.suptitle('解扩相干和散布（每点 = 32 片复和）')
fig.tight_layout(); fig.savefig(rep / 'despread_levels.png', dpi=110)

# ---- 眼图 ----
ek = []; ei = []
for l in open(eyed / 'eye_mf.txt'):
    p = l.split(); ek.append(int(p[0])); ei.append(int(p[1]))
ek = np.array(ek); ei = np.array(ei)
fs_e = np.load(eyed / 'frames.npz')['frame_start'].astype(int)
fig, axes = plt.subplots(1, 3, figsize=(13, 4.4))
lines.append('眼图（I 分量, 每码片 8 点, 800 码片）:')
for t, ax in enumerate(axes):
    lo = int(fs_e[t]) + 3000
    sel = (ek >= lo) & (ek < lo + 8 * 800)
    seg = ei[sel][: 8 * 800]
    if len(seg) >= 8 * 800:
        m = seg.reshape(-1, 8)
        for tr in m: ax.plot(range(8), tr, color='C0', alpha=0.06, lw=0.6)
        eye_h = float(np.percentile(m[:, 3], 90) - np.percentile(m[:, 3], 10))
        lines.append(f'  SNR {TIERS[t]:g} dB: 中央采样位(p3) P90-P10 眼高 = {eye_h:.0f}')
    ax.set_title(f'MF 眼图 (I) — {TIERS[t]:g} dB')
    ax.set_xlabel('采样/码片'); ax.grid(alpha=0.3)
fig.suptitle('匹配滤波输出眼图（dev1 数据段）')
fig.tight_layout(); fig.savefig(rep / 'eye_3snr.png', dpi=110)

text = '\n'.join(lines)
(rep / 'per_table.txt').write_text(text + '\n')
json.dump({'ok': sum(ok.values()), 'nfr': nfr, 'rows': rows,
           'frames_exact': frames_exact, 'frames_bad': frames_bad,
           'bytes_ok': bit_ok, 'bytes_bad': bit_bad},
          open(rep / 'stats.json', 'w'), indent=1)
print(text)
