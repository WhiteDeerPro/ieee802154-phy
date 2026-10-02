import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from runutil import report, run

COMMON = ["rtl/common/pn9_whiten.sv", "rtl/common/crc16_fcs.sv"]

if __name__ == "__main__":
    report(run("rx_chain_e2e", "test_e2e", [
        "tb/rx_chain_e2e/e2e_top.sv",
        "rtl/common/half_sine_fir.sv",
        "rtl/rx/frontend/rx_matched_filter.sv",
        "rtl/rx/frontend/preamble_sync.sv",
        "rtl/rx/backend/despreader.sv",
        "rtl/rx/backend/rx_deframer.sv",
    ] + COMMON))
