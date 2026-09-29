#!/usr/bin/env python
"""run_mc_model.py —— 蒙特卡洛对照: 浮点链路模型在同一 SNR 网格上的 BER

与 RTL (tb/rx_chain_e2e/run_mc.py) 严格同口径:
  · 同一 SNR 定义: 匹配滤波输出端"码片 SNR" (dB) —— 模型侧由 channels.awgn
    的脉冲能量标定 (Σh_float²), RTL 侧由定点脉冲能量 (Σh_fixed²=16436) 标定,
    各自在自身系数域内等价。
  · 同一 PSDU 长度 / 帧数 / 随机载荷
  · 符号级统计: SHR 之后的全部符号 (PHR+PSDU+FCS) 与发送符号 XOR popcount;
    同步失败帧按整帧计错 (保守口径, 同 measure.frame_bit_errors)
链路: chains.link([awgn(snr)], full=True, sync='honest') —— 含白化/FCS 完整组帧。

用法: python model/experiments/run_mc_model.py "-6,-4,-2,0,2" 2000
输出: model/out/rtl_ber/data/model_points.json
"""
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import chains          # noqa: E402
import phy_802154 as phy  # noqa: E402

SHR = 10
POP = np.array([bin(i).count("1") for i in range(16)], dtype=np.int64)


def run_point(snr, n_frames, psdu_len, rng):
    chain = chains.link([chains.awgn(snr, rng)], full=True, sync="honest")
    bit_err = bit_tot = 0
    n_sync_fail = 0
    for _ in range(n_frames):
        psdu = bytes(rng.integers(0, 256, size=psdu_len).tolist())
        sig = chain.run(payload=psdu)
        tx = np.asarray(sig.symbols[SHR:], dtype=np.int64)
        K = len(tx)
        bit_tot += 4 * K
        if sig.meta.get("sync_fail"):
            bit_err += 4 * K
            n_sync_fail += 1
            continue
        rx = np.asarray(sig.rx_symbols[SHR:], dtype=np.int64)
        m = min(len(rx), K)
        if m:
            bit_err += int(POP[(rx[:m] & 0xF) ^ (tx[:m] & 0xF)].sum())
        bit_err += 4 * (K - m)
    return dict(snr_db=snr, n_frames=n_frames, n_bit=int(bit_tot),
                n_bit_err=int(bit_err), ber=bit_err / max(1, bit_tot),
                n_sync_fail=n_sync_fail)


def main():
    snrs = [float(x) for x in sys.argv[1].split(",")] if len(sys.argv) > 1 \
        else [-6, -4, -2, 0, 2]
    n_frames = int(sys.argv[2]) if len(sys.argv) > 2 else 2000
    psdu_len = int(sys.argv[3]) if len(sys.argv) > 3 else 20
    rng = np.random.default_rng(1)
    rows = []
    for snr in snrs:
        r = run_point(snr, n_frames, psdu_len, rng)
        rows.append(r)
        print(f"model snr={snr:+g} dB  BER={r['ber']:.3e} "
              f"({r['n_bit_err']}/{r['n_bit']})  sync_fail={r['n_sync_fail']}")
    out = ROOT / "out" / "rtl_ber" / "data" / "model_points.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dict(psdu_len=psdu_len, frames=n_frames, points=rows),
                              indent=2))
    print(f"→ {out}")


if __name__ == "__main__":
    main()
