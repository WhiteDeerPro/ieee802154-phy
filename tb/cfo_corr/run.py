import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from runutil import report, run

HERE = Path(__file__).resolve().parent
RTL_RX = HERE.parents[1] / "rtl" / "rx"

if __name__ == "__main__":
    # c_ref_rom.svh / rot_lut.svh 用 `include 引入, 需要 incdir
    args = ["+incdir+" + str(RTL_RX)]
    if os.getenv("DUMP_VCD"):
        args.append("+define+DUMP_VCD")
    report(run("cfo_corr_top", "test_cfo_corr", [
        "tb/cfo_corr/cfo_corr_top.sv",
        "rtl/rx/cordic_atan2.sv",
        "rtl/rx/cfo_est.sv",
        "rtl/rx/cfo_rot.sv",
    ], build_args=args))
