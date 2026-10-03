import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from runutil import report, run

if __name__ == "__main__":
    report(run("adc_gear_ctrl", "test_adc_gear_ctrl",
               ["rtl/rx/top/adc_gear_ctrl.sv"]))
