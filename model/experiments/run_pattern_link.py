#!/usr/bin/env python
"""run_pattern_link.py —— 模式库接入真实链路: two_stage vs pattern

问题: `stage_cfo_correct` 的两种估计方式在**多帧**下的代价与效果:
  A. 'two_stage'  每帧独立盲估 (粗搜 + 前导相位差分)
  B. 'pattern'    查模式库: 命中直接取旋性, 失配才 FFT 发现 (跨帧记忆)

场景: 单设备 (固定旋性) 与 多设备交替 (旋性轮换), 各 N 帧。
指标: PER / CFO 估计误差 / match 比例 / 单帧耗时。

注意: 模式库的"帧起点粗估计"用 0 (链路里帧从 0 开始, MF 群延迟 < 1 码片),
      模式库自己扫抽取相位覆盖 —— 这正是它比"每帧盲估"省的地方。

输出: model/out/pattern_link/report.md
"""
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "model"))

import chains                                   # noqa: E402
from baseband import impairments as imp         # noqa: E402
from upper.patterns import PatternLibrary, load_reference   # noqa: E402

OUT = ROOT / "model" / "out" / "pattern_link"
SNR_CHIP = 18.0                 # 目标工作点 (README: -85 dBm 灵敏度 => 码片 SNR 18 dB)
N_FRAMES = 40
DEV_CFOS = [50e3, 150e3, 350e3]
PSDU_LEN = 20


def make_psdu(seed):
    return np.random.default_rng(seed).integers(0, 256, PSDU_LEN, dtype=np.uint8).tobytes()


def build(cfo, kind, lib=None):
    return chains.link(
        [imp.cfo_stage(cfo), chains.awgn(SNR_CHIP, np.random.default_rng(3))],
        cfo=kind, pattern_lib=lib, deframe=True, sync="honest")


def run_case(name, cfos, kind, lib=None, n=N_FRAMES):
    """cfos: 每帧的旋性列表 (单设备 = 全相同; 多设备 = 轮换)。"""
    chains_ = {c: build(c, kind, lib) for c in set(cfos)}
    ok = 0
    errs, acts = [], []
    t0 = time.time()
    for i in range(n):
        c = cfos[i % len(cfos)]
        sig = chains_[c].run(payload=make_psdu(i))
        fcs = sig.meta.get("fcs_ok")
        ok += int(bool(fcs))
        e = sig.meta.get("cfo_est")
        if e is not None:
            errs.append(abs(e - c))
        a = sig.meta.get("pattern_act")
        if a:
            acts.append(a)
    dt = time.time() - t0
    nm = acts.count("match")
    nd = acts.count("discover")
    return dict(name=name, ok=ok, n=n, per=1 - ok / n,
                err=np.median(errs) if errs else float("nan"),
                nm=nm, nd=nd, secs=dt, per_frame_ms=dt / n * 1e3)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    cref = load_reference()
    rows = []
    print(f"SNR(码片) = {SNR_CHIP} dB, {N_FRAMES} 帧/场景\n")
    print(f"{'场景':>22} {'CFO估计误差中位':>14} {'PER':>7} {'match/discover':>16} {'ms/帧':>8}")
    print("-" * 74)

    # A. 单设备, two_stage (基线)
    cfos = [DEV_CFOS[0]] * N_FRAMES
    r = run_case("单设备 two_stage", cfos, "two_stage")
    rows.append(("单设备 · two_stage(基线)", r))
    print(f"{r['name']:>22} {r['err']/1e3:>12.1f}k {r['per']:>7.1%} {'-':>16} {r['per_frame_ms']:>8.1f}")

    # B. 单设备, pattern
    lib1 = PatternLibrary(cref, max_patterns=4, match_th=0.45)
    r = run_case("单设备 pattern", cfos, "pattern", lib1)
    rows.append(("单设备 · pattern(库)", r))
    print(f"{r['name']:>22} {r['err']/1e3:>12.1f}k {r['per']:>7.1%} "
          f"{r['nm']:>7}/{r['nd']:<7} {r['per_frame_ms']:>8.1f}")

    # C. 多设备交替, two_stage
    cfos = [DEV_CFOS[i % len(DEV_CFOS)] for i in range(N_FRAMES)]
    r = run_case("多设备 two_stage", cfos, "two_stage")
    rows.append(("3设备交替 · two_stage(基线)", r))
    print(f"{r['name']:>22} {r['err']/1e3:>12.1f}k {r['per']:>7.1%} {'-':>16} {r['per_frame_ms']:>8.1f}")

    # D. 多设备交替, pattern
    lib2 = PatternLibrary(cref, max_patterns=6, match_th=0.45)
    r = run_case("多设备 pattern", cfos, "pattern", lib2)
    rows.append(("3设备交替 · pattern(库)", r))
    print(f"{r['name']:>22} {r['err']/1e3:>12.1f}k {r['per']:>7.1%} "
          f"{r['nm']:>7}/{r['nd']:<7} {r['per_frame_ms']:>8.1f}")

    md = ["# 模式库接入真实链路: two_stage vs pattern", "",
          f"SNR(码片) = {SNR_CHIP} dB，每场景 {N_FRAMES} 帧，PSDU {PSDU_LEN} B。",
          f"旋性集合 {', '.join(f'{c/1e3:.0f} kHz' for c in DEV_CFOS)}。", "",
          "| 场景 | CFO 估计误差中位 | PER | match/discover | ms/帧 |",
          "|---|---|---|---|---|"]
    for label, r in rows:
        md.append(f"| {label} | {r['err']/1e3:.1f} kHz | {r['per']:.1%} | "
                  f"{r['nm']}/{r['nd']} | {r['per_frame_ms']:.1f} |")
    md += ["", "## 解读", "",
           "· `pattern` 的 CFO 误差 = 模式库给出的旋性与真值之差（匹配命中时即模式参数的精度）。",
           "· `match/discover` **只在 pattern 场景有意义**；discover 次数应约等于设备数（冷启动一次）。",
           "· 单帧耗时差 = 盲估 vs 匹配的代价差；随帧数增长，模式库的摊薄优势会放大。"]
    (OUT / "report.md").write_text("\n".join(md) + "\n")
    print(f"\n[report] {OUT/'report.md'}")


if __name__ == "__main__":
    main()
