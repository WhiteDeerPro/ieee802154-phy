#!/usr/bin/env python
"""run_dual_mc.py —— rx_dual 的蒙特卡洛：多设备交替发帧, 双通道各带一套参数

场景（§8.3 "双线并行"的 ref 验证）:
  · 激励: mc_gen 的 cfo_list 给**每帧一个 CFO** = 一台设备的晶振偏差（设备交替发帧）;
  · 接收: rx_dual 两个通道各带一组**固定**参数（码片级相位增量）——
    A 覆盖设备 1、B 覆盖设备 2（也可人为给"都覆盖设备 1"等退化配置做对照）;
  · 统计: 按设备分组, 比较 单通道 / 选优(A∨B) 的 FCS 成功率。
  判据是 FCS（帧校验）—— 与链路层的"这帧能不能用"同一口径。

为什么消旋只靠频率匹配即可: 非相干解扩（|·|²）对**符号内**的固定相位不敏感,
所以消旋器与设备的初相不必对齐, 只要频率（每码片相位增量）匹配。

用法:
  python tb/rx_dual_mc/run_dual_mc.py --frames 200 --cfo-list "100000,-100000"
  python tb/rx_dual_mc/run_dual_mc.py --frames 200 --cfo-list "100000,-100000" \
      --inc-a auto --inc-b auto --snr 20 --tag dev2
产物:
  model/out/dual_mc/<tag>/{mem.bin, events.txt, meta.json, result.json}
  model/out/dual_mc/report.md
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tb" / "rx_chain_e2e"))
import mc_gen  # noqa: E402

SIM_DIR = HERE / "sim_build"
SOURCES = [
    "tb/rx_dual_mc/dual_mc_tb.sv",
    "rtl/common/half_sine_fir.sv",
    "rtl/common/pn9_whiten.sv",
    "rtl/common/crc16_fcs.sv",
    "rtl/rx/rx_matched_filter.sv",
    "rtl/rx/cfo_rot.sv",
    "rtl/rx/preamble_sync.sv",
    "rtl/rx/preamble_detect.sv",
    "rtl/rx/deinterleave.sv",
    "rtl/rx/sfd_detect.sv",
    "rtl/rx/rx_frontend.sv",
    "rtl/rx/rx_chip_backend.sv",
    "rtl/rx/despreader.sv",
    "rtl/rx/rx_deframer.sv",
    "rtl/rx/rx_dual.sv",
]
PHASE_FS = 1 << 24
SPS = 8
CHIP_RATE = 2e6
FS = SPS * CHIP_RATE
POP4 = np.array([bin(i).count("1") for i in range(16)], dtype=np.int32)

VCS_ENV = dict(
    os.environ,
    VCS_HOME=os.environ.get("VCS_HOME", "/opt/synopsys/vcs201809"),
    VCS_TARGET_ARCH=os.environ.get("VCS_TARGET_ARCH", "amd64"),
    SNPSLMD_LICENSE_FILE=os.environ.get("SNPSLMD_LICENSE_FILE",
                                        "/opt/synopsys/Synopsys.dat"),
    LM_LICENSE_FILE=os.environ.get("LM_LICENSE_FILE", "/opt/synopsys/Synopsys.dat"),
)


def inc_from_cfo(cfo_hz: float) -> int:
    """CFO → **码片级**相位增量（与 tb/rx_dual 的 INC_SMP*SPS 同口径）。"""
    return int(round(cfo_hz * PHASE_FS / FS)) * SPS


def build(force=False, ext=False) -> Path:
    """VCS 编译一次, 所有点复用。ext=True 编 EXTPH=1 变体（相位外置）。"""
    SIM_DIR.mkdir(parents=True, exist_ok=True)
    simv = SIM_DIR / ("simv_dual_mc_ext" if ext else "simv_dual_mc")
    if simv.exists() and not force:
        return simv
    csrc = SIM_DIR / ("csrc_dual_mc_ext" if ext else "csrc_dual_mc")
    csrc.mkdir(parents=True, exist_ok=True)
    cmd = [f"{VCS_ENV['VCS_HOME']}/bin/vcs", "-full64", "-sverilog",
           "-timescale=1ns/1ps", "-o", str(simv),
           "+incdir+" + str(ROOT / "rtl" / "rx")]
    if ext:
        cmd += ["-pvalue+dual_mc_tb.EXTPH=1"]
    cmd += [str(ROOT / s) for s in SOURCES]
    t0 = time.time()
    r = subprocess.run(cmd, cwd=csrc, env=VCS_ENV, capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stdout[-4000:] + "\n" + r.stderr[-4000:])
        raise SystemExit("[run_dual_mc] VCS build FAILED")
    print(f"[run_dual_mc] build ok ({time.time()-t0:.1f}s) -> {simv}")
    return simv


def run_point(simv: Path, out_dir: Path, cfo_list, snr, frames, inc_a, inc_b,
              psdu_len=20, seed=1, force_gen=False,
              phase_offset=None) -> dict:
    """生成激励 →（可选: 写真相位表）→ 跑 → 返回 meta。

    phase_offset 非 None 时进入"真相位注入"模式: 每帧相位 =
    (frame_start + phase_offset) & 15（标定值 8, 见 dev2 成功帧投票），
    经 +PHTAB 喂给 SYNC_DIRECT=1 的 DUT（对照实验: 分离扫描命中率）。
    """
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = mc_gen.gen_point(snr, frames, psdu_len, 8.0, seed, 400, 600, out_dir,
                            gap_jitter=16, cfo_list=cfo_list)
    cmd = [str(simv),
           f"+MEM={out_dir / 'mem.bin'}",
           f"+NSMP={meta['n_smp']}",
           f"+OUT={out_dir / 'events.txt'}",
           f"+CKS={meta['mem_cks']}",
           f"+INCA={inc_a & 0xFFFFFF}",
           f"+INCB={inc_b & 0xFFFFFF}"]
    if phase_offset is not None:
        gt = np.load(out_dir / "frames.npz")
        fs = gt["frame_start"].astype(np.int64)
        off = int(phase_offset) & 15
        phtab = out_dir / "phtab.txt"
        phtab.write_text("".join(
            f"{int(s)} {int((s + off) & 15)}\n" for s in fs))
        cmd.append(f"+PHTAB={phtab}")
        meta["phase_offset"] = off
    t0 = time.time()
    r = subprocess.run(cmd, cwd=out_dir, env=VCS_ENV, capture_output=True, text=True)
    if r.returncode != 0 or not (out_dir / "events.txt").exists():
        # tb 的 FATAL $finish 返回码为 0, 故必须检查产物存在性
        sys.stderr.write(r.stdout[-4000:] + "\n" + r.stderr[-4000:])
        raise SystemExit("[run_dual_mc] sim FAILED")
    print(f"[run_dual_mc] sim ok ({time.time()-t0:.1f}s, {meta['n_smp']} 采样)")
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))
    return meta


def score(point_dir: Path, meta: dict) -> dict:
    """读 events.txt + frames.npz → 按设备分组的成功率。"""
    gt = np.load(point_dir / "frames.npz")
    frame_start = gt["frame_start"].astype(np.int64)
    tx_syms = gt["tx_syms"]
    N, K = tx_syms.shape
    cfo_pf = meta.get("cfo_per_frame") or [0.0] * N

    frm_a = np.zeros(N, dtype=np.int8)      # 0 未报 / 1 fcs_ok / 2 fcs_fail
    frm_b = np.zeros(N, dtype=np.int8)
    n_sym = {"A": 0, "B": 0}
    n_fst = {"A": 0, "B": 0}
    sym_err = {"A": [0, 0], "B": [0, 0]}    # [错, 总] (PHR+PSDU 口径, 粗)
    with open(point_dir / "events.txt") as fh:
        for line in fh:
            p = line.split()
            if not p:
                continue
            tag = p[0]
            if tag in ("FRMA", "FRMB"):
                k = int(p[1])
                fcs = int(p[3], 0)
                f = int(np.searchsorted(frame_start, k, "right")) - 1
                if 0 <= f < N:
                    arr = frm_a if tag == "FRMA" else frm_b
                    arr[f] = 1 if fcs else 2
            elif tag in ("SYMA", "SYMB"):
                n_sym[tag[3]] += 1
            elif tag in ("FSTA", "FSTB"):
                n_fst[tag[3]] += 1

    devs = sorted(set(cfo_pf))
    rows = []
    for d in devs:
        idx = np.array([i for i in range(N) if cfo_pf[i] == d])
        a = int((frm_a[idx] == 1).sum())
        b = int((frm_b[idx] == 1).sum())
        ok = int(((frm_a[idx] == 1) | (frm_b[idx] == 1)).sum())
        rows.append(dict(cfo_hz=d, n=len(idx), a=a, b=b, ok=ok,
                         a_rate=a / len(idx), b_rate=b / len(idx),
                         ok_rate=ok / len(idx)))
    tot_a = int((frm_a == 1).sum())
    tot_b = int((frm_b == 1).sum())
    tot_ok = int(((frm_a == 1) | (frm_b == 1)).sum())
    return dict(n_frames=N, per_device=rows,
                total=dict(a=tot_a, b=tot_b, ok=tot_ok,
                           a_rate=tot_a / N, b_rate=tot_b / N, ok_rate=tot_ok / N),
                n_sym=n_sym, n_fst=n_fst,
                inc_a=meta.get("inc_a"), inc_b=meta.get("inc_b"))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--frames", type=int, default=200)
    ap.add_argument("--snr", type=float, default=20.0)
    ap.add_argument("--cfo-list", type=str, default="100000,-100000")
    ap.add_argument("--inc-a", type=str, default="auto",
                    help="'auto' = 用 cfo_list[0] 换算; 或直接给码片级增量")
    ap.add_argument("--inc-b", type=str, default="auto",
                    help="'auto' = 用 cfo_list[1] 换算（不足则同 [0]）")
    ap.add_argument("--tag", type=str, default="dev2")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out-root", type=str, default=str(ROOT / "model" / "out" / "dual_mc"))
    ap.add_argument("--force-build", action="store_true")
    ap.add_argument("--ext-phase", action="store_true",
                    help="真相位注入模式（SYNC_DIRECT=1 + PHTAB）: 对照实验用")
    ap.add_argument("--phase-offset", type=int, default=8,
                    help="真相位标定常数（默认 8, 来自 dev2 成功帧投票）")
    a = ap.parse_args()

    cfo_list = [float(x) for x in a.cfo_list.split(",") if x.strip()]
    inc_a = (inc_from_cfo(cfo_list[0]) if a.inc_a == "auto" else int(a.inc_a))
    inc_b = (inc_from_cfo(cfo_list[1] if len(cfo_list) > 1 else cfo_list[0])
             if a.inc_b == "auto" else int(a.inc_b))

    simv = build(force=a.force_build, ext=a.ext_phase)
    out_dir = Path(a.out_root) / a.tag
    meta = run_point(simv, out_dir, cfo_list, a.snr, a.frames, inc_a, inc_b,
                     seed=a.seed,
                     phase_offset=a.phase_offset if a.ext_phase else None)
    meta["inc_a"] = inc_a
    meta["inc_b"] = inc_b
    meta["mode"] = "ext_phase" if a.ext_phase else "scan"
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))

    res = score(out_dir, meta)
    (out_dir / "result.json").write_text(json.dumps(res, indent=2))

    # —— 报告 ——
    mode_txt = (f"**真相位注入**（(frame_start+{a.phase_offset & 15})&15, 无扫描）"
                if a.ext_phase else "扫描（SYNC_DIRECT=0, 自由周期 + 帧内可能重写）")
    lines = [
        f"# rx_dual 蒙特卡洛：多设备 × 双通道（{a.tag}）",
        "",
        f"- 帧数 **{res['n_frames']}**，码片 SNR {a.snr:+.0f} dB，"
        f"设备 CFO = {cfo_list} Hz（每帧轮换）",
        f"- 相位来源: {mode_txt}",
        f"- 通道 A 参数 phase_inc = {inc_a}（码片级，≈ {inc_a/PHASE_FS*CHIP_RATE/1e3:.1f} kHz）",
        f"- 通道 B 参数 phase_inc = {inc_b}（码片级，≈ {inc_b/PHASE_FS*CHIP_RATE/1e3:.1f} kHz）",
        "",
        "| 设备 CFO | 帧数 | 通道A 成功 | 通道B 成功 | 选优(A∨B) |",
        "|---|---|---|---|---|",
    ]
    for r in res["per_device"]:
        lines.append(
            f"| {r['cfo_hz']/1e3:+.0f} kHz | {r['n']} | {r['a_rate']*100:.1f}% "
            f"| {r['b_rate']*100:.1f}% | **{r['ok_rate']*100:.1f}%** |")
    t = res["total"]
    lines += [
        "",
        f"**总计**：A {t['a_rate']*100:.1f}% / B {t['b_rate']*100:.1f}% / "
        f"选优 **{t['ok_rate']*100:.1f}%** "
        f"（{t['ok']}/{res['n_frames']} 帧至少被一个通道解出）",
        "",
        f"事件计数：符号 A/B = {res['n_sym']['A']}/{res['n_sym']['B']}，"
        f"定界 A/B = {res['n_fst']['A']}/{res['n_fst']['B']}",
        "",
    ]
    rep = Path(a.out_root) / "report.md"
    rep.parent.mkdir(parents=True, exist_ok=True)
    rep.write_text("\n".join(lines))
    print("\n".join(lines))
    print(f"[run_dual_mc] -> {rep}")


if __name__ == "__main__":
    main()
