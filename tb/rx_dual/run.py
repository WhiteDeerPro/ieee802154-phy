import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from runutil import report, run

ROOT = Path(__file__).resolve().parents[2]

if __name__ == "__main__":
    report(run("rx_dual_wrap", "test_rx_dual", [
        "rtl/common/chip_lut.sv",
        "rtl/common/half_sine_fir.sv",
        "rtl/common/pn9_whiten.sv",
        "rtl/common/crc16_fcs.sv",
        "rtl/rx/rx_matched_filter.sv",   # 共享前端
        "rtl/rx/cfo_rot.sv",
        "rtl/rx/preamble_sync.sv",
        "rtl/rx/preamble_lock.sv",       # 精简同步器（无扫描, 定时外置）
        "rtl/rx/despreader.sv",
        "rtl/rx/rx_deframer.sv",
        "rtl/rx/rx_backend.sv",          # 执行段（每通道一份）
        "rtl/rx/rx_dual.sv",             # 共享前端 + 两个 backend
        "tb/rx_dual/rx_dual_wrap.sv",    # 波形包装（$dumpfile/$dumpvars）
    ], build_args=["+incdir+" + str(ROOT / "rtl" / "rx")]))
