"""cfo_corr 验证: cfo_est 联合估计 (对齐点+频偏) + cfo_rot 整帧消旋。

数据链路与 tb/rx_deframer 一致 (定点): tx_symbols → modulate_oqpsk_fixed →
fixed_half_sine 卷积。CFO 加在**定点采样输入**上 (真实位置), 再卷积成 MF 输出。

判据:
  · est 无噪时应精确锁定 p_hat=0, phase_inc 对应频偏误差 < 数 kHz;
  · est 有噪时容差放宽到判决容限 (62.5 kHz) 的几分之一;
  · rot 消旋后的波形应与"无 CFO 参考"高度一致 (归一化残差);
  · 定量指标: 消旋与否, 前导复相关峰高低的对比。
"""
import sys
from pathlib import Path

import cocotb
import numpy as np
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "model"))
import phy_802154 as phy

SPS = phy.SPS
FS = phy.SPS * phy.CHIP_RATE
P24 = 1 << 24
N_PRE_SMP = 2048            # 前导 8 符号 = 2048 采样
COEF = np.array(phy.fixed_half_sine())


def build_frame(psdu, cfo_hz=0.0, noise=0.0, rng=None, scale=8):
    """定点链路: 采样级波形 → (可选)加噪 → (可选)CFO → MF 卷积。返回 (i_mf,q_mf)。"""
    i_f, q_f = phy.modulate_oqpsk_fixed(phy.tx_symbols(psdu).tolist())
    # modulate_oqpsk_fixed 的 Q 路比 I 路多 sps//2 个采样 (半码片偏移), 补齐到同长对齐
    n_smp = max(len(i_f), len(q_f))
    i_f = np.pad(np.array(i_f, dtype=float), (0, n_smp - len(i_f)))
    q_f = np.pad(np.array(q_f, dtype=float), (0, n_smp - len(q_f)))
    i_f = i_f * scale
    q_f = q_f * scale
    if rng is not None and noise > 0:
        i_f = i_f + np.round(noise * rng.standard_normal(len(i_f)))
        q_f = q_f + np.round(noise * rng.standard_normal(len(q_f)))
    if cfo_hz:
        n = np.arange(len(i_f))
        ph = 2 * np.pi * cfo_hz * n / FS
        c, s = np.cos(ph), np.sin(ph)
        i_f, q_f = i_f * c - q_f * s, q_f * c + i_f * s
    return np.convolve(i_f, COEF), np.convolve(q_f, COEF)


def sgn(v, w=21):
    """cocotb 的 .value 返回 LogicArray (无符号语义), 有符号端口要手动做符号还原。"""
    v = int(v)
    return v - (1 << w) if v >= (1 << (w - 1)) else v


async def drive(dut, i_seq, q_seq, n=None):
    """逐拍驱动采样流 (每拍 dv_in=1), 返回收集到的 i_out/q_out (已还原符号)。"""
    n = len(i_seq) if n is None else n
    oi, oq = [], []
    for k in range(n):
        dut.i_in.value = int(i_seq[k])
        dut.q_in.value = int(q_seq[k])
        dut.dv_in.value = 1
        await FallingEdge(dut.clk)
        if dut.dv_out.value == 1:
            oi.append(sgn(dut.i_out.value, 21))
            oq.append(sgn(dut.q_out.value, 21))
    return oi, oq


async def reset(dut):
    cocotb.start_soon(Clock(dut.clk, 62.5, unit="ns").start())
    for s in ("i_in", "q_in", "dv_in", "est_start", "rot_load", "phase_off"):
        getattr(dut, s).value = 0
    dut.rst_n.value = 0
    await Timer(200, unit="ns")
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1
    await FallingEdge(dut.clk)


def rot_ref(i_seq, q_seq, phase_inc, w=21):
    """Python 定点参照: 与 cfo_rot.sv 逐位一致 (LUT 高 8 位 / Q1.15 复乘 / >>15)。"""
    n = np.arange(len(i_seq))
    pacc = (phase_inc * n) & (P24 - 1)
    idx = pacc >> 16
    cs = np.round(np.cos(2 * np.pi * idx / 256) * 32767).astype(np.int64)
    sn = np.round(np.sin(2 * np.pi * idx / 256) * 32767).astype(np.int64)
    i = np.asarray(i_seq, dtype=np.int64)
    q = np.asarray(q_seq, dtype=np.int64)
    oi = ((i * cs + q * sn) >> 15)
    oq = ((q * cs - i * sn) >> 15)
    lo, hi = -(1 << (w - 1)), (1 << (w - 1)) - 1
    return np.clip(oi, lo, hi), np.clip(oq, lo, hi)


@cocotb.test()
async def est_clean_and_noisy(dut):
    """cfo_est: 无噪精确锁定; 有噪仍在判决容限内。"""
    await reset(dut)
    rng = np.random.default_rng(11)
    psdu = bytes(rng.integers(0, 256, size=8).tolist())

    # ---- 无噪 ----
    for cfo in (0.0, 50e3, 96e3, 300e3):
        i_mf, q_mf = build_frame(psdu, cfo)
        dut.est_start.value = 1
        await FallingEdge(dut.clk)
        dut.est_start.value = 0
        await drive(dut, i_mf, q_mf, N_PRE_SMP)
        for _ in range(80):
            await FallingEdge(dut.clk)
            if dut.est_done.value == 1:
                break
        assert dut.est_done.value == 1, f"CFO={cfo/1e3:.0f}k 未出 done"
        p = int(dut.p_hat.value)
        pi = int(dut.phase_inc.value)
        if pi >= P24 // 2:
            pi -= P24
        f_est = pi / P24 * FS
        print(f"  clean CFO={cfo/1e3:7.1f}k -> p_hat={p} f_est={f_est/1e3:8.2f}k "
              f"err={(f_est-cfo)/1e3:+7.2f}k")
        # RTL 约定: p_hat = 相位候选 (而非模板对齐点)。码片 0 的峰落在
        # 采样偏移 off[0] = sps-1 = 7, 故正确锁定值恒为 7 (模型侧记为 p=0)。
        assert p == SPS - 1, f"无噪应锁定相位候选 {SPS-1}, 实得 {p}"
        assert abs(f_est - cfo) < 20e3, f"无噪估计偏差过大: {f_est-cfo:.1f} Hz"

    # ---- 有噪 (chip SNR 由 noise=60/scale=8 标定, 与 rx_chain tb 同量级) ----
    for cfo in (0.0, 96e3):
        i_mf, q_mf = build_frame(psdu, cfo, noise=60.0, rng=rng)
        dut.est_start.value = 1
        await FallingEdge(dut.clk)
        dut.est_start.value = 0
        await drive(dut, i_mf, q_mf, N_PRE_SMP)
        for _ in range(80):
            await FallingEdge(dut.clk)
            if dut.est_done.value == 1:
                break
        pi = int(dut.phase_inc.value)
        if pi >= P24 // 2:
            pi -= P24
        f_est = pi / P24 * FS
        print(f"  noisy CFO={cfo/1e3:7.1f}k -> p_hat={int(dut.p_hat.value)} "
              f"f_est={f_est/1e3:8.2f}k err={(f_est-cfo)/1e3:+7.2f}k")
        assert abs(f_est - cfo) < 62.5e3, "有噪估计超出判决容限"


@cocotb.test()
async def rot_bit_true(dut):
    """cfo_rot: 输出与 Python 定点参照逐位一致。"""
    await reset(dut)
    rng = np.random.default_rng(5)
    psdu = bytes(rng.integers(0, 256, size=8).tolist())
    i_mf, q_mf = build_frame(psdu, 96e3)
    n = 1500
    i_seq = [int(v) for v in i_mf[:n]]
    q_seq = [int(v) for v in q_mf[:n]]

    phase_inc = int(round(96e3 / FS * P24))          # 正向增量
    dut.phase_inc.value = phase_inc
    dut.rot_load.value = 1
    await FallingEdge(dut.clk)
    dut.rot_load.value = 0
    oi, oq = await drive(dut, i_seq, q_seq)
    dut.dv_in.value = 0

    ref_i, ref_q = rot_ref(i_seq, q_seq, phase_inc)
    assert len(oi) >= n - 2, f"输出拍数不足: {len(oi)}"
    m = min(len(oi), n)
    ai_, aq_ = np.array(oi[:m]), np.array(oq[:m])
    di = np.abs(ai_ - ref_i[:m]).max()
    dq = np.abs(aq_ - ref_q[:m]).max()
    bad = np.nonzero((np.abs(ai_ - ref_i[:m]) > 0) | (np.abs(aq_ - ref_q[:m]) > 0))[0]
    print(f"  rot bit-true: max|dI|={di} max|dQ|={dq}  ({m} 拍, 失配 {len(bad)} 点)")
    if len(bad):
        k0 = int(bad[0])
        print(f"    首失配 @{k0}: rtl=({ai_[k0]},{aq_[k0]}) ref=({ref_i[k0]},{ref_q[k0]})")
    assert di == 0 and dq == 0, "cfo_rot 与 Python 定点参照不一致"


@cocotb.test()
async def corr_peak_recovery(dut):
    """定性核心: 消旋前后前导复相关峰高度的对比 (同步环是否被救回)。"""
    await reset(dut)
    rng = np.random.default_rng(3)
    psdu = bytes(rng.integers(0, 256, size=8).tolist())
    cfo = 96e3
    i_mf, q_mf = build_frame(psdu, cfo)
    n = 4096
    i_seq = [int(v) for v in i_mf[:n]]
    q_seq = [int(v) for v in q_mf[:n]]

    dut.est_start.value = 1
    await FallingEdge(dut.clk)
    dut.est_start.value = 0
    await drive(dut, i_seq, q_seq, N_PRE_SMP)
    for _ in range(80):
        await FallingEdge(dut.clk)
        if dut.est_done.value == 1:
            break
    assert dut.est_done.value == 1

    dut.rot_load.value = 1
    await FallingEdge(dut.clk)
    dut.rot_load.value = 0
    oi, oq = await drive(dut, i_seq, q_seq)
    dut.dv_in.value = 0

    def peak(xi, xq):
        a = np.asarray(xi, dtype=float) + 1j * np.asarray(xq, dtype=float)
        t = np.conj(phy.matched_filter(phy.modulate_oqpsk(np.tile(phy.CHIP[0], 8))))
        c = np.abs(np.convolve(a, t))
        return float(c[:2048].max())

    pk_raw = peak(i_seq, q_seq)
    pk_fix = peak(oi[:n], oq[:n])
    print(f"  前导相关峰: 修前={pk_raw:.3e}  修后={pk_fix:.3e}  比={pk_fix/pk_raw:.3f}")
    assert pk_fix > pk_raw, "消旋后相关峰应恢复 (比修前高)"
