import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from runutil import report, run

COMMON = ["rtl/common/pn9_whiten.sv", "rtl/common/crc16_fcs.sv"]

if __name__ == "__main__":
    # 1) 单元级: rx_deframer 单独例化, 直接喂符号流
    report(run("rx_deframer", "test_rx_deframer",
               ["rtl/rx/rx_deframer.sv"] + COMMON))
    # 2) 链级: preamble_sync → despreader → rx_deframer (同一 build 目录顺序覆盖)
    report(run("rx_chain", "test_rx_chain", [
        "tb/rx_deframer/rx_chain.sv",
        "rtl/rx/preamble_sync.sv",
        "rtl/rx/despreader.sv",
        "rtl/rx/rx_deframer.sv",
    ] + COMMON))
