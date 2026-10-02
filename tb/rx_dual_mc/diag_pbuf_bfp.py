#!/usr/bin/env python
"""diag_pbuf_bfp.py —— pbuf v2（1024 环 + BFP8）专项诊断与线性对照。

流程:
  1. 同激励跑两个 bin: simv_dual_mc（BFP8 版） vs simv_pbuf_lin（旧版线性,
     DEPTH=1024/PRE=512/POST=512）——均 dump 1024 点窗（+PBUF）;
  2. dump 列（BFP8 版）: idx i q raw_mem exp（raw=8bit 尾数, exp=块指数）;
  3. 保真度: 两版窗逐样本对比（lag 扫描）→ 量化 SNR;
  4. 窗定位/CFO: 延迟 256 自相关 → 折叠后与真值（±100k）对照。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tb" / "rx_chain_e2e"))
import run_dual_mc as R          # noqa: E402
import mc_gen                    # noqa: E402

OUT = ROOT / "model" / "out" / "dual_mc" / "pbuf_bfp"
FS = 16e6


def build_lin():
    """旧版线性（未量化）preamble_buf, 同窗参数 → 对照 bin。"""
    simv = HERE / "sim_build" / "simv_pbuf_lin"
    if simv.exists():
        return simv
    # BFP8 提交的父提交 = 最后的"线性 4096 版"（HEAD 现已是 BFP8 版）
    old = subprocess.run(["git", "show", "5a500f0^:rtl/rx/frontend/preamble_buf.sv"],
                         cwd=ROOT, capture_output=True, text=True).stdout
    assert "preamble_buf" in old
    old = old.replace("parameter integer DEPTH = 4096", "parameter integer DEPTH = 1024")
    old = old.replace("parameter integer PRE   = 1024", "parameter integer PRE   = 512")
    old = old.replace("parameter integer POST  = 2048", "parameter integer POST  = 512")
    # 语义对齐: 旧版触发后多写 1 个样本（513 个）会覆盖窗首地址（off-by-one）;
    # 新版恰停在 窗尾-1（512 个）, 窗首不被覆盖——对照 bin 同步修正。
    old = old.replace("if (cnt == POST[11:0]) done_r <= 1'b1;",
                      "if (cnt == POST[11:0] - 1'b1) done_r <= 1'b1;")
    lin_src = Path("/tmp/preamble_buf_lin.sv")
    lin_src.write_text(old)
    srcs = [str(lin_src) if s == "rtl/rx/frontend/preamble_buf.sv" else str(ROOT / s)
            for s in R.SOURCES]
    csrc = HERE / "sim_build" / "csrc_pbuf_lin"
    csrc.mkdir(parents=True, exist_ok=True)
    cmd = [f"{R.VCS_ENV['VCS_HOME']}/bin/vcs", "-full64", "-sverilog",
           "-timescale=1ns/1ps", "-o", str(simv),
           "+incdir+" + str(ROOT / "rtl" / "rx")] + srcs
    r = subprocess.run(cmd, cwd=csrc, env=R.VCS_ENV, capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stdout[-3000:])
        raise SystemExit("lin build FAILED")
    print("[pbuf] lin build ok")
    return simv


def gen_and_run(simv, out_dir, tag, frames=4, snr=20, psdu_len=20, seed=11):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = mc_gen.gen_point(snr, frames, psdu_len, 8.0, seed, 400, 600, out_dir,
                            gap_jitter=16, cfo_list=[100e3, -100e3])
    cmd = [str(simv),
           f"+MEM={out_dir/'mem.bin'}", f"+NSMP={meta['n_smp']}",
           f"+OUT={out_dir/f'events_{tag}.txt'}", f"+CKS={meta['mem_cks']}",
           f"+INCA={R.inc_from_cfo(100e3) & 0xFFFFFF}",
           f"+INCB={R.inc_from_cfo(-100e3) & 0xFFFFFF}",
           f"+PBUF={out_dir/f'pbuf_{tag}.txt'}"]
    r = subprocess.run(cmd, cwd=out_dir, env=R.VCS_ENV, capture_output=True, text=True)
    dump = out_dir / f"pbuf_{tag}.txt"
    n = len(dump.read_text().splitlines()) if dump.exists() else 0
    print(f"[pbuf:{tag}] rc={r.returncode} dump_lines={n}")
    for line in r.stdout.splitlines():
        if ("pbuf-dbg" in line) or ("p368" in line):
            print("   ", line.strip())
    if n == 0:
        sys.stderr.write(r.stdout[-3000:])
        raise SystemExit(f"dump FAILED ({tag})")
    return meta


def load_dump(p):
    rows = [l.split() for l in Path(p).read_text().splitlines() if l.strip()]
    return np.array(rows, dtype=np.int64)   # [idx, i, q, (raw_mem, exp)]


def cfo_scan(i, q, D=256, L=256, step=4):
    """窗内滑窗延迟自相关。D=256 = 前导周期（±31.25k 无模糊, 高值折叠）。"""
    x = i + 1j * q
    n = len(x)
    best = (-1, 0.0, 0j)
    for s in range(D, n - L + 1, step):
        r = np.sum(x[s:s + L] * np.conj(x[s - D:s - D + L]))
        if abs(r) > best[1]:
            best = (s, abs(r), r)
    s, mag, r = best
    return np.angle(r) * FS / (2 * np.pi * D), s, mag


def aligned_snr(fi, fq, li, lq, lags=range(-6, 7)):
    out = []
    n = len(li)
    for lag in lags:
        a0, a1 = max(0, -lag), min(n, n - lag)
        if a1 - a0 < 512:
            continue
        f_i, f_q = fi[a0:a1], fq[a0:a1]
        l_i, l_q = li[a0 + lag:a1 + lag], lq[a0 + lag:a1 + lag]
        sp = np.sum(l_i ** 2 + l_q ** 2)
        ep = np.sum((f_i - l_i) ** 2 + (f_q - l_q) ** 2)
        out.append((lag, 10 * np.log10(sp / max(ep, 1e-9))))
    return out


def main():
    bin_bfp = R.build()
    bin_lin = build_lin()
    gen_and_run(bin_bfp, OUT, "bfp")
    gen_and_run(bin_lin, OUT, "lin")

    da = load_dump(OUT / "pbuf_bfp.txt")
    fi, fq = da[:, 1].astype(float), da[:, 2].astype(float)
    if da.shape[1] >= 5:
        raw, ex = da[:, 3], da[:, 4]
        print(f"[raw] mem 尾数唯一值数 = {len(np.unique(raw))}, 值域 [{raw.min()},{raw.max()}]")
        print(f"[raw] exp 唯一值 = {np.unique(ex).tolist()} （应 ~13 级别）")
        print(f"[raw] raw 前 16 = {raw[:16].tolist()}")
        print(f"[raw] exp 前 16 = {ex[:16].tolist()}")
        sh = ex - 7
        man = np.where(sh >= 0, raw * (1 << np.maximum(sh, 0)),
                       raw >> np.maximum(-sh, 1))
        mism = int(np.sum(man != fi.astype(np.int64)))
        print(f"[raw] Python 复原 vs 硬件 rd_i 失配 = {mism}/1024")
    dl = load_dump(OUT / "pbuf_lin.txt")
    li, lq = dl[:, 1].astype(float), dl[:, 2].astype(float)
    n = min(len(fi), len(li))
    fi, fq, li, lq = fi[:n], fq[:n], li[:n], lq[:n]

    print(f"[bfp]  unique 值（i 轴）= {len(np.unique(fi))}；[lin] = {len(np.unique(li))}")
    print(f"[bfp]  unique 前 24: {np.unique(fi)[:24].astype(int).tolist()}")
    pairs = aligned_snr(fi, fq, li, lq)
    print("[保真] lag 扫描: " + "  ".join(f"{lag:+d}:{v:.1f}dB" for lag, v in pairs))
    best_lag, best_snr = max(pairs, key=lambda t: t[1])
    print(f"[保真] 最佳 lag = {best_lag:+d}, 量化 SNR = {best_snr:.1f} dB")

    e = fi ** 2 + fq ** 2
    seg = [f"{int(np.sqrt(np.mean(e[k:k+128]))):>6.0f}" for k in range(0, n, 128)]
    print("[能量] 窗内 8 段 rms:", " ".join(seg))

    # ---- 误差形态 + 理想编码对照 ----
    d = fi - li
    dq = fq - lq
    bad = np.where((np.abs(d) > 128) | (np.abs(dq) > 128))[0]
    print(f"[err] |diff|>128 样本数 = {len(bad)}（i 轴 {int((np.abs(d)>128).sum())}, "
          f"q 轴 {int((np.abs(dq)>128).sum())}）")
    print("[err] 索引:", bad.tolist())
    if da.shape[1] >= 5:
        raw, ex = da[:, 3], da[:, 4]
        print("[head] 窗首 16 四元组 lin / bfp / raw / exp / (lin+16)>>5 / (lin+32)>>6:")
        for k0 in range(16):
            r5 = (int(li[k0]) + 16) >> 5
            r6 = (int(li[k0]) + 32) >> 6
            print(f"   {k0:2d}: {int(li[k0]):7d} {int(fi[k0]):7d} {int(raw[k0]):5d} {int(ex[k0]):2d}"
                  f"   {r5:5d}   {r6:5d}")
    print("[err] 索引 mod 64:", sorted(set((bad % 64).tolist())))
    hist = np.histogram(d, bins=[-4096, -512, -256, -128, -64, 0, 64, 128, 256, 512, 4096])[0]
    print("[err] diff 直方 (-4096..4096):", hist.tolist())
    print("[err] 连续 24 样本 lin / bfp / diff:")
    for k0 in range(404, 428):
        print(f"   {k0:4d}: {li[k0]:8.0f} {fi[k0]:8.0f} {d[k0]:8.0f}")
    B = 13
    sh = B - 7
    idq = np.right_shift(li.astype(np.int64) + (1 << (sh - 1)), sh)
    ideali = idq << sh
    alg = 10 * np.log10(np.sum(li ** 2) / max(np.sum((ideali - li) ** 2), 1e-9))
    mism = int(np.sum(idq != da[:, 3])) if da.shape[1] >= 5 else -1
    print(f"[理想] B=13 固定: 量化 SNR = {alg:.1f} dB; 硬件 raw vs 理想 tail 失配 = "
          f"{mism}/1024")
    # 逐块 max（窗内）——看块内真实 max 的分布
    bm = [int(np.max(np.abs(li[k:k+64]))) for k in range(64, n - 64, 64)]
    print("[块max] 窗内块 max 序列:", bm[:12], "...")

    for tag, (i, q) in (("bfp", (fi, fq)), ("lin", (li, lq))):
        cfo, s, mag = cfo_scan(i, q)
        print(f"[{tag}] CFO(D=256) = {cfo/1e3:+8.2f} kHz （峰位 s={s}, |r|={mag:.3g}）")
    print("[真值] 帧 CFO 交替 +100k/-100k（首帧 +100k; D=256 无模糊 ±31.25k → 折叠 -25k）")


if __name__ == "__main__":
    main()
