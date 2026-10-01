import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from runutil import report, run

ROOT = Path(__file__).resolve().parents[2]

if __name__ == "__main__":
    report(run("rx_dual", "test_rx_dual", [
        "rtl/common/chip_lut.sv",
        "rtl/common/half_sine_fir.sv",
        "rtl/common/pn9_whiten.sv",
        "rtl/common/crc16_fcs.sv",
        "rtl/rx/rx_matched_filter.sv",
        "rtl/rx/cordic_atan2.sv",
        "rtl/rx/cfo_est.sv",
        "rtl/rx/cfo_rot.sv",
        "rtl/rx/preamble_detect.sv",
        "rtl/rx/preamble_sync.sv",
        "rtl/rx/despreader.sv",
        "rtl/rx/rx_deframer.sv",
        "rtl/rx/rx_top.sv",
        "rtl/rx/rx_dual.sv",
    ], build_args=["+incdir+" + str(ROOT / "rtl" / "rx")]))
