"""adc_gear_chain: 集成对照 —— gear_ctrl(决策) + quant_if(执行) ↔ ref(model/algo/adc_gear.py)。

约定:
  · 每帧: 帧事件后跑链路到空闲（处理本帧触发的档位切换握手）; 对照 dut.adc_gear。
  · monotone/random/burst: 逐帧对照 ref 的生效档（无 force）。
  · force: 显式断言序列（force 直连执行侧为"即时"语义, 与 ref 的帧语义错位——单独测）。
档位编码: 0=12b, 2=8b, 4=4b（v1 策略集; 1/3 预留）。
"""
import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, ReadOnly
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'model'))
from algo.adc_gear import AdcGear      # noqa: E402

ENC = {16: 0, 12: 1, 8: 2, 4: 3}


async def reset(dut):
    dut.rst_n.value = 0
    for s in ('frame_ev', 'snr_valid', 'fcs_ok', 'sup_force', 'force_full', 'adc_ack'):
        getattr(dut, s).value = 0
    dut.snr_est.value = 0
    dut.fcs_ok.value = 1
    await FallingEdge(dut.clk)
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1
    await FallingEdge(dut.clk)


async def service(dut, max_ticks=8):
    """跑链路到空闲: 处理可能的一次档位切换握手; 返回最终 adc_gear。"""
    for _ in range(max_ticks):
        await RisingEdge(dut.clk)
        await ReadOnly()
        rq = int(dut.adc_req.value)
        g = int(dut.adc_gear.value)
        await FallingEdge(dut.clk)
        if rq:
            dut.adc_ack.value = 1
            await RisingEdge(dut.clk)
            await FallingEdge(dut.clk)
            dut.adc_ack.value = 0
        else:
            return g
    return g


async def frame(dut, snr_valid, snr_est, fcs_ok):
    dut.snr_valid.value = int(bool(snr_valid))
    dut.snr_est.value = int(snr_est)
    dut.fcs_ok.value = int(bool(fcs_ok))
    dut.frame_ev.value = 1
    await RisingEdge(dut.clk)
    await FallingEdge(dut.clk)
    dut.frame_ev.value = 0
    return await service(dut)


async def run_sequence(dut, events, label):
    ref = AdcGear()
    for i, (sv, se, ok) in enumerate(events):
        exp = ENC[ref.on_frame(snr_valid=sv, snr_est=se, fcs_ok=ok)]
        got = await frame(dut, sv, se, ok)
        assert got == exp, (
            f"[{label}] 帧{i}: 链路档位 {got} != ref {exp} "
            f"(sv={sv} se={se} ok={ok}; ref fail={ref.fail_cnt} ttl={ref.ttl_cnt})")
    dut._log.info(f"{label}: {len(events)} 帧生效档逐帧一致 OK")


def scene_monotone():
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
        ev.append((bool(rng.random() > 0.2), snr, ok))
    return ev


def scene_burst_fail():
    ev = []
    for k in range(120):
        snr = 22 if k < 60 else 4
        ev.append((True, snr, not (55 <= k < 65)))
    return ev


@cocotb.test()
async def test_chain(dut):
    cocotb.start_soon(Clock(dut.clk, 62.5, unit="ns").start())

    # —— 逐帧对照（无 force）——
    await reset(dut)
    await run_sequence(dut, scene_monotone(), "monotone")
    await reset(dut)
    await run_sequence(dut, scene_random(seed=7), "random")
    await reset(dut)
    await run_sequence(dut, scene_burst_fail(), "burst_fail")

    # —— force 显式序列（监督绕过决策层, 执行侧即时）——
    await reset(dut)
    # 1) 降档到 4bit（好环境 + permit）
    g = await frame(dut, True, 25, True)
    assert g == 3, f"应降到 4bit: {g}"
    # 2) 拉 force（同源双信号）→ 执行侧即时强制 12b
    dut.force_full.value = 1
    dut.sup_force.value = 1
    g = await service(dut)
    assert g == 0, f"force 应强制全精度(16b): {g}"
    # 3) force 期间帧事件（环境好）→ 降档被拒, 保持 12b
    g = await frame(dut, True, 25, True)
    assert g == 0, f"force 期间不应降档: {g}"
    # 4) 释放 force → 帧事件 → 重新允许降档
    dut.force_full.value = 0
    dut.sup_force.value = 0
    await FallingEdge(dut.clk)
    g = await frame(dut, True, 25, True)
    assert g == 3, f"释放后应恢复降档: {g}"
    dut._log.info("force 序列 4 项断言通过 ✓")
