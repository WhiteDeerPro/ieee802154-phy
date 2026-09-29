"""cocotb + VCS 冒烟测试：复位后计数器从 0 递增。"""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, Timer


@cocotb.test()
async def smoke_count(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.rst_n.value = 0
    await Timer(25, unit="ns")
    assert dut.count.value == 0, "复位值应为 0"
    dut.rst_n.value = 1
    for i in range(1, 5):
        await RisingEdge(dut.clk)
        await Timer(1, unit="ns")  # 避开与 always_ff 的读竞争
        assert dut.count.value == i, f"第 {i} 拍应为 {i}, 实际 {dut.count.value}"
    dut._log.info("smoke test passed: cocotb + VCS 闭环正常")
