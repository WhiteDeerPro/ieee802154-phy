"""TX 链 (tx_framer + oqpsk_modulator) vs 黄金模型全帧 bit-true 比对。

黄金参考: modulate_oqpsk_fixed(tx_symbols(psdu)) — 组帧/白化/FCS/扩频/成形全在 Python。
RTL: PSDU 字节流入, 16MHz I/Q 采样流出。采样偏移由相关自动确定。
"""
import sys
from pathlib import Path

import cocotb
import numpy as np
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "model"))
import phy_802154 as phy

CLK_NS = 62.5
PSDU = bytes([0x01, 0x80, 0xA5, 0x5A, 0x00, 0xFF, 0x42, 0xC3, 0x11, 0x99])


def to_signed(v, bits=12):
    v = int(v)
    return v - (1 << bits) if v >= (1 << (bits - 1)) else v


@cocotb.test()
async def tx_chain_bit_true(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_NS, unit="ns").start())
    for sig, v in (("ce_2m", 0), ("start", 0), ("len", 0), ("data_in", 0),
                   ("data_valid", 0), ("sym_valid_unused", 0)):
        try:
            getattr(dut, sig).value = v
        except AttributeError:
            pass
    dut.rst_n.value = 0
    await Timer(200, unit="ns")
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1

    syms = phy.tx_symbols(PSDU).tolist()
    i_exp, q_exp = phy.modulate_oqpsk_fixed(syms)
    n_i, n_q = len(i_exp), len(q_exp)

    i_got, q_got = [], []
    started = False
    ce_cnt = 0
    sent = 0

    async def monitor():
        t = 0
        while len(i_got) < n_i + 16 and t < (len(syms) + 8) * 256:
            if started:
                i_got.append(to_signed(dut.i_out.value))
                q_got.append(to_signed(dut.q_out.value))
            await FallingEdge(dut.clk)
            t += 1

    mon = cocotb.start_soon(monitor())

    # 发 start
    while dut.busy.value == 1:
        await FallingEdge(dut.clk)
    await FallingEdge(dut.clk)
    dut.len.value = len(PSDU)
    dut.start.value = 1
    await FallingEdge(dut.clk)
    dut.start.value = 0
    started = True

    # 打 ce + 喂 PSDU 字节
    while dut.done.value == 0:
        await FallingEdge(dut.clk)
        ce_cnt += 1
        dut.ce_2m.value = 1 if ce_cnt % 8 == 0 else 0
        if dut.data_ready.value == 1 and sent < len(PSDU):
            dut.data_in.value = PSDU[sent]
            sent += 1
        else:
            dut.data_in.value = 0
    # 打满末符号 + FIR 拖尾
    extra = 0
    while len(i_got) < n_i + 16 and extra < 4096:
        await FallingEdge(dut.clk)
        extra += 1
        ce_cnt += 1
        dut.ce_2m.value = 1 if ce_cnt % 8 == 0 else 0
    await mon

    assert sent == len(PSDU), f"PSDU 字节未送完: {sent}/{len(PSDU)}"


    # 符号级判定: framer 发出的符号流应与黄金符号完全一致

    def find_offset(got, exp, tag):
        hits = [L for L in range(0, len(got) - len(exp) + 1)
                if got[L:L + len(exp)] == exp]
        if not hits:
            L = 12
            bad = [(k, got[L + k], exp[k])
                   for k in range(len(exp)) if got[L + k] != exp[k]]
            dut._log.info(f"DBG {tag} L={L} 不匹配 {len(bad)}/9216, 前 8: {bad[:8]}")
            dut._log.info(f"DBG {tag} 不匹配采样位置: {[b[0] for b in bad][:30]}")
        assert len(hits) == 1, f"{tag}: 匹配偏移不唯一/不存在: {hits[:5]}"
        return hits[0]

    Li = find_offset(i_got, i_exp, "I")
    Lq = find_offset(q_got, q_exp, "Q")

    # —— [显式 FCS 断言, 2026-10-02] FCS = 末 2 字节 = 末 4 个符号 ——
    # 波形 bit-true 已隐含其正确性; 此处显式切片, 供直接审阅/防回归。
    import hashlib
    n_sym = len(syms)
    fcs_syms = syms[-4:]
    fcs_i_exp = i_exp[(n_sym - 4) * 8:]
    fcs_q_exp = q_exp[(n_sym - 4) * 8:]
    assert len(i_got) >= Li + (n_sym - 4) * 8 + 32, "波形长度不足, 无法校验 FCS 段"
    assert i_got[Li + (n_sym - 4) * 8: Li + (n_sym - 4) * 8 + len(fcs_i_exp)] == fcs_i_exp, "FCS 段 I 波形不匹配"
    if (n_sym - 4) * 8 + len(fcs_q_exp) <= len(q_got) - Lq:
        assert q_got[Lq + (n_sym - 4) * 8: Lq + (n_sym - 4) * 8 + len(fcs_q_exp)] == fcs_q_exp, "FCS 段 Q 波形不匹配"
    import os
    _out = Path(__file__).resolve().parents[2] / "model" / "out" / "tx_wave"
    _out.mkdir(parents=True, exist_ok=True)
    np.save(_out / "tx_iq_rtl.npy", np.array([i_got, q_got]))
    (_out / "tx_iq_meta.txt").write_text(
        f"PSDU={PSDU.hex()}\nn_sym={n_sym}\nLi={Li}\nLq={Lq}\n"
        f"fcs_sym_bytes={[hex(int(s)) for s in fcs_syms]}\n")
    dut._log.info(f"FCS 显式断言 PASS（末 4 符号）; 波形已导出 {_out}")
    dut._log.info(
        f"tx_chain PASS: {len(PSDU)}B PSDU, {len(syms)} 符号, "
        f"I {n_i} 采样(偏移{Li}) + Q {n_q} 采样(偏移{Lq}) 全匹配"
    )
