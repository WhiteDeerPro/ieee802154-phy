"""oqpsk_modulator 与黄金模型 modulate_oqpsk_fixed 采样级 bit-true 比对。

对齐方式: t=0 = 首符号 load 拍; I 路 RTL 输出比模型晚 2 拍 (pipe+acc 寄存);
Q 路以首个奇数码片注入拍为各自 t=0, 同样晚 2 拍。
"""
import sys
from pathlib import Path

import cocotb
import numpy as np
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "model"))
import phy_802154 as phy

N_SYM = 20
CLK_NS = 62.5


def to_signed(v, bits=12):
    v = int(v)
    return v - (1 << bits) if v >= (1 << (bits - 1)) else v


@cocotb.test()
async def modulator_bit_true(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_NS, unit="ns").start())
    dut.ce_2m.value = 0
    dut.sym.value = 0
    dut.sym_valid.value = 0
    dut.rst_n.value = 0
    await Timer(200, unit="ns")
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1
    await FallingEdge(dut.clk)

    rng = np.random.default_rng(11)
    syms = rng.integers(0, 16, size=N_SYM).tolist()
    i_exp, q_exp = phy.modulate_oqpsk_fixed(syms)
    n_i, n_q = len(i_exp), len(q_exp)

    i_got, q_got = [], []
    t0_i = None   # 首符号 load 拍
    t0_q = None   # 首个 Q 注入拍 (load 拍 +12)
    syms_sent = 0
    ce_cnt = 0

    # 采样协程: 从 t0_i 起连续采集 I/Q, 长度多留 16 拍拖尾;
    # 与模型的采样偏移由后续相关自动确定 (消除拍计数器相位歧义)
    async def monitor():
        t = 0
        while len(i_got) < n_i + 16 and t < N_SYM * 256 + 8192:
            if t0_i is not None:
                i_got.append(to_signed(dut.i_out.value))
                q_got.append(to_signed(dut.q_out.value))
            await FallingEdge(dut.clk)
            t += 1

    mon = cocotb.start_soon(monitor())

    # 驱动: ce_2m 每 8 拍一次; 每 32 个 ce 发一个符号
    while syms_sent < N_SYM:
        await FallingEdge(dut.clk)
        ce_cnt += 1
        if ce_cnt % 8 == 0:
            dut.ce_2m.value = 1
            if ce_cnt % 256 == 0 and syms_sent < N_SYM:
                if t0_i is None:
                    t0_i = ce_cnt          # 本拍 load (monitor 用统一拍计数)
                    t0_q = ce_cnt + 12     # 首奇数码片 C1: ce 拍 t0+8, 注入 +4
                dut.sym.value = int(syms[syms_sent])
                dut.sym_valid.value = 1
                syms_sent += 1
        else:
            dut.ce_2m.value = 0
            dut.sym_valid.value = 0

    # 收尾: 继续打满最后一个符号的 32 个 ce 拍 + FIR 拖尾
    extra = 0
    while len(i_got) < n_i + 16 and extra < 2048:
        await FallingEdge(dut.clk)
        extra += 1
        ce_cnt += 1
        dut.ce_2m.value = 1 if ce_cnt % 8 == 0 else 0
        dut.sym_valid.value = 0
    await mon

    assert len(i_got) >= n_i, f"I 采样数不足: {len(i_got)} < {n_i} (t0_i={t0_i})"

    def find_offset(got, exp, tag):
        """在 got 中找唯一偏移 L 使 got[L:L+len(exp)] == exp"""
        hits = [L for L in range(0, len(got) - len(exp) + 1)
                if got[L:L + len(exp)] == exp]
        assert len(hits) == 1, f"{tag}: 匹配偏移不唯一/不存在: {hits[:5]} (len={len(got)})"
        return hits[0]

    Li = find_offset(i_got, i_exp, "I")
    Lq = find_offset(q_got, q_exp, "Q")
    dut._log.info(
        f"oqpsk_modulator PASS: I {n_i} 采样(偏移{Li}) + Q {n_q} 采样(偏移{Lq}) 全匹配"
    )
