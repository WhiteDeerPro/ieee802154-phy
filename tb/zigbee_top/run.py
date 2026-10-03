import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from runutil import report, run

ROOT = Path(__file__).resolve().parents[2]

if __name__ == "__main__":
    report(run("zigbee_top", "test_zigbee_top", [
        "rtl/tx/tx_framer.sv", "rtl/tx/oqpsk_modulator.sv",
        "rtl/common/chip_lut.sv", "rtl/common/half_sine_fir.sv",
        "rtl/common/pn9_whiten.sv", "rtl/common/crc16_fcs.sv",
        "rtl/rx/frontend/rx_matched_filter.sv", "rtl/rx/frontend/preamble_sync_csq.sv",
        "rtl/rx/frontend/preamble_detect.sv", "rtl/rx/frontend/preamble_buf.sv",
        "rtl/rx/frontend/deinterleave.sv", "rtl/rx/frontend/rx_frontend.sv",
        "rtl/rx/backend/cfo_rot.sv", "rtl/rx/backend/sfd_detect.sv",
        "rtl/rx/backend/despreader.sv", "rtl/rx/backend/rx_deframer.sv",
        "rtl/rx/backend/rx_chip_backend.sv", "rtl/rx/top/rx_dual.sv",
        "rtl/top/zigbee_top.sv",
    ], build_args=["+incdir+" + str(ROOT / "rtl" / "rx")]))
