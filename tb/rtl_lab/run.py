import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from runutil import report, run

if __name__ == "__main__":
    # 顶层用**纯透传包装** rtl_lab_top: 它只例化 DUT 并加 $dumpfile/$dumpvars
    # (cocotb 是 Python TB, 没地方放系统任务; VCS 的 +vcs+dumpvars 在本环境未生效)。
    # 端口同名同宽, 测试代码 dut.xxx 无需改动。
    report(run("rtl_lab_top", "test_rtl_lab", [
        "rtl/tx/oqpsk_modulator.sv",
        "rtl/common/chip_lut.sv",
        "rtl/common/half_sine_fir.sv",
        "tb/rtl_lab/rtl_lab_top.sv",
    ], build_args=["-debug_access+all"]))
