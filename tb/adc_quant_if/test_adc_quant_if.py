"""adc_quant_if: 权限闸门 + 握手 行为测试（cocotb）。

权限矩阵（docs/21 §15）:
  升档: 恒允许; 降档: 需 permit(set_low_ok) 且不越 GEAR_MIN; force_full: 强制全态 + 拒降。
"""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, ReadOnly


async def reset(dut):
    dut.set_gear.value = 0
    dut.set_req.value = 0
    dut.set_low_ok.value = 0
    dut.set_force_full.value = 0
    dut.adc_ack.value = 0
    dut.rst_n.value = 0
    await FallingEdge(dut.clk)
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1
    await FallingEdge(dut.clk)


async def read(dut):
    await ReadOnly()
    v = (int(dut.adc_gear.value), int(dut.adc_req.value), int(dut.denied.value))
    await FallingEdge(dut.clk)      # 离开 ReadOnly 区, 允许后续写入
    return v


async def pulse_set(dut, gear, low_ok=0, force=0):
    """请求一拍; 返回请求沿的 (gear, req, denied)。"""
    dut.set_gear.value = gear
    dut.set_low_ok.value = low_ok
    dut.set_force_full.value = force
    dut.set_req.value = 1
    await RisingEdge(dut.clk)
    await ReadOnly()
    v = (int(dut.adc_gear.value), int(dut.adc_req.value), int(dut.denied.value))
    await FallingEdge(dut.clk)
    dut.set_req.value = 0
    return v


async def do_ack(dut):
    dut.adc_ack.value = 1
    await RisingEdge(dut.clk)
    await FallingEdge(dut.clk)
    dut.adc_ack.value = 0
    await RisingEdge(dut.clk)


@cocotb.test()
async def test_quant_if(dut):
    cocotb.start_soon(Clock(dut.clk, 62.5, unit="ns").start())
    await reset(dut)
    g, rq, dn = await read(dut)
    assert (g, rq, dn) == (0, 0, 0), f"复位态应为 12b 空: {(g, rq, dn)}"

    # 1) 降档无 permit → 拒绝
    g, rq, dn = await pulse_set(dut, 2, low_ok=0)
    assert (g, rq) == (0, 0) and dn == 1, f"无 permit 降档应被拒: g={g} req={rq} dn={dn}"

    # 2) 降档有 permit → 接受 + 握手
    g, rq, dn = await pulse_set(dut, 1, low_ok=1)
    assert (g, rq) == (1, 1) and dn == 0, f"permit 降档应接受: g={g} req={rq} dn={dn}"
    await do_ack(dut)
    g, rq, dn = await read(dut)
    assert rq == 0, "ack 后 req 应清零"

    # 3) 升档（无需 permit）→ 恒允许
    g, rq, dn = await pulse_set(dut, 0)
    assert (g, rq) == (0, 1) and dn == 0, f"升档应恒允许: g={g} req={rq}"
    await do_ack(dut)

    # 4) 降到 4bit 后测 force（外部代理: 强制全态 + 拒降）
    await pulse_set(dut, 2, low_ok=1)
    await do_ack(dut)
    dut.set_force_full.value = 1
    await RisingEdge(dut.clk)
    g, rq, dn = await read(dut)
    assert (g, rq) == (0, 1), f"force 应强制全态: g={g} req={rq}"
    await do_ack(dut)
    g, rq, dn = await pulse_set(dut, 2, low_ok=1, force=1)
    assert g == 0 and dn == 1, f"force 期间降档应拒: g={g} dn={dn}"
    dut.set_force_full.value = 0
    await FallingEdge(dut.clk)
    g, rq, dn = await pulse_set(dut, 2, low_ok=1)
    assert (g, rq) == (2, 1) and dn == 0, f"释放 force 后降档应恢复: g={g} req={rq} dn={dn}"
    await do_ack(dut)

    # 5) 忙保护: req 未 ack 时新请求被忽略
    g, rq, dn = await pulse_set(dut, 0)                  # 升到 12b, req=1 未 ack
    assert (g, rq) == (0, 1)
    g2, rq2, dn2 = await pulse_set(dut, 1, low_ok=1)     # 忙时请求
    assert (g2, rq2, dn2) == (0, 1, 0), f"忙时请求应被忽略: {(g2, rq2, dn2)}"
    await do_ack(dut)
    g, rq, dn = await read(dut)
    assert rq == 0 and g == 0
    dut._log.info("权限矩阵 6 项断言全部通过 ✓")
