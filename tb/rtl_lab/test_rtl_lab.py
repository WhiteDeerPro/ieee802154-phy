# -*- coding: utf-8 -*-
"""
rtl_lab —— RTL 数据导出实验: 把 oqpsk_modulator 的 12bit 定点 I/Q 捕捉下来
=============================================================================

这一层只做三件事, 不做信号处理:
    1. 驱动 RTL, 连续采集 ``i_out`` / ``q_out`` (signed [11:0]) 与 ``sample_dv``;
    2. **用断言守住数据质量** —— 采集连续性、位宽范围、与黄金模型的 bit-true 一致;
    3. 导出到 ``model/out/rtl_lab/``, 交给模型侧 ``run_rtl_lab.py`` 做
       IQ 合成 → DAC/ADC → 信道 → 接收机 → 可视化。

为什么要分开: RTL 侧只该保证"数据对", 分析/画图是模型侧的事 —— 混在一起会
让仿真依赖 matplotlib, 也让断言与非断言代码纠缠不清。

定点约定: RTL 里脉冲系数是 Q2.6 (``fixed_half_sine(q=6)``, 1.0 = 64),
所以采样值的 1.0 也是 64 —— 导出时同时存原始整数与定标因子, 避免下游猜。
"""
import sys
from pathlib import Path

import cocotb
import numpy as np
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer

MODEL = Path(__file__).resolve().parents[2] / "model"
sys.path.insert(0, str(MODEL))
import phy_802154 as phy                                       # noqa: E402

OUT = MODEL / "out" / "rtl_lab"
CLK_NS = 62.5
Q_SCALE = 64.0                      # Q2.6: 1.0 对应 64
IQ_BITS = 12
IQ_MAX = (1 << (IQ_BITS - 1)) - 1   # 2047
IQ_MIN = -(1 << (IQ_BITS - 1))      # -2048


def to_signed(v, bits=IQ_BITS):
    v = int(v)
    return v - (1 << bits) if v >= (1 << (bits - 1)) else v


@cocotb.test()
async def rtl_lab_capture(dut):
    """采集 I/Q 并断言数据质量, 导出供模型侧分析。"""
    cocotb.start_soon(Clock(dut.clk, CLK_NS, unit="ns").start())
    dut.ce_2m.value = 0
    dut.sym.value = 0
    dut.sym_valid.value = 0
    dut.rst_n.value = 0
    await Timer(200, unit="ns")
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1
    await FallingEdge(dut.clk)

    rng = np.random.default_rng(2026)
    # 用**完整 PPDU** (含 8 符号前导 + SFD): 下游要走同步/解扩, 没前导就锁不上。
    # 取 127B —— 802.15.4 的最大 PSDU, 既走最长帧, 也把 VCD 做到 MB 级别。
    psdu = bytes(rng.integers(0, 256, size=127).tolist())
    syms = phy.ppdu_symbols(psdu).tolist()
    i_exp, q_exp = phy.modulate_oqpsk_fixed(syms)
    n_i, n_q = len(i_exp), len(q_exp)          # Q 比 I 多 sps//2 拍 (半码片拖尾)
    n_sym = len(syms)                          # 驱动器必须发满整帧 (PPDU 长度的符号数)

    i_got, q_got, dv_got = [], [], []
    t0_i = None
    syms_sent = 0
    ce_cnt = 0

    async def monitor():
        t = 0
        while len(i_got) < n_i + 16 and t < n_sym * 256 + 8192:
            if t0_i is not None:
                i_got.append(to_signed(dut.i_out.value))
                q_got.append(to_signed(dut.q_out.value))
                dv_got.append(int(dut.sample_dv.value))
            await FallingEdge(dut.clk)
            t += 1

    mon = cocotb.start_soon(monitor())

    while syms_sent < n_sym:
        await FallingEdge(dut.clk)
        ce_cnt += 1
        if ce_cnt % 8 == 0:
            dut.ce_2m.value = 1
            if ce_cnt % 256 == 0 and syms_sent < n_sym:
                if t0_i is None:
                    t0_i = ce_cnt
                dut.sym.value = int(syms[syms_sent])
                dut.sym_valid.value = 1
                syms_sent += 1
        else:
            dut.ce_2m.value = 0
            dut.sym_valid.value = 0

    extra = 0
    while len(i_got) < n_i + 16 and extra < 2048:
        await FallingEdge(dut.clk)
        extra += 1
        ce_cnt += 1
        dut.ce_2m.value = 1 if ce_cnt % 8 == 0 else 0
        dut.sym_valid.value = 0
    await mon

    i_arr = np.asarray(i_got, dtype=np.int32)
    q_arr = np.asarray(q_got, dtype=np.int32)
    dv_arr = np.asarray(dv_got, dtype=np.uint8)

    # ---------------- 断言: 数据质量 ----------------
    assert len(i_arr) >= n_i, f"I 采样数不足: {len(i_arr)} < {n_i}"
    assert len(q_arr) == len(i_arr), "I/Q 长度不一致"
    assert len(dv_arr) == len(i_arr), "sample_dv 长度与 I/Q 不一致"

    # 位宽: 12bit 有符号, 越界说明定标或截位有问题
    assert i_arr.min() >= IQ_MIN and i_arr.max() <= IQ_MAX, \
        f"I 越界 12bit: [{i_arr.min()}, {i_arr.max()}]"
    assert q_arr.min() >= IQ_MIN and q_arr.max() <= IQ_MAX, \
        f"Q 越界 12bit: [{q_arr.min()}, {q_arr.max()}]"

    # 采集连续性: t0 之后 sample_dv 应恒为 1 (FIR 流水线填满后), 不允许出现缺口
    assert dv_arr.all(), f"sample_dv 出现缺口: {int((dv_arr == 0).sum())} 拍为 0"

    # 与黄金模型 bit-true (这是本实验的质量底线)
    def find_offset(got, exp, tag):
        hits = [L for L in range(0, len(got) - len(exp) + 1)
                if list(got[L:L + len(exp)]) == list(exp)]
        assert len(hits) == 1, f"{tag}: 匹配偏移不唯一/不存在: {hits[:5]}"
        return hits[0]

    li = find_offset(i_arr, i_exp, "I")
    lq = find_offset(q_arr, q_exp, "Q")
    assert li == lq, f"I/Q 相对偏移不一致: {li} vs {lq} (模型应同源)"

    # 导出: 对齐到模型起点, 长度取 Q 路 (比 I 多 sps//2 拍), 下游无需再找偏移
    n_take = n_q
    assert len(i_arr) >= li + n_take and len(q_arr) >= lq + n_take, \
        "采集长度不足以覆盖完整波形 (I 尾部拖尾)"
    payload = dict(
        i=i_arr[li:li + n_take].astype(np.int32),
        q=q_arr[lq:lq + n_take].astype(np.int32),
        dv=dv_arr[li:li + n_take],
        n_i=np.int32(n_i), n_q=np.int32(n_q),
        q_scale=np.float64(Q_SCALE),
        iq_bits=np.int32(IQ_BITS),
        n_sym=np.int32(len(syms)),
        sample_per_sym=np.int32(256),          # 32 码片 × 8 采样
        chip_rate=np.float64(phy.CHIP_RATE),
        sps=np.int32(phy.SPS),
        syms=np.asarray(syms, dtype=np.int32),
        psdu=np.frombuffer(psdu, dtype=np.uint8),
        offset_i=np.int32(li), offset_q=np.int32(lq),
        note="12bit signed 定点 I/Q, Q2.6 (1.0 = 64); 已对齐到黄金模型起点; "
             "含完整 PPDU 前导",
    )
    OUT.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(OUT / "rtl_iq.npz", **payload)

    # 同时给一份人类可读的 CSV (前 512 拍, 便于直接翻看数据流)
    hdr = "idx,i,q,dv\n"
    rows = "".join(f"{k},{int(i_arr[li + k])},{int(q_arr[li + k])},{int(dv_arr[li + k])}\n"
                   for k in range(min(512, n_take)))
    (OUT / "rtl_iq_head.csv").write_text(hdr + rows, encoding="utf-8")

    peak_i = int(np.abs(i_arr).max())
    dut._log.info(
        f"rtl_lab_capture PASS: 采集 {len(i_arr)} 拍 (偏移 I={li}/Q={lq}), "
        f"bit-true 匹配 I {n_i} / Q {n_q} 点, 峰值 |I|={peak_i} (满量程 {IQ_MAX}), "
        f"sample_dv 无缺口, PPDU {len(syms)} 符号 -> 已导出 {OUT}")
