#!/usr/bin/env python
"""run_mc.py —— 蒙特卡洛平台: 编译 RTL → 生成激励 → VCS 运行 → 统计 BER

平台形态 (为什么这样):
  cocotb 逐采样驱动 ~300 µs/采样, 蒙特卡洛要 1e6 比特 → 小时级不可行;
  改为"文件向量驱动": Python 预生成激励 mem (mc_gen.py), VCS 自主播放并 dump
  事件 (mc_tb.sv), Python 后处理统计。实测纯仿真速度 (快 ~1000 倍), 且每个
  SNR 点独立成进程, 天然并行。

统计口径 (与 model/measure.frame_bit_errors 对齐, 但只算 PHR+PSDU):
  · 每帧区间 [frame_start[f], frame_start[f+1]) 内的 SYM 事件 = RTL 解出符号流
  · 与发送符号流 [SHR 之后] 逐符号 XOR 计 popcount = 比特错误
  · 符号数不足 (同步失败/截断) 时, 缺失符号按 4 bit 全错计 (保守, 同模型口径)
  · n_bit = 4 × (2 + 2·psdu_len) 每帧 (PHR + PSDU; SHR 只体现为同步成败)

用法:
  python tb/rx_chain_e2e/run_mc.py --snr-list -4,-2,0,2 --frames 2000 --jobs 4
产物:
  model/out/rtl_ber/data/snr<p>/   mem.bin / events.txt / meta.json / result.json
  model/out/rtl_ber/data/summary.json
"""
import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mc_gen  # noqa: E402

SIM_DIR = HERE / "sim_build_mc"
SOURCES = [
    "tb/rx_chain_e2e/mc_tb.sv",
    "rtl/common/half_sine_fir.sv",
    "rtl/common/pn9_whiten.sv",
    "rtl/common/crc16_fcs.sv",
    "rtl/rx/frontend/rx_matched_filter.sv",
    "rtl/rx/frontend/preamble_sync.sv",
    "rtl/rx/backend/despreader.sv",
    "rtl/rx/backend/rx_deframer.sv",
]
# CFO 链: 主链插入 cfo_rot (消旋), cfo_est 挂 MF 输出 (前导联合估计)
CFO_SOURCES = [
    "tb/rx_chain_e2e/mc_cfo_tb.sv",
    "rtl/common/half_sine_fir.sv",
    "rtl/common/pn9_whiten.sv",
    "rtl/common/crc16_fcs.sv",
    "rtl/rx/frontend/rx_matched_filter.sv",
    "rtl/rx/legacy/cfo_est.sv",
    "rtl/rx/backend/cfo_rot.sv",
    "rtl/rx/legacy/cordic_atan2.sv",
    "rtl/rx/frontend/preamble_sync.sv",
    "rtl/rx/backend/despreader.sv",
    "rtl/rx/backend/rx_deframer.sv",
]
# 顶层集成链: DUT = rx_top (ADC→MF→rot→sync→despread→deframer, CFO free-running)
TOP_SOURCES = [
    "tb/rx_chain_e2e/mc_top_tb.sv",
    "rtl/rx/legacy/rx_top.sv",
    "rtl/rx/frontend/preamble_detect.sv",
] + CFO_SOURCES[1:]
POP4 = np.array([bin(i).count("1") for i in range(16)], dtype=np.int32)

VCS_ENV = dict(
    os.environ,
    VCS_HOME=os.environ.get("VCS_HOME", "/opt/synopsys/vcs201809"),
    VCS_TARGET_ARCH=os.environ.get("VCS_TARGET_ARCH", "amd64"),
    SNPSLMD_LICENSE_FILE=os.environ.get("SNPSLMD_LICENSE_FILE",
                                        "/opt/synopsys/Synopsys.dat"),
    LM_LICENSE_FILE=os.environ.get("LM_LICENSE_FILE", "/opt/synopsys/Synopsys.dat"),
)


def point_name(snr: float, cfo_hz: float = 0.0) -> str:
    base = f"snr{snr:+g}".replace("+", "p").replace("-", "m")
    if cfo_hz:
        base += f"_cfo{cfo_hz/1e3:g}k".replace(".", "d")
    return base


def build(force=False, cfo_chain=False, top_chain=False, pvals=None):
    """vcs 编译一次, 所有点复用。

    三条链: 基础 (mc_tb) / CFO (mc_cfo_tb) / 顶层集成 (mc_top_tb + rx_top)。
    注意: 三条链的中间产物 (csrc) 必须分开 —— 否则交叉构建会覆盖对方的共享库,
    使先前编好的 simv 运行时报 "_NNNNN_archive_1.so: cannot open shared object file"。
    pvals: {参数名: 整数} —— 顶层链的编译期扫参 (与 simv 名绑定, 避免互相覆盖)。
    """
    tag = "top" if top_chain else ("cfo" if cfo_chain else "mc")
    srcs = TOP_SOURCES if top_chain else (CFO_SOURCES if cfo_chain else SOURCES)
    if top_chain:
        # DPI-C 观测器（"固件在环"最小演示）: C 源随顶层链一起编译
        srcs = list(srcs) + ["tb/rx_chain_e2e/observer_dpi.c"]
    if top_chain and pvals:
        tag += "_" + "_".join(f"{k}{'' if v is None else v}"
                               for k, v in sorted(pvals.items()))
    SIM_DIR.mkdir(parents=True, exist_ok=True)
    simv = SIM_DIR / f"simv_mc_{tag}"
    if simv.exists() and not force:
        return simv
    csrc = SIM_DIR / f"csrc_{tag}"
    csrc.mkdir(parents=True, exist_ok=True)
    # 编译中间产物落各自 csrc, 不传 -Mdir (VCS 2018 对 -Mdir 取值格式挑剔)
    cmd = [f"{VCS_ENV['VCS_HOME']}/bin/vcs", "-full64", "-sverilog",
           "-timescale=1ns/1ps", "-o", str(simv)]
    if cfo_chain or top_chain:
        # c_ref_rom.svh / rot_lut.svh 用 `include 引入, 需要 incdir
        cmd.append("+incdir+" + str(ROOT / "rtl" / "rx"))
    for k, v in sorted((pvals or {}).items()):
        if v is not None:
            cmd.append(f"-pvalue+mc_top_tb.{k}={v}")
    cmd += [str(ROOT / s) for s in srcs]
    t0 = time.time()
    r = subprocess.run(cmd, cwd=csrc, env=VCS_ENV,
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stdout[-4000:] + "\n" + r.stderr[-4000:])
        raise SystemExit("[run_mc] VCS build FAILED")
    print(f"[run_mc] build ok ({time.time()-t0:.1f}s) -> {simv}")
    return simv


def score(point_dir: Path) -> dict:
    """读 events.txt + frames.npz → 统计。"""
    gt = np.load(point_dir / "frames.npz")
    frame_start = gt["frame_start"].astype(np.int64)
    tx_syms = gt["tx_syms"]
    psdu = gt["psdu"]
    N, K = tx_syms.shape

    sym_k, sym_v = [], []
    byt_k, byt_v = [], []
    frm_k, frm_len, frm_ok = [], [], []
    fst_k = []
    n_det = 0
    with open(point_dir / "events.txt") as fh:
        for line in fh:
            p = line.split()
            if not p:
                continue
            if p[0] == "SYM":
                sym_k.append(int(p[1])); sym_v.append(int(p[2], 0))
            elif p[0] == "BYT":
                byt_k.append(int(p[1])); byt_v.append(int(p[2], 0))
            elif p[0] == "FRM":
                frm_k.append(int(p[1])); frm_len.append(int(p[2], 0))
                frm_ok.append(int(p[3], 0))
            elif p[0] == "FST":
                fst_k.append(int(p[1]))
            elif p[0] == "DET":
                n_det += 1
    sym_k = np.array(sym_k, dtype=np.int64)
    sym_v = np.array(sym_v, dtype=np.int32)
    byt_k = np.array(byt_k, dtype=np.int64)
    byt_v = np.array(byt_v, dtype=np.int32)
    fst_k = np.array(fst_k, dtype=np.int64)

    bounds = np.concatenate([frame_start, [np.iinfo(np.int64).max]])
    n_bit_err = n_bit = 0
    n_sym_err = n_sym = 0
    n_frames_synced = 0        # 解出符号数 ≥ K 的帧
    n_frame_done = n_fcs_ok = n_psdu_ok = 0
    n_extra_sym = 0            # 区间内多出的符号 (下一帧误检等)
    per_frame_err = np.zeros(N, dtype=np.int64)
    per_frame_fcs = np.zeros(N, dtype=np.int8)

    for f in range(N):
        lo, hi = bounds[f], bounds[f + 1]
        # 符号流起点 = 本帧 frame_start (之前是前导期的无关符号);
        # 无 frame_start = 同步失败帧, 整帧计错 (保守口径)
        g0 = int(np.searchsorted(fst_k, lo, "left"))
        g1 = int(np.searchsorted(fst_k, hi, "left"))
        start = int(fst_k[g0]) if g1 > g0 else hi
        i0 = int(np.searchsorted(sym_k, start, "right"))
        i1 = int(np.searchsorted(sym_k, hi, "left"))
        got = sym_v[i0:i1]
        exp = tx_syms[f].astype(np.int32)
        m = min(len(got), K)
        err = 0
        if m:
            err = int(POP4[(got[:m] & 0xF) ^ (exp[:m] & 0xF)].sum())
        err += 4 * (K - m)                      # 缺失符号 = 全错 (保守口径)
        n_bit_err += err
        n_bit += 4 * K
        n_sym_err += int(np.count_nonzero(((got[:m] & 0xF) ^ (exp[:m] & 0xF)) != 0)) + (K - m)
        n_sym += K
        per_frame_err[f] = err
        if len(got) >= K:
            n_frames_synced += 1
        if len(got) > K:
            n_extra_sym += len(got) - K

        j0 = int(np.searchsorted(byt_k, lo, "left"))
        j1 = int(np.searchsorted(byt_k, hi, "left"))
        if j1 - j0 == psdu.shape[1]:
            if np.array_equal(byt_v[j0:j1].astype(np.uint8), psdu[f]):
                n_psdu_ok += 1

        k0 = int(np.searchsorted(frm_k, lo, "left"))
        k1 = int(np.searchsorted(frm_k, hi, "left"))
        if k1 > k0:
            n_frame_done += k1 - k0
            n_fcs_ok += int(np.sum(np.array(frm_ok[k0:k1]) != 0))
            per_frame_fcs[f] = int(frm_ok[k1 - 1])

    meta = json.loads((point_dir / "meta.json").read_text())
    out = dict(
        snr_db=meta["snr_db"], n_frames=N, n_bit=int(n_bit),
        n_bit_err=int(n_bit_err), ber=n_bit_err / max(1, n_bit),
        n_sym=int(n_sym), n_sym_err=int(n_sym_err), ser=n_sym_err / max(1, n_sym),
        n_frames_synced=int(n_frames_synced), n_frame_done=int(n_frame_done),
        n_fcs_ok=int(n_fcs_ok), n_psdu_ok=int(n_psdu_ok), n_detect=n_det,
        n_frame_start=len(fst_k), n_extra_sym=int(n_extra_sym),
        frame_done_ratio=n_frame_done / N, fcs_ok_ratio=n_fcs_ok / N,
        clip_frac=meta["clip_frac"], n_smp=meta["n_smp"], sigma=meta["sigma"],
    )
    np.savez_compressed(point_dir / "per_frame.npz",
                        bit_err=per_frame_err, fcs_ok=per_frame_fcs)
    (point_dir / "result.json").write_text(json.dumps(out, indent=2))
    return out


def run_point(snr, cfo_hz, frames, args, simv, data_dir):
    """单点 (snr, cfo): 生成 → 仿真 → 统计。"""
    # 绝对路径: simv 的 cwd 是本点目录, 相对路径会被二次拼接
    pdir = (data_dir / point_name(snr, cfo_hz)).resolve()
    t0 = time.time()
    meta = mc_gen.gen_point(snr, frames, args.psdu_len, args.scale, args.seed,
                            args.gap, args.tail, pdir,
                            gap_jitter=args.gap_jitter, cfo_hz=cfo_hz,
                            iq_gain_db=args.iq_gain_db, iq_phase_deg=args.iq_phase_deg)
    t_gen = time.time() - t0

    t0 = time.time()
    sim_cmd = [str(simv), f"+MEM={pdir/'mem.bin'}", f"+NSMP={meta['n_smp']}",
               f"+OUT={pdir/'events.txt'}", f"+CKS={meta['mem_cks']}"]
    if args.cfo_chain or getattr(args, "top_chain", False):
        sim_cmd += [f"+FRAMES={pdir/'frames.txt'}", f"+NFRAMES={frames}"]
    if getattr(args, "fixinc", None) is not None and (
            args.cfo_chain or getattr(args, "top_chain", False)):
        sim_cmd.append(f"+FIXINC={args.fixinc}")   # 诊断: 跳过估计用固定 phase_inc
    if getattr(args, "top_chain", False) and getattr(args, "no_cfo", False):
        sim_cmd.append("+NOCFO=0")                  # 顶层链: 旁路消旋 (对照)
    if getattr(args, "top_chain", False) and getattr(args, "fdelay", 0):
        sim_cmd.append(f"+FDELAY={args.fdelay}")    # 顶层链: 触发点相对帧起点额外偏移
    if getattr(args, "top_chain", False) and args.trigext != 1:
        sim_cmd.append(f"+TRIGEXT={args.trigext}")  # 0: 自主 (detect) 触发
    if getattr(args, "top_chain", False) and getattr(args, "extinc", None) is not None:
        sim_cmd.append(f"+EXTINC={args.extinc}")    # 外部参数通道: 强制 phase_inc
    if getattr(args, "top_chain", False) and getattr(args, "dpi", False):
        sim_cmd.append("+DPI=1")                    # DPI 观测器（固件在环）
    if getattr(args, "top_chain", False) and getattr(args, "extlck", None) is not None:
        sim_cmd.append(f"+EXTLCK={args.extlck}")    # 外部定时通道: 强制相位
    r = subprocess.run(sim_cmd, cwd=pdir, env=VCS_ENV, capture_output=True, text=True)
    if r.returncode != 0 or "done:" not in r.stdout:
        (pdir / "sim.log").write_text(
            f"returncode={r.returncode}\n--- stdout ---\n{r.stdout}\n--- stderr ---\n{r.stderr}")
        raise RuntimeError(f"[run_mc] simv failed for snr={snr} (see {pdir/'sim.log'})")
    t_sim = time.time() - t0

    res = score(pdir)
    res.update(cfo_hz=cfo_hz, gen_secs=round(t_gen, 1), sim_secs=round(t_sim, 1))
    (pdir / "result.json").write_text(json.dumps(res, indent=2))
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snr-list", required=True,
                    help="码片 SNR (dB) 逗号分隔, 如 -4,-2,0,2")
    ap.add_argument("--cfo-list", default="0",
                    help="载波频偏 (Hz) 逗号分隔; 与 snr-list 笛卡尔积, 如 0,1000,2000")
    ap.add_argument("--frames", type=int, default=2000)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--psdu-len", type=int, default=20)
    ap.add_argument("--scale", type=float, default=8.0)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--gap", type=int, default=400)
    ap.add_argument("--tail", type=int, default=600)
    ap.add_argument("--gap-jitter", type=int, default=16)
    ap.add_argument("--out-dir", default=str(ROOT / "model" / "out" / "rtl_ber" / "data"))
    ap.add_argument("--cfo-chain", action="store_true",
                    help="用 CFO 链 (mc_cfo_tb: MF→cfo_rot→sync) 代替基础链")
    ap.add_argument("--top-chain", action="store_true",
                    help="用顶层集成链 (mc_top_tb + rx_top: ADC→…→deframer, CFO free-running)")
    ap.add_argument("--no-cfo", action="store_true",
                    help="顶层链专用: 旁路消旋 (cfo_en=0) 作对照")
    ap.add_argument("--fdelay", type=int, default=0,
                    help="顶层链专用: 外部触发器相对帧起点的额外偏移 (扫描 cfo_est 的 Δ 容限)")
    ap.add_argument("--chipoff", type=int, default=None,
                    help="顶层链专用: 自主触发的 EST_CHIP_OFF_AUTO (默认 3; 传 0 可显式关闭)")
    ap.add_argument("--est-nsmp", type=int, default=None,
                    help="顶层链专用: EST_NSMP (编译期参数, 默认 2048)")
    ap.add_argument("--est-skip-t3", type=int, default=None,
                    help="顶层链专用: 自主触发的 EST_SKIP_T3_AUTO (默认 0)")
    ap.add_argument("--trigext", type=int, default=1,
                    help="顶层链专用: 1=外部 est_start (默认); 0=自主 detect 触发")
    ap.add_argument("--extinc", type=int, default=None,
                    help="外部参数通道: 强制 phase_inc=<24bit 有符号> (绕开内部估计; 正式端口 ext_inc)")
    ap.add_argument("--dpi", action="store_true",
                    help="DPI 观测器（固件在环）: est_done 时调用 C 侧 observer_dpi.c, LOCK 后接管消旋参数")
    ap.add_argument("--extlck", type=int, default=None,
                    help="外部定时通道: 跳过 16 候选扫描, 直接用该相位去交错 (0..15)")
    ap.add_argument("--force-build", action="store_true")
    ap.add_argument("--fixinc", type=int, default=None,
                    help="诊断: 固定 phase_inc (跳过估计); 0 = 完全不消旋; 顶层链/CFO 链均有效")
    ap.add_argument("--iq-gain-db", type=float, default=0.0,
                    help="I/Q 增益失衡 (dB, 模拟域注入: CFO 后、ADC 前)")
    ap.add_argument("--iq-phase-deg", type=float, default=0.0,
                    help="I/Q 相位失衡 (度)")
    a = ap.parse_args()

    snrs = [float(x) for x in a.snr_list.split(",") if x.strip()]
    cfos = [float(x) for x in a.cfo_list.split(",") if x.strip()]
    combos = [(s, c) for s in snrs for c in cfos]
    data_dir = Path(a.out_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    pvals = {}
    if getattr(a, "top_chain", False):
        # 仅显式指定才进 tag/编译, 否则用 RTL/TB 默认 (自主触发: chip_off=3, skip_t3=0)
        if a.chipoff is not None:
            pvals["CHIP_OFF_P"] = a.chipoff
        if a.est_nsmp is not None:
            pvals["NSMP_P"] = a.est_nsmp
        if a.est_skip_t3 is not None:
            pvals["SKIP_T3_P"] = a.est_skip_t3
    simv = build(a.force_build, a.cfo_chain, getattr(a, "top_chain", False), pvals)
    chain_tag = "顶层集成" if getattr(a, "top_chain", False) else ("CFO链" if a.cfo_chain else "基础链")

    results = {}
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=min(a.jobs, len(combos))) as ex:
        futs = {ex.submit(run_point, s, c, a.frames, a, simv, data_dir): (s, c)
                for s, c in combos}
        for fut in as_completed(futs):
            s, c = futs[fut]
            try:
                res = fut.result()
            except Exception as e:
                print(f"[run_mc] snr={s:+g} cfo={c/1e3:g}k 并行运行失败 ({e}); 串行重试")
                res = run_point(s, c, a.frames, a, simv, data_dir)
            results[(s, c)] = res
            print(f"[run_mc:{chain_tag}] snr={s:+g} dB cfo={c/1e3:6.1f} kHz  "
                  f"BER={res['ber']:.3e} ({res['n_bit_err']}/{res['n_bit']})  "
                  f"fcs_ok={res['fcs_ok_ratio']*100:.1f}%  "
                  f"synced={res['n_frames_synced']}/{res['n_frames']}  "
                  f"gen {res['gen_secs']}s sim {res['sim_secs']}s")

    summary = dict(
        frames=a.frames, psdu_len=a.psdu_len, scale=a.scale, seed=a.seed,
        gap=a.gap, tail=a.tail, gap_jitter=a.gap_jitter,
        points={point_name(s, c): results[(s, c)] for s, c in combos},
        total_secs=round(time.time() - t0, 1),
    )
    (data_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"[run_mc] total {summary['total_secs']}s → {data_dir/'summary.json'}")


if __name__ == "__main__":
    main()
