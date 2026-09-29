import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from runutil import report, run

if __name__ == "__main__":
    report(run("oqpsk_modulator", "test_mod", [
        "rtl/tx/oqpsk_modulator.sv",
        "rtl/common/chip_lut.sv",
        "rtl/common/half_sine_fir.sv",
    ]))
