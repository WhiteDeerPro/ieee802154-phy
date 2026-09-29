#!/usr/bin/env python
"""mc_gen.py —— 蒙特卡洛激励生成 (多帧定点波形 + AWGN + ADC 量化 → mem.bin)

口径 (与链路库 awgn 及既有 tb 一致):
  采样级噪声 σ = SCALE · √(Σh²_fixed / 10^(snr/10)),  h = fixed_half_sine(Q2.6)
  ⇒ 匹配滤波输出端"码片 SNR" = 10·log10((SCALE·Σh²)² / (σ²·Σh²)) = snr (dB)
  必须用**定点**脉冲能量 Σh²=16436, 不是浮点半正弦的 Σh²=4 —— 差 64 倍
  (同款注释见 tb/cfo_corr/test_rtl_lab.py: 用错会让噪声小 64 倍)。

每帧采样结构: [GAP 纯噪] [波形+噪] [TAIL 纯噪], 帧尾 TAIL 保证最后一符号
被完整推出 (despreader 符号完成需 32 码片 = 256 采样)。

输出 (out_dir/):
  mem.bin     每采样 32bit LE: [11:0]=I, [23:12]=Q (signed 12bit, 已 ADC 量化)
  frames.npz  frame_start[N] 每帧波形起始采样索引; tx_syms[N,K] 发送符号[10:]
              (K = 2+2·psdu_len, 即 PHR+PSDU, 与 RTL 解出的符号流同口径)
  meta.json   参数与削波率等

用法: python tb/rx_chain_e2e/mc_gen.py --snr 0 --frames 20 --out-dir /tmp/mc_p0
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "model"))
import phy_802154 as phy

SPS = phy.SPS
SHR_SYMBOLS = 10
ADC_W = 12
ADC_MAX = (1 << (ADC_W - 1)) - 1        # 2047
ADC_MIN = -(1 << (ADC_W - 1))           # -2048
ES_H_FIXED = float(np.sum(np.array(phy.fixed_half_sine(), dtype=float) ** 2))  # 16436


def noise_sigma(scale: float, snr_db: float) -> float:
    """给定码片 SNR(dB) 的采样级噪声 σ (定点脉冲能量口径)。"""
    return scale * float(np.sqrt(ES_H_FIXED / 10 ** (snr_db / 10.0)))


def quantize(v: np.ndarray) -> tuple:
    """ADC 12bit: 四舍五入 + 饱和。返回 (int32 量化值, 削波采样数)。"""
    r = np.round(v)
    n_clip = int(np.count_nonzero((r > ADC_MAX) | (r < ADC_MIN)))
    return np.clip(r, ADC_MIN, ADC_MAX).astype(np.int32), n_clip


def mem_checksum(packed: np.ndarray) -> int:
    """头尾各 1024 个元素的 XOR —— 供 RTL 侧 +CKS 校验激励完整性。"""
    n = len(packed)
    if n == 0:
        return 0
    head = packed[:min(1024, n)]
    tail = packed[max(0, n - 1024):]
    return int(np.bitwise_xor.reduce(np.concatenate([head, tail])))


def gen_point(snr_db, n_frames, psdu_len, scale, seed, gap, tail, out_dir,
              gap_jitter=16, cfo_hz=0.0, iq_gain_db=0.0, iq_phase_deg=0.0):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    sigma = noise_sigma(scale, snr_db)

    i_parts, q_parts = [], []
    frame_start = np.empty(n_frames, dtype=np.int64)
    # K = PHR(1B) + PSDU + FCS(2B) 的符号数, 即 SHR 之后的全部符号 (RTL 解出的同口径)
    K = len(phy.tx_symbols(bytes(psdu_len))) - SHR_SYMBOLS
    txs = np.empty((n_frames, K), dtype=np.int8)
    psdus = np.empty((n_frames, psdu_len), dtype=np.uint8)
    n_clip = n_total = 0
    idx = 0

    for f in range(n_frames):
        # 帧前 GAP (纯噪声); 每帧加随机抖动 → 帧到达相位 (mod 16) 随机化,
        # 覆盖全部 16 个采样相位 (修复后的同步器应全部可收)
        g_len = gap + int(rng.integers(0, gap_jitter))
        i_parts.append(rng.standard_normal(g_len) * sigma)
        q_parts.append(rng.standard_normal(g_len) * sigma)
        idx += g_len

        # 帧波形 (定点调制 ×SCALE + AWGN)
        psdu = bytes(rng.integers(0, 256, size=psdu_len).tolist())
        syms = phy.tx_symbols(psdu)
        i_f, q_f = phy.modulate_oqpsk_fixed(syms.tolist())
        n = max(len(i_f), len(q_f))       # q 比 i 多 sps//2 (半码片偏移尾巴)
        i_f = np.asarray(i_f, dtype=float)
        q_f = np.asarray(q_f, dtype=float)
        i_f = np.pad(i_f * scale, (0, n - len(i_f)))
        q_f = np.pad(q_f * scale, (0, n - len(q_f)))
        i_f = i_f + rng.standard_normal(n) * sigma
        q_f = q_f + rng.standard_normal(n) * sigma

        frame_start[f] = idx
        txs[f] = np.asarray(syms[SHR_SYMBOLS:], dtype=np.int8)
        i_parts.append(i_f)
        q_parts.append(q_f)
        idx += n
        psdus[f] = np.frombuffer(psdu, dtype=np.uint8)

        # 帧后 TAIL (纯噪声, 保证帧尾符号流排空)
        i_parts.append(rng.standard_normal(tail) * sigma)
        q_parts.append(rng.standard_normal(tail) * sigma)
        idx += tail

    i_cat = np.concatenate(i_parts)
    q_cat = np.concatenate(q_parts)

    # CFO: 模拟域复旋转 (量化前注入, 与 test_cfo_sweep 同位置);
    # 相位以整条流起点为 0, 帧间连续 —— 与真实接收机看到的连续频偏一致
    if cfo_hz:
        n_idx = np.arange(len(i_cat))
        ph = 2 * np.pi * cfo_hz * n_idx / (SPS * phy.CHIP_RATE)
        cph, sph = np.cos(ph), np.sin(ph)
        i_cat, q_cat = (i_cat * cph - q_cat * sph,
                        i_cat * sph + q_cat * cph)

    # I/Q 失衡 (模拟域: 天线 → CFQ/IQ 失衡 → ADC; 与 baseband.impairments.add_iq_imbalance 同式)
    if iq_gain_db or iq_phase_deg:
        _g = 10 ** (iq_gain_db / 20)
        _p = np.radians(iq_phase_deg)
        i_cat, q_cat = i_cat * _g, i_cat * np.sin(_p) + q_cat * np.cos(_p)

    # ADC 12bit 量化 (统一在 CFO 后: 真实位置是天线→CFO→ADC)
    i_cat, n_clip_i = quantize(i_cat)
    q_cat, n_clip_q = quantize(q_cat)
    n_clip = n_clip_i + n_clip_q
    n_total = 2 * len(i_cat)
    n_smp = len(i_cat)

    # 打包 32bit: 低 12 = I, 高 12 = Q
    # 注意字节序: VCS $fread 对 reg [31:0] 数组按**大端**组装每个元素
    # (实测: numpy 小端写入的值 X 被读成 byteswap(X), 见 mc_tb 的 +CKS 校验),
    # 所以此处必须按 '>u4' 写入, 读侧才得到预期值。
    packed = ((i_cat & 0xFFF).astype(np.uint32)
              | (((q_cat & 0xFFF).astype(np.uint32)) << 12))
    packed.astype('>u4').tofile(out_dir / "mem.bin")
    mem_cks = mem_checksum(packed)

    np.savez_compressed(out_dir / "frames.npz",
                        frame_start=frame_start, tx_syms=txs, psdu=psdus)
    # CFO 链触发参考: 帧起点采样索引 (每行一个, 供 mc_cfo_tb.sv 在
    # 前导起点处发 est_start / rot_load; "上帝视角"触发仅用于验证消旋架构成效)
    np.savetxt(out_dir / "frames.txt", frame_start, fmt="%x")

    meta = dict(snr_db=snr_db, scale=scale, sigma=sigma, n_frames=n_frames,
                psdu_len=psdu_len, seed=seed, gap=gap, tail=tail,
                gap_jitter=gap_jitter, cfo_hz=cfo_hz,
                iq_gain_db=iq_gain_db, iq_phase_deg=iq_phase_deg,
                n_smp=int(n_smp), clip_frac=n_clip / max(1, n_total),
                es_h_fixed=ES_H_FIXED, tx_syms_per_frame=int(txs.shape[1]),
                mem_cks=f"{mem_cks:08x}")
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))
    return meta


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snr", type=float, required=True, help="码片 SNR (dB), MF 输出端")
    ap.add_argument("--frames", type=int, default=2000)
    ap.add_argument("--psdu-len", type=int, default=20)
    ap.add_argument("--scale", type=float, default=8.0)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--gap", type=int, default=400)
    ap.add_argument("--tail", type=int, default=600)
    ap.add_argument("--gap-jitter", type=int, default=16)
    ap.add_argument("--cfo-hz", type=float, default=0.0)
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    meta = gen_point(a.snr, a.frames, a.psdu_len, a.scale, a.seed,
                     a.gap, a.tail, a.out_dir, a.gap_jitter, a.cfo_hz)
    print(f"[mc_gen] snr={a.snr} dB frames={a.frames} n_smp={meta['n_smp']} "
          f"({meta['n_smp']*4/1048576:.1f} MB) clip={meta['clip_frac']*100:.3f}%")


if __name__ == "__main__":
    main()
