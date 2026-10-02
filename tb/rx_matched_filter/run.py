import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from runutil import report, run

if __name__ == "__main__":
    report(run("rx_matched_filter", "test_mf", [
        "rtl/rx/frontend/rx_matched_filter.sv",
        "rtl/common/half_sine_fir.sv",
    ]))
