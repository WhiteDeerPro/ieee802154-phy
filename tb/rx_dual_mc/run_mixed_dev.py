#!/usr/bin/env python
"""run_mixed_dev.py —— 混合设备类型的通信过程模拟（RTL, rx_dual）

设备类型（帧号 % 4 轮转）:
  dev1: +100 kHz,  20 B, 20 dB   （主群, 通道 A 覆盖）
  dev2: -100 kHz,  20 B, 20 dB   （主群, 通道 B 覆盖）
  dev3: +100 kHz,  64 B, 14 dB   （重负载: 长帧 + 低功率, 通道 A）
  dev4: +100→+125 kHz 线性漂移, 20 B, 20 dB  （漂移设备: 逐步被通道边缘化）

接收: rx_dual 双通道（A=+100k / B=-100k 固定）+ **真相位注入**
（offset=10, 上轮 dev2 标定的最优; 见 docs/16 §7.5 —— 相位覆盖变量已单独
验证, 此处排除, 聚焦"设备类型 × 覆盖"）。

产物 (model/out/dual_mc/<tag>/):
  mem.bin / frames.npz / meta.json / events.txt   激励与事件
  rot.txt        码片级消旋输出（A/B, 每码片一行）——供解扩星座分析
  eye_mf.txt     采样级 MF 输出（前 4 帧窗口, 每类一帧）——供眼图
  result.json / report.md   分设备解析率

用法: python tb/rx_dual_mc/run_mixed_dev.py --frames 200 --tag mixdev1
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tb" / "rx_chain_e2e"))
import mc_gen          # noqa: E402
import run_dual_mc as R  # noqa: E402  (复用 build / VCS_ENV / inc_from_cfo)

DRIFT_HZ = 25e3          # dev4 的 CFO 漂移总量 (100k → 125k)
DEV_NAMES = ["dev1", "dev2", "dev3", "dev4"]
DEV_DESC = ["+100k 20B 20dB", "-100k 20B 20dB", "+100k 64B 14dB",
            "+100k→+125k 漂移 20B 20dB"]


def build_stream(frames):
    """每帧 (cfo_hz, (psdu_len, snr_db))；dev4 的 CFO 线性漂移。"""
    cfo, spec = [], []
    n4 = sum(1 for f in range(frames) if f % 4 == 3)
    k4 = 0
    for f in range(frames):
        m = f % 4
        if m == 0:
            cfo.append(100e3);  spec.append((20, 20.0))
        elif m == 1:
            cfo.append(-100e3); spec.append((20, 20.0))
        elif m == 2:
            cfo.append(100e3);  spec.append((64, 14.0))
        else:
            cfo.append(100e3 + DRIFT_HZ*(k4/max(1, n4-1)))
            spec.append((20, 20.0)); k4 += 1
    return cfo, spec


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--frames", type=int, default=200)
    ap.add_argument("--tag", default="mixdev1")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--snr", type=float, default=20.0, help="噪声基准（码片 SNR）")
    ap.add_argument("--phase-offset", type=int, default=10)
    a = ap.parse_args()

    out_dir = (ROOT / "model" / "out" / "dual_mc" / a.tag).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    cfo, spec = build_stream(a.frames)

    # tb 有改动（+ROTDUMP）, 强制重编 EXT 变体
    simv = R.build(ext=True, force=True)
    meta = mc_gen.gen_point(a.snr, a.frames, 20, 8.0, a.seed, 400, 600, out_dir,
                            gap_jitter=16, cfo_list=cfo, frame_specs=spec)
    meta["inc_a"] = R.inc_from_cfo(100e3)
    meta["inc_b"] = R.inc_from_cfo(-100e3)
    meta["phase_offset"] = a.phase_offset & 15
    meta["mode"] = "ext_phase_mixed"

    gt = np.load(out_dir / "frames.npz")
    fs = gt["frame_start"].astype(np.int64)
    off = a.phase_offset & 15
    (out_dir / "phtab.txt").write_text(
        "".join(f"{int(s)} {int((s+off)&15)}\n" for s in fs))

    eye_s, eye_e = int(fs[0]), int(fs[min(4, len(fs)-1)])
    cmd = [str(simv),
           f"+MEM={out_dir/'mem.bin'}", f"+NSMP={meta['n_smp']}",
           f"+OUT={out_dir/'events.txt'}",
           f"+INCA={meta['inc_a'] & 0xFFFFFF}", f"+INCB={meta['inc_b'] & 0xFFFFFF}",
           f"+PHTAB={out_dir/'phtab.txt'}",
           f"+ROTDUMP={out_dir/'rot.txt'}",
           f"+EYEDUMP={out_dir/'eye_mf.txt'}",
           f"+EYES={eye_s}", f"+EYEE={eye_e}"]
    t0 = time.time()
    r = subprocess.run(cmd, cwd=out_dir, env=R.VCS_ENV, capture_output=True, text=True)
    if r.returncode != 0 or not (out_dir / "events.txt").exists():
        sys.stderr.write(r.stdout[-4000:] + "\n" + r.stderr[-4000:])
        raise SystemExit("[mixed] sim FAILED")
    print(f"[mixed] sim ok ({time.time()-t0:.1f}s, {meta['n_smp']} 采样)")

    meta["per_frame_cfo"] = [float(c) for c in cfo]
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))

    # —— 评分: 按设备类型分组 ——
    ev = open(out_dir / "events.txt").read().splitlines()
    frm_a = np.zeros(a.frames, dtype=np.int8)
    frm_b = np.zeros(a.frames, dtype=np.int8)
    n_fst = {"A": 0, "B": 0}
    for l in ev:
        p = l.split()
        if not p:
            continue
        if p[0] in ("FRMA", "FRMB"):
            k = int(p[1]); f = int(np.searchsorted(fs, k, "right")) - 1
            if 0 <= f < a.frames:
                arr = frm_a if p[0] == "FRMA" else frm_b
                arr[f] = 1 if int(p[3]) else 2
        elif p[0] in ("FSTA", "FSTB"):
            n_fst[p[0][3]] += 1

    rows = []
    for m, name in enumerate(DEV_NAMES):
        idx = np.array([f for f in range(a.frames) if f % 4 == m])
        aa = int((frm_a[idx] == 1).sum()); bb = int((frm_b[idx] == 1).sum())
        ok = int(((frm_a[idx] == 1) | (frm_b[idx] == 1)).sum())
        rows.append(dict(dev=name, desc=DEV_DESC[m], n=len(idx),
                         a=aa, b=bb, ok=ok,
                         ok_rate=ok/max(1, len(idx))))
    tot_ok = int(((frm_a == 1) | (frm_b == 1)).sum())

    res = dict(n_frames=a.frames, per_dev=rows,
               total=dict(ok=tot_ok, ok_rate=tot_ok/a.frames),
               n_fst=n_fst,
               cfo_first=[float(c) for c in cfo[:8]])
    (out_dir / "result.json").write_text(json.dumps(res, indent=2))

    lines = [
        f"# 混合设备通信过程模拟（{a.tag}）", "",
        f"- 帧数 **{a.frames}**（4 类设备轮转），噪声基准 码片 SNR {a.snr:+.0f} dB，"
        f"真相位注入 offset=+{a.phase_offset & 15}（docs/16 §7.5）", "",
        "| 设备 | 类型 | 帧数 | 通道A | 通道B | 选优 |",
        "|---|---|---|---|---|---|",
    ]
    for r_ in rows:
        lines.append(f"| {r_['dev']} | {r_['desc']} | {r_['n']} | "
                     f"{r_['a']}/{r_['n']} | {r_['b']}/{r_['n']} | "
                     f"**{r_['ok']}/{r_['n']} = {r_['ok_rate']*100:.0f}%** |")
    lines += ["",
              f"**总计**：{tot_ok}/{a.frames} = {tot_ok/a.frames*100:.1f}%"
              f"（定界 A/B = {n_fst['A']}/{n_fst['B']}）", ""]
    rep = out_dir / "report.md"
    rep.write_text("\n".join(lines))
    print("\n".join(lines))
    print(f"[mixed] -> {rep}")


if __name__ == "__main__":
    main()
