import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from runutil import report, run

if __name__ == "__main__":
    report(run("chain_top", "test_adc_gear_chain", [
        "rtl/rx/top/adc_gear_ctrl.sv",
        "rtl/rx/top/adc_quant_if.sv",
        "tb/adc_gear_chain/chain_top.sv",
    ]))
