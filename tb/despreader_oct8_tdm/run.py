import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from runutil import report, run

if __name__ == "__main__":
    report(run("cmp_top", "test_despreader_oct8_tdm",
               ["rtl/rx/variants/despreader_oct8.sv",
                "rtl/rx/variants/despreader_oct8_tdm.sv",
                "tb/despreader_oct8_tdm/cmp_top.sv"]))
