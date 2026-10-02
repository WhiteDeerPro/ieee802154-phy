# -*- coding: utf-8 -*-
"""run_multipath_eye.py —— 多径前/后：眼图 · 星座 · BER（ref 层快速对照）。

三列对照:
  ① 无多径（纯 AWGN）        ② 多径 [1, 0.5] @ 0.5 码片（深衰落陷波）
  ③ 多径 + MMSE 均衡（前导 LS + 频域均衡; 均衡后相干解扩）

观测: 眼图 = MF 后采样波形(sig.rx, 8 采样/码片折叠); 星座 = 解扩软值(sig.rx_soft);
      BER = measure.frame_bit_errors（多帧平均）。
运行: python model/experiments/run_multipath_eye.py
产出: model/out/multipath_eye/eye_mp.png + report.md
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import _common
import chains
import measure
import visualize
from baseband import impairments as imp

plt = _common.init()
OUT = _common.out_dir("multipath_eye")

SPS = 8
SNR_DB = -1.0           # 码片 SNR（run_multipath_eq 同工作点：多径破坏显现）
N_FRAMES = 20
# 深陷波档（快速扫描选点: [1,-.9]@0.25chip → BER 0.31, 陷波落在码片能量带;
#   [1,.5]@0.5chip 陷波在带外、硬扛无损, 仅作参考不再入图）
MP = dict(gains=[1.0, -0.9], delays=[0.0, 0.25])

CASES = [
    ("clean", "① 无多径（纯 AWGN）"),
    ("mp",    "② 多径 [1, -.9] @0.25 chip（深陷波）"),
    ("eq",    "③ 多径 + MMSE 均衡"),
]


def build(mode, rng, snr=SNR_DB):
    seg = [] if mode == "clean" else [imp.multipath_stage(MP["gains"], MP["delays"])]
    seg = seg + [chains.awgn(snr, rng)]
    if mode == "eq":
        return chains.link(seg, full=False, sync="genie", n_taps=6,
                           noise_var=chains.eq_noise_var(snr), coherent=True)
    return chains.link(seg, full=False, sync="genie")


def run_case(mode):
    rng = np.random.default_rng(7)
    chain = build(mode, rng)
    ber_e = ber_t = 0
    eye = soft = None
    for i in range(N_FRAMES):
        psdu = bytes(rng.integers(0, 256, size=20).tolist())
        sig = chain.run(payload=psdu)
        e, t = measure.frame_bit_errors(sig)
        ber_e += e
        ber_t += t
        if i == 3:                      # 取第 4 帧做展示
            eye = np.asarray(sig.rx)
            soft = np.asarray(sig.rx_soft)
    return ber_e / max(ber_t, 1), eye, soft


def main():
    fig, axes = plt.subplots(2, len(CASES), figsize=(4.1 * len(CASES), 6.4))
    lines = ["# 多径前/后：眼图 · 星座 · BER（chip SNR=%.0f dB, %d 帧）" % (SNR_DB, N_FRAMES), ""]
    for col, (mode, name) in enumerate(CASES):
        ber, eye, soft = run_case(mode)
        E = measure.eye_data(eye, SPS, n_traces=160)
        ax = axes[0][col]
        for row in E:
            ax.plot(np.arange(SPS), row, alpha=0.15, lw=0.8, color="tab:blue")
        ax.set_title(f"{name}\nBER = {ber:.3e}", fontsize=9.5)
        ax.set_xlabel("码片内采样")
        ax.grid(alpha=0.3)

        ax2 = axes[1][col]
        try:
            visualize.plot_constellation(np.asarray(soft).ravel(), ax=ax2)
        except Exception:
            ax2.scatter(np.real(soft), np.imag(soft), s=8, alpha=0.6)
        ax2.set_title("解扩软值星座", fontsize=9)
        lines.append(f"- {name}: **BER = {ber:.3e}**（眼图见 eye_mp.png）")
        print(lines[-1])
    fig.suptitle("多径前/后对照（genie 同步, 通道估计仅均衡链）", fontsize=11)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(OUT / "eye_mp.png", dpi=110)
    (OUT / "report.md").write_text("\n".join(lines) + "\n视觉对照: `eye_mp.png`\n")
    print("→", OUT / "eye_mp.png")


if __name__ == "__main__":
    main()
