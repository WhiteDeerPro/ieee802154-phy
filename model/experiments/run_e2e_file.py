# -*- coding: utf-8 -*-
"""
run_e2e_file.py —— 完整流程演练: 文档 → 比特流 → 波形 → 损伤 → 还原
====================================================================
把 README.md 二进制化, 经 ref 全链 (TX: 分帧/组帧/白化/CRC/扩频/OQPSK;
信道: AWGN+CFO+DC/IQ; RX: MF/帧检测/CFO消旋/DC-IQ校正/解扩/解帧) 还原,
输出带数据快照的流程图 + 终端逐级报告。
运行: python model/experiments/run_e2e_file.py  →  out/vis/vis_e2e_file_flow.png

本脚本是**多帧流水线**而非单帧链路, 因此链路库的用法与单帧实验 (见 run_ber.py) 略有不同:

  TX     逐帧过 ``chains.tx_stages()`` 生成波形, 帧间插 GAP 静默后拼成整条流;
  信道   损伤段作用于**整条流** (一次 AWGN → 一次 CFO → 一次 DC/IQ, 与原脚本同序);
  RX     逐帧切片 (帧长已知, 各帧不同) → 每帧复用同一条接收段链恢复该帧。

接收段用 ``chains.rx_stages()`` 配方; 唯一就地补充的是 CFO 阶段 (见
``cfo_derotate_periodogram``): chains 的预设不含「周期图谱峰粗估 + 相位差分精估」这条
组合, 为保持与本脚本迁移前逐比特一致, 按 ``baseband.link`` 的阶段协议就地实现。

比特误码统一用 ``measure.frame_bit_errors`` 口径 (同步失败帧整帧计错)。
"""
import hashlib
import sys
from pathlib import Path

# 本脚本位于 model/experiments/ —— 库模块 (chains / measure / phy_802154 / visualize)
# 在上一级的 model/, 而 Python 只自动把脚本自身目录加入 sys.path, 故显式引导。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

import _common
import chains
import measure
import phy_802154 as phy
from baseband import impairments as imp
from baseband.link import Chain, Signal

MODEL = Path(__file__).resolve().parent.parent   # model/ (脚本在 model/experiments/)
ROOT = MODEL.parent                               # 仓库根 (读 README.md)
plt = _common.init()                       # Agg 后端 + 中文字体
OUT = _common.out_dir("vis")               # model/out/vis/（已创建）
SPS = phy.SPS

# ---- 信道参数 ----
CFO_HZ = 50e3
SNR_DB = 8.0
DC = (0.20, 0.15)
IQ = (1.0, 5.0)
FRAME_LEN = 120          # 每帧 PSDU 字节 (≤127)
GAP = 400                # 帧间静默采样

rng = np.random.default_rng(777)


def cfo_derotate_periodogram(sig):
    """CFO 估计 + 消旋 (接收段首阶段, 须在匹配滤波之前)。

    估计顺序与原脚本一致: ``estimate_cfo_periodogram`` 谱峰粗估 (无模糊) →
    ``estimate_cfo_fine`` 前导相位差分精估。估计值写入 ``meta['cfo_est']``。
    """
    f_coarse = phy.estimate_cfo_periodogram(sig.rx)
    f, _ = phy.estimate_cfo_fine(sig.rx, f_coarse)
    sig.meta["cfo_est"] = float(f)
    sig.rx = phy.correct_cfo(sig.rx, f)


# ================= TX: 逐帧组帧 → 拼成连续波形 =================
file_bytes = (ROOT / "README.md").read_bytes()
print(f"[1] file to binary: README.md = {len(file_bytes)} B, "
      f"sha256={hashlib.sha256(file_bytes).hexdigest()[:16]}...")

n_frames = (len(file_bytes) + FRAME_LEN - 1) // FRAME_LEN
frames = [file_bytes[i * FRAME_LEN:(i + 1) * FRAME_LEN] for i in range(n_frames)]

tx_chain = Chain(name="tx").then_tx(*chains.tx_stages())   # 组帧+白化+CRC → 扩频 → 成形

stream = []          # 连续波形 (含 GAP)
frame_syms = []      # 每帧发送符号 (接收段按已知帧长建段)
for i, psdu in enumerate(frames):
    sig = tx_chain.run(payload=psdu)      # 空信道/空接收段 → 只走发送段
    if i:
        stream += [0j] * GAP
    stream += list(sig.tx)
    frame_syms.append(sig.symbols)
stream = np.array(stream)
frame_len_sym = [len(s) for s in frame_syms]
print(f"[2-4] framing/whitening/spreading/modulation: {n_frames} frames, "
      f"frame PSDU≤{FRAME_LEN} B, waveform {len(stream)} samples ≈ {len(stream)/16e6*1e3:.0f} ms")

# ================= 信道: 整条流依次过损伤 =================
# chains.awgn 与旧脚本的 phy.add_awgn 同标定 (pulse_energy = Σh² = sps/2, 同一 rng)。
channel = Chain(name="channel").then_channel(
    chains.awgn(SNR_DB, rng),
    imp.cfo_stage(CFO_HZ),
    imp.dc_stage(*DC),
    imp.iq_stage(*IQ),
)
y = channel.run(Signal(tx=stream)).rx
print(f"[5] channel impairments: AWGN {SNR_DB} dB + CFO {CFO_HZ/1e3:.0f} kHz + "
      f"DC{DC} + IQ {IQ[0]}dB/{IQ[1]}°")

# ================= RX: 逐帧切片 → 复用同一条接收段 =================
# [6] 帧检测 + CFO 粗/精估与消旋, 之后 MF → 同步 → 采样 → DC/IQ 校正 → 解扩 → [10] 解帧
rx_chain = Chain(name="rx").then_rx(
    cfo_derotate_periodogram,
    *chains.rx_stages(sync="honest", dc_iq=True, deframe=True),
)

rx = []
sigs = []
off = 0
for i, psdu in enumerate(frames):
    n_sym = frame_len_sym[i]
    seg = y[off:off + n_sym * 32 * SPS + 4]      # 帧长已知 → 逐帧独立建接收段
    off += len(seg) + GAP
    sig = rx_chain.run(Signal(payload=psdu, rx=seg, symbols=frame_syms[i]))
    sigs.append(sig)
    rx.append((sig.rx_payload if sig.meta["fcs_ok"] else b"", sig.meta["fcs_ok"],
               sig.meta["phr_len"], sig.meta["cfo_est"]))

rx_bytes = b"".join(o for o, ok, _, _ in rx if ok)
n_ok = sum(1 for _, ok, _, _ in rx if ok)
est_all = [s.meta["cfo_est"] for s in sigs]
syms_all = [(s.rx_symbols, s.symbols) for s in sigs]
bit_err = bit_tot = 0
for s in sigs:
    e, t = measure.frame_bit_errors(s)      # 同步失败帧整帧计错
    bit_err += e
    bit_tot += t
ber = bit_err / bit_tot if bit_tot else 0.0
print(f"[6-10] RX: {n_ok}/{n_frames} frames pass CRC, symbol BER={ber:.2e}, "
      f"mean CFO estimate {np.mean(est_all)/1e3:.2f} kHz")
print(f"checksum: recovered {len(rx_bytes)} B, "
      f"sha256={hashlib.sha256(rx_bytes).hexdigest()[:16]}..., byte-identical = {rx_bytes == file_bytes}")

# ================= 流程图 =================
fig, axs = plt.subplots(2, 5, figsize=(21, 8.5),
                        gridspec_kw={"height_ratios": [1, 1], "wspace": 0.32,
                                     "hspace": 0.42})

# TX 行
axs[0, 0].text(0.05, 0.62, f"{len(file_bytes)} B file", fontsize=12, weight="bold")
axs[0, 0].text(0.05, 0.42, file_bytes[:16].hex(" "), fontsize=8, family="monospace")
axs[0, 0].text(0.05, 0.22, f"...total {n_frames} frames", fontsize=9)
axs[0, 0].axis("off"); axs[0, 0].set_title("① Binary conversion")

axs[0, 1].text(0.05, 0.62, "framing + packing + whiten + CRC", fontsize=11, weight="bold")
axs[0, 1].text(0.05, 0.42, f"frame 0: PHR={FRAME_LEN} symbols={frame_len_sym[0]}",
               fontsize=9)
axs[0, 1].text(0.05, 0.24, f"whiten first byte {phy.pn9_bytes(bytes([FRAME_LEN]))[0]:02x}",
               fontsize=8, family="monospace")
axs[0, 1].axis("off"); axs[0, 1].set_title("② Frame packing (tx_symbols)")

chips0 = phy.symbols_to_chips(phy.tx_symbols(frames[0]))[:64]
axs[0, 2].plot(np.arange(64), chips0, "s-", ms=5, lw=0.8, color="tab:blue")
axs[0, 2].set_ylim(-1.4, 1.4)
axs[0, 2].set_title("③ Spreading (first 64 chips of frame 0)")
axs[0, 2].set_xlabel("Chip"); axs[0, 2].set_yticks([-1, 0, 1])

w0 = phy.modulate_oqpsk(phy.symbols_to_chips(phy.tx_symbols(frames[0])))
t = np.arange(96) / 16
axs[0, 3].plot(t, w0.real[:96], lw=0.8, label="I")
axs[0, 3].plot(t, w0.imag[:96], lw=0.8, label="Q")
axs[0, 3].set_title("④ OQPSK modulation (first 6 chip waveform)")
axs[0, 3].set_xlabel("Time (µs)"); axs[0, 3].legend(fontsize=8)
axs[0, 3].set_ylim(-1.4, 1.4)

axs[0, 4].text(0.05, 0.7, f"AWGN {SNR_DB} dB", fontsize=11)
axs[0, 4].text(0.05, 0.52, f"CFO {CFO_HZ/1e3:.0f} kHz", fontsize=11)
axs[0, 4].text(0.05, 0.34, f"DC {DC}", fontsize=11)
axs[0, 4].text(0.05, 0.16, f"IQ {IQ[0]}dB/{IQ[1]}°", fontsize=11)
axs[0, 4].axis("off"); axs[0, 4].set_title("⑤ Channel impairments")

# RX 行
axs[1, 0].text(0.05, 0.66, "MF + frame detection", fontsize=11, weight="bold")
axs[1, 0].text(0.05, 0.42, f"{n_frames} frames all detected", fontsize=9)
axs[1, 0].text(0.05, 0.24, "block-wise autocorrelation positioning", fontsize=9)
axs[1, 0].axis("off"); axs[1, 0].set_title("⑥ Detection (preamble)")

axs[1, 1].text(0.05, 0.66, "CFO estimation (differential)", fontsize=11, weight="bold")
axs[1, 1].text(0.05, 0.44, f"f_est mean {np.mean(est_all)/1e3:.2f} kHz", fontsize=10)
axs[1, 1].text(0.05, 0.26, f"residual {np.mean(np.abs(np.array(est_all)-CFO_HZ))/1e3:.2f} kHz",
               fontsize=9)
axs[1, 1].axis("off"); axs[1, 1].set_title("⑦ CFO estimation")

seg0 = y[: frame_len_sym[0] * 32 * SPS + 4]
f0 = est_all[0]
segc = phy.correct_cfo(seg0, f0)
mfc = phy.matched_filter(segc)
pc, _, _ = phy.correlate_preamble(mfc, return_corr=True)
mm = np.arange(320, 320 + 96)
ii = pc + mm * SPS + np.where(mm % 2, SPS // 2, 0) + (SPS - 1)
zz = mfc[ii] / 4.0
axs[1, 2].scatter(zz.real, zz.imag, s=10, c="tab:green", alpha=0.8)
axs[1, 2].set_xlim(-1.6, 1.6); axs[1, 2].set_ylim(-1.6, 1.6)
axs[1, 2].set_aspect("equal")
axs[1, 2].set_title("⑧ Constellation after derotation + DC/IQ correction")
axs[1, 2].set_xlabel("I"); axs[1, 2].set_ylabel("Q")

got0, want0 = syms_all[0]
pwr0 = None
axs[1, 3].plot(np.arange(20), got0[:20], "o-", ms=5, label="despread")
axs[1, 3].plot(np.arange(20), want0[:20], "x--", ms=6, lw=0.8, label="transmitted")
axs[1, 3].set_ylim(-0.5, 15.5)
axs[1, 3].set_title("⑨ Despread (16-way correlation argmax, frame 0 first 20 symbols)")
axs[1, 3].set_xlabel("Symbol"); axs[1, 3].legend(fontsize=8)

axs[1, 4].text(0.05, 0.72, f"dewhiten+PHR+CRC: {n_ok}/{n_frames} frames pass", fontsize=11,
               weight="bold")
axs[1, 4].text(0.05, 0.52, f"recovered {len(rx_bytes)} B", fontsize=10)
axs[1, 4].text(0.05, 0.34, "byte-identical = "
               + ("✓" if rx_bytes == file_bytes else "×"), fontsize=12)
axs[1, 4].text(0.05, 0.16, f"BER={ber:.2e}", fontsize=9)
axs[1, 4].axis("off"); axs[1, 4].set_title("⑩ Deframe (rx_deframe_symbols)")

fig.suptitle("Full flow demo: README.md → waveform → impairments → recovery (ref golden model full chain)",
             fontsize=15)
out = OUT / "vis_e2e_file_flow.png"
fig.savefig(out, dpi=130)
print(f"saved: {out}")
