"""adc_gear_ctrl: ref(model/algo/adc_gear.py) ↔ RTL（rtl/rx/top/adc_gear_ctrl.sv）
逐帧 bit-true 对照。

场景（固定种子）: 单调下降+回升 / 随机跳变 / 失败突发。
事件约定: frame_ev 单拍; 同拍携带 snr_valid/snr_est/fcs_ok; 每帧对照 gear_req。
（注: cocotb+VCS 每时钟沿交互开销大, 场景规模控制在数百帧。）
"""
import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, ReadOnly
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'model'))
from algo.adc_gear import AdcGear      # noqa: E402

ENC = {12: 0, 8: 1, 4: 2}


async def reset(dut):
    """复位（时钟由 test 启动一次; 此处不重复 start）。"""
    dut.rst_n.value = 0
    dut.frame_ev.value = 0
    dut.snr_valid.value = 0
    dut.snr_est.value = 0
    dut.fcs_ok.value = 1
    await FallingEdge(dut.clk)
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1
    await FallingEdge(dut.clk)


async def frame(dut, snr_valid, snr_est, fcs_ok):
    """一个帧边界事件: 低电平期置值 → 上升沿采样 → ReadOnly 读 gear_req。"""
    dut.snr_valid.value = int(bool(snr_valid))
    dut.snr_est.value = int(snr_est)
    dut.fcs_ok.value = int(bool(fcs_ok))
    dut.frame_ev.value = 1
    await RisingEdge(dut.clk)
    await ReadOnly()
    g = int(dut.gear_req.value)
    await FallingEdge(dut.clk)          # 离开 ReadOnly 区后方可写
    dut.frame_ev.value = 0
    return g


async def run_sequence(dut, events, label):
    ref = AdcGear()
    for i, (sv, se, ok) in enumerate(events):
        exp = ref.on_frame(snr_valid=sv, snr_est=se, fcs_ok=ok)
        got = await frame(dut, sv, se, ok)
        assert got == ENC[exp], (
            f"[{label}] 帧{i}: RTL gear_req={got} != ref={exp} "
            f"(事件 sv={sv} se={se} ok={ok}; ref 内部 fail={ref.fail_cnt} ttl={ref.ttl_cnt})")
    dut._log.info(f"{label}: {len(events)} 帧逐位一致 OK")


def scene_monotone():
    """20dB 保持 → 单调降至 2dB → 回升 20dB（与 run_adc_gear 语义同族）。"""
    ev = []
    for snr in [20] * 25 + list(np.repeat(np.arange(18, 1, -2), 12)) + [2] * 25 + [20] * 40:
        ev.append((True, int(snr), True))
    return ev


def scene_random(seed=7, n=150):
    rng = np.random.default_rng(seed)
    ev = []
    for _ in range(n):
        snr = int(rng.integers(0, 28))
        ok = bool(rng.random() > rng.choice([0.0, 0.1, 0.3], p=[0.6, 0.3, 0.1]))
        sv = bool(rng.random() > 0.2)
        ev.append((sv, snr, ok))
    return ev


def scene_burst_fail(seed=2):
    ev = []
    for k in range(120):
        snr = 22 if k < 60 else 4
        ok = not (55 <= k < 65)            # 10 连败突发
        ev.append((True, snr, ok))
    return ev


@cocotb.test()
async def test_gear(dut):
    cocotb.start_soon(Clock(dut.clk, 62.5, unit="ns").start())   # 时钟仅此一次
    await reset(dut)
    await run_sequence(dut, scene_monotone(), "monotone")
    await reset(dut)
    await run_sequence(dut, scene_random(seed=7), "random")
    await reset(dut)
    await run_sequence(dut, scene_burst_fail(), "burst_fail")
