import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from runutil import run
HERE = Path(__file__).resolve().parent
run("cfo_corr_top", "test_dbg", [
    "tb/cfo_corr/cfo_corr_top.sv",
    "rtl/rx/legacy/cordic_atan2.sv", "rtl/rx/legacy/cfo_est.sv", "rtl/rx/backend/cfo_rot.sv",
], build_args=["+incdir+" + str(HERE.parents[1] / "rtl" / "rx")])
