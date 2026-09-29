import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from runutil import run
HERE = Path(__file__).resolve().parent
run("cfo_corr_top", "test_wave", [
    "tb/cfo_corr/cfo_corr_top.sv",
    "rtl/rx/cordic_atan2.sv", "rtl/rx/cfo_est.sv", "rtl/rx/cfo_rot.sv",
], build_args=["+incdir+" + str(HERE.parents[1] / "rtl" / "rx"), "+define+DUMP_VCD"])
