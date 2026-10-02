import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from runutil import report, run

COMMON = ["rtl/common/pn9_whiten.sv", "rtl/common/crc16_fcs.sv"]

# Verdi 的 FSDB PLI (VCS 编译时以 -P <tab> <a> 链接; 用法同邻居项目 SPI/Makefile)
FSDB_PLI_DIR = "/opt/synopsys/verdi201809/share/PLI/VCS/LINUX64"

if __name__ == "__main__":
    # 顶层用纯透传包装 rx_lab_top (例化 rx_chain_e2e + $dumpfile/$dumpvars),
    # 因此仿真同时产出 sim_build/rx_lab.vcd, 含各阶段内部 wire。
    #
    # --fsdb: 链接 Verdi PLI 并定义 DUMP_FSDB → 额外产出 sim_build/rx_lab.fsdb
    #         (全层次; 默认不带, 与原来一致)
    build_args = ["-debug_access+all"]
    if "--fsdb" in sys.argv:
        build_args += [
            "+define+DUMP_FSDB",
            "-kdb",   # 生成 KDB (simv.daidir): Verdi -dbdir 加载设计源码/层次用
            "-P", f"{FSDB_PLI_DIR}/novas.tab", f"{FSDB_PLI_DIR}/pli.a",
        ]
        # 运行时 simv 还要能加载 FSDB dumper 动态库 (libsscore_vcs201809.so)
        _old_ld = os.environ.get("LD_LIBRARY_PATH", "")
        os.environ["LD_LIBRARY_PATH"] = FSDB_PLI_DIR + (f":{_old_ld}" if _old_ld else "")
    report(run("rx_lab_top", "test_rx_lab", [
        "tb/rx_lab/rx_lab_top.sv",
        "tb/rx_chain_e2e/e2e_top.sv",
        "rtl/common/half_sine_fir.sv",
        "rtl/rx/frontend/rx_matched_filter.sv",
        "rtl/rx/frontend/preamble_sync.sv",
        "rtl/rx/backend/despreader.sv",
        "rtl/rx/backend/rx_deframer.sv",
    ] + COMMON, build_args=build_args))
