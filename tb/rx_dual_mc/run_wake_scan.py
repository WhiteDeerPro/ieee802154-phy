#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""run_wake_scan.py —— PMU 唤醒延迟扫描（docs/22 §6 "唤醒策略锁定余量"实验）。

问题：LISTEN 期 sync 冻结（时钟/数据门控）；pd_rise 唤醒后 sync 实际重启延迟 L 拍。
      现状时序：restart@帧起点+460（pd_rise）→ 重扫锁定@+1484（余前导 1588 采样）。
      L 多大时锁定仍来得及？—— 即唤醒路径的延迟预算。

实现：RTL 端口方案（PMU-lite v2 ①③, notes §43）：tb +WDLY=<L> 驱动 dut.wake_dly
      （rx_frontend 内 wk 门控 → preamble_sync.en；pd_rise 后关 L+1 拍）。
      en=0 冻结处理、smp_cnt 照走（"相位维护"例外）；L=0 现状等价（60/60 复现）。
      [弃用] 首版 tb force u_sync.dv_in 方案：VCS 折叠共享 dv 网，连带冻结 deint，无效。
      WONCE 单次缺口实验：手动 +WDLY=<L> +WONCE=1 → ev_snr20_wonce<L>.txt（§43）。
      冷启动验证（wake_clr 复位）：+WDLY=<L> +WONCE=1 +WCLR=1 → ev_snr20_wonce<L>_wclr.txt
      （§43 “冷启动验证”节：小缺口 3/5/9 拍 = 试探帧弃 + 正式帧 58/58 达成）。

扫描：L × {snr20, snr6} → FRMA(FCS=1) 成功帧数（口径同边界回归）。
      默认点含小 L 段（0,2,4,8,16,32）——L=16（关17拍）为孤立无损窗口，关键数据点。
用法：python tb/rx_dual_mc/run_wake_scan.py [--build] [L1 L2 ...]
产出：model/out/dual_mc/wake_scan/（report.md + ev_*.txt）
"""
import sys
import json
import subprocess
import time
import argparse
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tb' / 'rx_dual_mc'))
import run_dual_mc as R  # noqa: E402

SIM = R.SIM_DIR
DMC = ROOT / 'model/out/dual_mc'
OUTD = DMC / 'wake_scan'
SIMV = 'simv_wake'
OUTD.mkdir(parents=True, exist_ok=True)

inc_a = R.inc_from_cfo(100e3) & 0xFFFFFF
inc_b = R.inc_from_cfo(-100e3) & 0xFFFFFF


def build(force=False):
    out = SIM / SIMV
    if out.exists() and not force:
        return
    cmd = [f"{R.VCS_ENV['VCS_HOME']}/bin/vcs", "-full64", "-sverilog",
           "-timescale=1ns/1ps", "-o", str(out),
           "+incdir+" + str(ROOT / 'rtl' / 'rx'),
           "+define+WTRACE_EN",   # 启用 sync 内部探针（仅 Csq 兼容）
           "-pvalue+dual_mc_tb.RSTEN=1"] + [str(ROOT / s) for s in R.SOURCES]
    csrc = SIM / f"csrc_{SIMV}"
    csrc.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    r = subprocess.run(cmd, cwd=csrc, env=R.VCS_ENV, capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stdout[-4000:] + "\n")
        raise SystemExit("[wake] build FAILED")
    print(f"[wake] build ok ({time.time()-t0:.1f}s) -> {out}")


def run_point(stream, wdly):
    """跑 (stream, WDLY) → dict(ok, total, dets, secs)。"""
    d = DMC / stream
    m = json.load(open(d / 'meta.json'))
    ev = OUTD / f'ev_{stream}_w{wdly}.txt'
    if ev.exists():
        ev.unlink()
    cmd = [str(SIM / SIMV), f'+MEM={d}/mem.bin', f"+NSMP={m['n_smp']}",
           f'+OUT={ev}', f"+CKS={m['mem_cks']}",
           f'+INCA={inc_a}', f'+INCB={inc_b}', '+PH=0', '+NORMT=90',
           f'+WDLY={wdly}']
    t0 = time.time()
    r = subprocess.run(cmd, cwd=d, env=R.VCS_ENV, capture_output=True, text=True)
    if r.returncode != 0 or not ev.exists():
        sys.stderr.write(r.stdout[-1500:])
        raise SystemExit(f"[wake] sim FAILED ({stream} L={wdly})")
    fs = np.load(d / 'frames.npz')['frame_start'].astype(np.int64)
    okA, dets = set(), []
    for line in open(ev):
        p = line.split()
        if not p:
            continue
        if p[0] == 'FRMA' and len(p) >= 4 and int(p[3], 0) == 1:
            k = int(p[1])
            f = int(np.searchsorted(fs, k, 'right')) - 1
            if 0 <= f < len(fs):
                okA.add(f)
        elif p[0] == 'DET':
            dets.append(int(p[1]))
    return dict(stream=stream, wdly=wdly, ok=len(okA), total=len(fs),
                dets=dets, secs=time.time() - t0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--build', action='store_true')
    ap.add_argument('--force', action='store_true')
    ap.add_argument('Ls', nargs='*', type=int)
    a = ap.parse_args()
    if a.build or a.force or not (SIM / SIMV).exists():
        build(force=a.force)
    Ls = a.Ls or [0, 2, 4, 8, 16, 32, 64, 128, 192, 256, 320, 384, 448, 512, 576, 640, 768]
    streams = ['snr20', 'snr6']
    rows = []
    for st in streams:
        for L in Ls:
            res = run_point(st, L)
            rows.append(res)
            print(f"[wake] {st:6s} L={L:4d}  FRMA={res['ok']:3d}/{res['total']}  "
                  f"({res['secs']:.1f}s)", flush=True)
    # —— 报告 ——
    lines = ["# PMU 唤醒延迟扫描（+WDLY）", "",
             "问题: LISTEN 期 sync 冻结, pd_rise 唤醒延迟 L 拍; 锁定是否来得及？",
             "口径: FRMA(FCS=1) 成功帧数（snr20 基线 60/60; snr6 基线 15/60）。", ""]
    for st in streams:
        sub = [r for r in rows if r['stream'] == st]
        base = next(r['ok'] for r in sub if r['wdly'] == 0)
        lines.append(f"## {st}（基线 L=0: {base}/{sub[0]['total']}）")
        lines.append("")
        lines.append("| L | FRMA | Δ vs L=0 |")
        lines.append("|---|---|---|")
        for r in sub:
            d = r['ok'] - base
            lines.append(f"| {r['wdly']} | {r['ok']}/{r['total']} | {d:+d} |")
        lines.append("")
    rep = OUTD / 'report.md'
    rep.write_text('\n'.join(lines), encoding='utf-8')
    print(f"[wake] report -> {rep}")


if __name__ == '__main__':
    main()
