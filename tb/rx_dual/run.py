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
        "rtl/rx/frontend/rx_matched_filter.sv",   # 共享前端: MF
        "rtl/rx/variants/preamble_sync.sv",
    "rtl/rx/frontend/preamble_detect.sv",       # 共享前端: 相位恢复（扫描）
    "rtl/rx/frontend/preamble_buf.sv",          # 前导缓冲（I-13）
        "rtl/rx/frontend/deinterleave.sv",        # 共享前端: 持续去交错
        "rtl/rx/backend/cfo_rot.sv",             # 每通道: 码片级消旋
        "rtl/rx/backend/sfd_detect.sv",          # 每通道: SFD 定界（需消旋后）
        "rtl/rx/backend/despreader.sv",
        "rtl/rx/backend/rx_deframer.sv",
        "rtl/rx/backend/rx_chip_backend.sv",     # 每通道执行段
        "rtl/rx/frontend/rx_frontend.sv",         # 共享: MF + 相位 + 去交错
        "rtl/rx/top/rx_dual.sv",
        "tb/rx_dual/rx_dual_wrap.sv",
    ], build_args=["+incdir+" + str(ROOT / "rtl" / "rx")]))
