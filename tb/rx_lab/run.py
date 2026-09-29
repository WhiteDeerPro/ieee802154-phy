import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from runutil import report, run

COMMON = ["rtl/common/pn9_whiten.sv", "rtl/common/crc16_fcs.sv"]

if __name__ == "__main__":
    # 顶层用纯透传包装 rx_lab_top (例化 rx_chain_e2e + $dumpfile/$dumpvars),
    # 因此仿真同时产出 sim_build/rx_lab.vcd, 含各阶段内部 wire。
    report(run("rx_lab_top", "test_rx_lab", [
        "tb/rx_lab/rx_lab_top.sv",
        "tb/rx_chain_e2e/e2e_top.sv",
        "rtl/common/half_sine_fir.sv",
        "rtl/rx/rx_matched_filter.sv",
        "rtl/rx/preamble_sync.sv",
        "rtl/rx/despreader.sv",
        "rtl/rx/rx_deframer.sv",
    ] + COMMON, build_args=["-debug_access+all"]))
