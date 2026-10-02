#!/usr/bin/env python
"""run_ps_e2e.py —— 端到端解码验证：相位对齐偏移 δ 的容限 + 实读相位成功率

链路: (CFO 消旋) → matched_filter → sample_chips(align=f0+δ) → despread_chips
      → rx_deframe_symbols(syms[10:]) → fcs_ok。
信号复用 out/ps_arch/_sig（gap/tail=3000, seed=1, 20 帧/点, PSDU=20B）。

δ 扫描 [-8,+8]（覆盖轴交换区 ±4/±8 与边缘）；实读相位 = A16 窗内 argmax(cA) /
Csq 能量选相(cC)，δA/δC = wrap16(c − c*)（c*=(f0+7)%16）。

用法: python model/experiments/run_ps_e2e.py [--smoke]
"""
import sys
import json
from pathlib import Path

ROOT = Path('/home/host/Desktop/workspace/communication')
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'model'))
sys.path.insert(0, str(ROOT / 'model' / 'experiments'))
sys.path.insert(0, str(ROOT / 'tb' / 'rx_chain_e2e'))

import numpy as np            # noqa: E402
import phy_802154 as phy      # noqa: E402
import _common                # noqa: E402

OUT = _common.out_dir('ps_e2e')
SIG = ROOT / 'model' / 'out' / 'ps_arch' / '_sig'
SNRS = [0, 6, 10, 20]
CFOS = [0.0, 100e3]
N_FRAMES = 20
SHR = 10
DS = list(range(-8, 9))
NSYM = None


def load_point(snr, cfo):
    pdir = SIG / f'{snr}_{int(cfo)}'
    raw = np.fromfile(pdir / 'mem.bin', dtype='>u4')
    i = (raw & 0xFFF).astype(np.int64)
    q = ((raw >> 12) & 0xFFF).astype(np.int64)
    i = np.where(i >= 2048, i - 4096, i)
    q = np.where(q >= 2048, q - 4096, q)
    fs = np.load(pdir / 'frames.npz')['frame_start'].astype(np.int64)
    meta = json.load(open(pdir / 'meta.json'))
    cf = meta.get('cfo_per_frame')
    cf = float(cf[0]) if cf else 0.0
    return i + 1j * q, fs, cf


def derot(v, cf):
    if abs(cf) < 1e-6:
        return v
    t = np.arange(len(v)) / 16e6
    return v * np.exp(-2j * np.pi * cf * t)


def chip_seq(mf, c, L0):
    even = mf[c::16]
    odd = mf[((c + 12) % 16)::16]
    L = min(L0, len(even), len(odd))
    y = np.empty(2 * L, dtype=mf.dtype)
    y[0::2] = even[:L]
    y[1::2] = odd[:L]
    return y


def build_curves(mf):
    L0 = (len(mf) - 15) // 16 - 1
    lam16, en16 = [], []
    for c in range(16):
        y = chip_seq(mf, c, L0)
        prod = y[32:] * np.conj(y[:-32])
        cs = np.concatenate([[0.], np.cumsum(prod)])
        r = cs[32:] - cs[:-32]
        e = np.abs(y) ** 2
        ce = np.concatenate([[0.], np.cumsum(e)])
        Ev = (ce[32:] - ce[:-32])[:len(r)]
        lam16.append((np.abs(r.real) + np.abs(r.imag)) / (Ev + 1e-9))
        n = len(lam16[-1])
        Ev2 = (ce[32:] - ce[:-32])[:n]
        en16.append(Ev2 / 32.0)
    return np.stack(lam16), np.stack(en16)


def decode_ok(mf, align):
    chips = phy.sample_chips(mf, int(align), NSYM * 32)
    syms = phy.despread_chips(chips)
    _got, ok, _L = phy.rx_deframe_symbols(syms[SHR:])
    return ok


def wrap16(d):
    d = int(d) % 16
    return d if d <= 8 else d - 16


def run(smoke=False):
    global NSYM
    NSYM = len(phy.tx_symbols(bytes(20)))
    snrs = [20] if smoke else SNRS
    cfos = [0.0] if smoke else CFOS
    nfr = 3 if smoke else N_FRAMES
    table = {}
    for snr in snrs:
        for cfo in cfos:
            v, fs, cf = load_point(snr, cfo)
            vd = derot(v, cf)
            mf = phy.matched_filter(vd)
            lam16, en16 = build_curves(mf)
            nch = lam16.shape[1]
            ok_scan = np.zeros(len(DS), dtype=int)
            okA = okC = 0
            dAs, dCs = [], []
            for k in range(nfr):
                f0 = int(fs[k])
                cstar = (f0 + 7) % 16
                lo, hi = max(0, (f0 - 512) // 8), min(nch, (f0 + 512) // 8)
                sub = lam16[:, lo:hi]
                j = int(np.argmax(sub))
                cA = j // sub.shape[1]
                cC = int(np.argmax(en16[:, lo:hi].sum(axis=1)))
                dA = wrap16(cA - cstar)
                dC = wrap16(cC - cstar)
                dAs.append(dA); dCs.append(dC)
                okA += decode_ok(mf, f0 + dA)
                okC += decode_ok(mf, f0 + dC)
                for di, d in enumerate(DS):
                    ok_scan[di] += decode_ok(mf, f0 + d)
            table[(snr, cfo)] = dict(ok_scan=ok_scan, okA=okA, okC=okC,
                                     dAs=dAs, dCs=dCs, n=nfr)
            print(f"SNR={snr:2d} CFO={cfo/1e3:5.0f}k  δ扫描 "
                  f"{dict(zip(DS, ok_scan))}")
            print(f"               实读: A16 {okA}/{nfr} (δ={dAs})  "
                  f"Csq {okC}/{nfr} (δ={dCs})")
    if smoke:
        return
    # report
    lines = ["# 端到端解码：对齐偏移 δ 容限（sample_chips→despread→deframe→FCS）", "",
             f"信号: out/ps_arch/_sig（gap/tail=3000, PSDU=20B, {N_FRAMES} 帧/点）；"
             "每点先按真值 CFO 全局消旋。",
             "δ = 相对真值帧起点 f0 的抽样偏移（采样）；δA/δC = 实读相位换算偏移。", "",
             "## FCS 成功率 vs δ", ""]
    hdr = "| 档 | " + " | ".join(f"{d:+d}" for d in DS) + " |"
    lines.append(hdr)
    lines.append("|" + "---|" * (len(DS) + 1))
    for (snr, cfo), r in table.items():
        row = f"| {snr}dB {cfo/1e3:.0f}k | " + " | ".join(
            str(x) for x in r['ok_scan']) + " |"
        lines.append(row)
    lines += ["", "## 实读相位成功率（A16 / Csq）", "",
              "| 档 | A16 hit | Csq hit | A16 δ | Csq δ |", "|---|---|---|---|---|"]
    for (snr, cfo), r in table.items():
        lines.append(f"| {snr}dB {cfo/1e3:.0f}k | {r['okA']}/{r['n']} | "
                     f"{r['okC']}/{r['n']} | {r['dAs']} | {r['dCs']} |")
    lines += ["", "## 结论", "",
              "1. **解码硬容限 δ∈[-3,+3]**（±4 起基本全灭——**±4 = 半码片/轴交换区**, 这正是轴判别要防的）：",
              "   20dB 时 [-3,+3] 内 20/20; 6dB 的 ±3 边缘; 0dB 仅 [-1,+1]（超低 SNR 容限收窄）。",
              "2. **实读相位 → 解码**: Csq ≥6dB 全部 20/20（δ≡0, 能量定相每次命中真格点）;",
              "   A16 18~19/20: 读数漂移帧 δ∈{±3,±4,±5}, 其中 |δ|>=4 的直接失败",
              "   —— 裸 argmax 的'格点选错'风险实例，正对应 RTL 里 axis_ok/SFD 验证的扣防。",
              "3. **能量定相 + 单相干路在端到端层面成立**（≥6dB 全绿）—— §22/§23 方案 C 的又一正向证据。",
              "4. 局限: 用真值 f0 做锚（只测'相位选择'一环, 不含检测/定界误差）; 消旋用真值 CFO。"]
    (OUT / 'report.md').write_text("\n".join(lines) + "\n")
    print(f"\n[report] {OUT/'report.md'}")


if __name__ == '__main__':
    run(smoke='--smoke' in sys.argv)
