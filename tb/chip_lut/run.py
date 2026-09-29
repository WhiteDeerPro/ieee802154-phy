import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from runutil import report, run

if __name__ == "__main__":
    report(run("chip_lut", "test_chip_lut", ["rtl/common/chip_lut.sv"]))
