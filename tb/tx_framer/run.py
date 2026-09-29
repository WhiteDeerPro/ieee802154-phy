import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from runutil import report, run

if __name__ == "__main__":
    report(run("tx_chain", "test_framer", [
        "tb/tx_framer/tx_chain.sv",
        "rtl/tx/tx_framer.sv",
        "rtl/tx/oqpsk_modulator.sv",
        "rtl/common/chip_lut.sv",
        "rtl/common/half_sine_fir.sv",
        "rtl/common/pn9_whiten.sv",
        "rtl/common/crc16_fcs.sv",
    ]))
