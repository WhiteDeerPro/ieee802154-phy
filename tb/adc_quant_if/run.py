import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from runutil import report, run

if __name__ == "__main__":
    report(run("adc_quant_if", "test_adc_quant_if",
               ["rtl/rx/top/adc_quant_if.sv"]))
