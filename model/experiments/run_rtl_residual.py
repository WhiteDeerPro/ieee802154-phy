# -*- coding: utf-8 -*-
"""run_rtl_residual.py —— RTL 侧残余 CFO 容限（外部参数通道错配扫描）

方法：CFO 真值固定，通过外部参数通道（`--extinc`）强制一个**错配**的消旋值
（真值 + δ），扫描 δ，看顶层链（`rx_top`）的同步率 / 解帧率。

这直接测出 **baseband 对残余频偏的容忍度** —— 决定分辨单元宽度（docs/16）。
与 model 侧 `run_residual_cfo.py` 的两条曲线（genie/honest）互为对照：
RTL 的 `preamble_sync` 已实施方案 C（L1 模判据），预期比 model 的实部判据宽容。

运行: python model/experiments/run_rtl_residual.py [--frames 20] [--cfo 100000]
输出: model/out/rtl_residual/{report.md, rtl_residual.png}
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import _common

ROOT = Path(__file__).resolve().parents[2]
PY = str(ROOT / ".venv" / "bin" / "python")
RUN_MC = str(ROOT / "tb" / "rx_chain_e2e" / "run_mc.py")
PHASE_FS = 2 ** 24
FS = 16e6

DELTAS_KHZ = [0, 2, 4, 6, 8, 10, 15, 20, 30, 40, 50, 75]


def inc_from_hz(f_hz):
    """Hz -> phase_inc (24bit 满量程 2π) 定点值。"""
    return int(round(f_hz * PHASE_FS / FS))


def run_point(cfo_hz, delta_hz, frames, tag):
    inc = inc_from_hz(cfo_hz + delta_hz)
    out_dir = f"/tmp/rtl_res_{tag}"
    cmd = [PY, RUN_MC, "--snr-list=20", f"--cfo-list={int(cfo_hz)}",
           f"--frames={frames}", "--jobs=1", "--top-chain",
           f"--extinc={inc}", f"--out-dir={out_dir}"]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=600)
    txt = r.stdout + r.stderr
    m = re.search(r"synced=(\d+)/(\d+)", txt)
    f = re.search(r"fcs_ok=([\d.]+)%", txt)
    b = re.search(r"BER=([\d.eE+-]+)", txt)
    return (int(m.group(1)) / int(m.group(2)) if m else 0.0,
            float(f.group(1)) / 100 if f else 0.0,
            float(b.group(1)) if b else 1.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=20)
    ap.add_argument("--cfo", type=float, default=100e3)
    args = ap.parse_args()

    rows = []
    for d in DELTAS_KHZ:
        synced, fcs, ber = run_point(args.cfo, d * 1e3, args.frames, d)
        rows.append((d, synced, fcs, ber))
        print(f"  δ={d:3d} kHz  synced={synced:.0%}  fcs_ok={fcs:.0%}  BER={ber:.2e}")

    # ---- 图 ----
    plt = _common.init()
    OUT = _common.out_dir("rtl_residual")
    fig, ax = plt.subplots(figsize=(7, 4.2))
    xs = [r[0] for r in rows]
    ax.plot(xs, [r[1] * 100 for r in rows], "o-", label="同步率 (RTL)")
    ax.plot(xs, [r[2] * 100 for r in rows], "s--", label="解帧率 (RTL)")
    ax.set_xlabel("残余频偏 δ (kHz)")
    ax.set_ylabel("成功率 (%)")
    ax.set_title(f"RTL 侧残余 CFO 容限（外部参数通道错配, 真值 {args.cfo/1e3:.0f} kHz, "
                 f"{args.frames} 帧/点）")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(OUT / "rtl_residual.png", dpi=130)

    lines = [f"# RTL 侧残余 CFO 容限（{args.frames} 帧/点）\n",
             f"- 消旋值经外部参数通道强制为 `真值 + δ`（真值 {args.cfo/1e3:.0f} kHz）",
             "- 对照: model 侧 `run_residual_cfo.py`（genie 40–50 kHz / honest 2–4 kHz）\n",
             "| δ (kHz) | 同步率 | 解帧率 | BER |", "|---|---|---|---|"]
    for d, s, f, b in rows:
        lines.append(f"| {d} | {s:.0%} | {f:.0%} | {b:.2e} |")
    ok = [d for d, s, _, _ in rows if s >= 0.9]
    lines.append(f"\n**无损容限 δ_max ≈ {max(ok)} kHz** → 分辨单元全宽 ≈ "
                 f"{2*max(ok)} kHz（RTL 实测口径）")
    (OUT / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告: {OUT/'report.md'}  图: {OUT/'rtl_residual.png'}")


if __name__ == "__main__":
    main()
