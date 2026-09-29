"""rx_deframer 单元测试: 符号流 (自 PHR 起) 直接喂入, 与黄金镜像比对。

验证: PSDU 字节流、psdu_len、fcs_ok、frame_done 时序; 含 FCS 失败用例。
黄金: phy_802154.rx_deframe_symbols (去白化 → PHR 解析 → CRC 校验)。
"""
import sys
from pathlib import Path

import cocotb
import numpy as np
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "model"))
import phy_802154 as phy

SHR_SYMBOLS = 10  # SHR = 5 字节 = 10 符号


async def reset(dut):
    dut.sym.value = 0
    dut.sym_dv.value = 0
    dut.frame_start.value = 0
    dut.rst_n.value = 0
    await Timer(200, unit="ns")
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1


async def feed_symbols(dut, syms):
    """喂符号流, frame_start 与第一个符号同拍; 符号间隔 ≥12 拍 (pn9 每字节 8 拍的吞吐约束)。"""
    data, lens, fcs, dones = [], [], [], 0

    async def poll():
        nonlocal dones
        if dut.data_valid.value == 1:
            data.append(int(dut.data_out.value))
        if dut.frame_done.value == 1:
            dones += 1
            lens.append(int(dut.psdu_len.value))
            fcs.append(int(dut.fcs_ok.value))

    for k, s in enumerate(syms):
        dut.sym.value = int(s)
        dut.sym_dv.value = 1
        dut.frame_start.value = 1 if k == 0 else 0
        await FallingEdge(dut.clk)
        await poll()
        dut.sym_dv.value = 0
        for _ in range(11):          # 符号间隔 12 拍 > pn9 8 拍/字节
            await FallingEdge(dut.clk)
            await poll()
    dut.frame_start.value = 0
    return bytes(data), lens, fcs, dones


@cocotb.test()
async def deframer_good_frame(dut):
    cocotb.start_soon(Clock(dut.clk, 62.5, unit="ns").start())
    await reset(dut)

    rng = np.random.default_rng(7)
    for L in (0, 1, 8, 127):
        psdu = bytes(rng.integers(0, 256, size=L).tolist())
        syms = phy.tx_symbols(psdu)[SHR_SYMBOLS:]
        exp_psdu, exp_ok, exp_len = phy.rx_deframe_symbols(syms)
        assert exp_ok and exp_psdu == psdu and exp_len == L, "黄金镜像自检失败"

        data, lens, fcs, dones = await feed_symbols(dut, syms)
        # 多给几拍让尾字节消化
        for _ in range(20):
            await FallingEdge(dut.clk)
        assert dones == 1, f"L={L}: frame_done 次数 {dones} != 1"
        assert lens == [L], f"L={L}: psdu_len {lens} != [{L}]"
        assert fcs == [1], f"L={L}: fcs_ok {fcs} != [1]"
        assert data == psdu, f"L={L}: PSDU 不匹配 ({len(data)} 字节)"
    dut._log.info("deframer_good_frame PASS: L=0/1/8/127 全过")


@cocotb.test()
async def deframer_bad_fcs(dut):
    cocotb.start_soon(Clock(dut.clk, 62.5, unit="ns").start())
    await reset(dut)

    rng = np.random.default_rng(8)
    psdu = bytes(rng.integers(0, 256, size=8).tolist())
    syms = phy.tx_symbols(psdu)[SHR_SYMBOLS:].copy()
    syms[5] ^= 0x8  # 翻转一个符号 → PSDU 或 FCS 必然出错
    exp_psdu, exp_ok, _ = phy.rx_deframe_symbols(syms)
    assert not exp_ok, "黄金镜像自检失败 (翻转符号后仍判通过?)"

    data, lens, fcs, dones = await feed_symbols(dut, syms)
    for _ in range(20):
        await FallingEdge(dut.clk)
    assert dones == 1, f"frame_done 次数 {dones} != 1"
    assert fcs == [0], f"fcs_ok {fcs} != [0]"
    assert data == exp_psdu, "输出字节应与黄金镜像一致 (即使 FCS 失败)"
    dut._log.info("deframer_bad_fcs PASS: 错误帧 fcs_ok=0 且帧边界正确收尾")
