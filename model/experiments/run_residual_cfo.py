# -*- coding: utf-8 -*-
"""run_residual_cfo.py —— 残余 CFO 容限细扫（决定分辨单元宽度）

场景：消旋之后仍残留的频偏 δ（未修正），链路直接跑。
      （直接注入 δ 与"先消一个大 CFO 再残留 δ"等价 —— 消旋是线性相移。）

测：δ ∈ [0, 100] kHz 下的 BER 与同步失败率。
用途：**分辨单元（链路状态）的覆盖半宽 = 可容忍的 δ_max** —— 这决定
"两个状态的中心至少要隔多远"以及"要不要重叠"（docs/16 多假设一节）。

运行: python model/experiments/run_residual_cfo.py
输出: model/out/cfo_residual/report.md
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

import _common
import chains
import measure
from baseband import impairments as imp

PSDU = bytes(range(20))
N_FRAMES = 200
SNR_DB = 18.0                      # 实测工作点（README 链路预算: −85 dBm → 可用 SNR ≈ 18 dB）
DELTAS_KHZ = [0, 2, 4, 6, 8, 10, 12, 16, 20, 30, 40, 50, 75, 100]


def main():
    rng = np.random.default_rng(2026)
    results = {}
    for sync_mode in ("genie", "honest"):
        rows = []
        for d in DELTAS_KHZ:
            n_err = n_bit = n_fail = 0
            for _ in range(N_FRAMES):
                sig = chains.simulate(
                    PSDU,
                    [imp.cfo_stage(d * 1e3), chains.awgn(SNR_DB, rng)],
                    sync=sync_mode, deframe=True)
                e, n = measure.frame_bit_errors(sig)
                n_err += e
                n_bit += n
                n_fail += int(bool(sig.meta.get("sync_fail")))
            ber = n_err / max(n_bit, 1)
            rows.append((d, ber, n_fail / N_FRAMES))
            print(f"  [{sync_mode:6s}] δ={d:3d} kHz   BER={ber:.2e}   sync_fail={n_fail/N_FRAMES:.0%}")
        results[sync_mode] = rows

    lines = ["# 残余 CFO 容限细扫（决定分辨单元宽度）\n",
             f"- 每点 {N_FRAMES} 帧，SNR {SNR_DB} dB，无消旋（直接注入残差 δ）",
             "- **两条曲线分离两个环节**：`genie` = 已知定时（测解调容限）；"
             "`honest` = 全搜索同步（测同步器容限）\n"]
    for mode, rows in results.items():
        base = rows[0][1]
        lines.append(f"## {mode} 同步\n")
        lines.append("| δ (kHz) | BER | 同步失败率 | |")
        lines.append("|---|---|---|---|")
        for d, ber, sf in rows:
            tag = ""
            if d > 0 and ber <= max(base * 1.5, 1e-6):
                tag = "无损"
            elif ber > 0.1:
                tag = "崩溃"
            lines.append(f"| {d} | {ber:.2e} | {sf:.0%} | {tag} |")
        ok = [d for d, ber, _ in rows if ber <= max(base * 1.5, 1e-6)]
        lines.append(f"\n无损容限 ≈ **{max(ok)} kHz** → 单元全宽 ≈ {2*max(ok)} kHz\n")

    out = _common.out_dir("cfo_residual") / "report.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告: {out}")


if __name__ == "__main__":
    main()
