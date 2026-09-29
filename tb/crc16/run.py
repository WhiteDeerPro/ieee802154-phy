import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from runutil import report, run

if __name__ == "__main__":
    report(run("crc16_fcs", "test_crc16", ["rtl/common/crc16_fcs.sv"]))
