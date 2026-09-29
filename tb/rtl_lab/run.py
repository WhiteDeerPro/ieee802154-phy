import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from runutil import report, run

# Verdi 的 FSDB PLI (VCS 编译时以 -P <tab> <a> 链接; 用法同邻居项目 SPI/Makefile)
FSDB_PLI_DIR = "/opt/synopsys/verdi201809/share/PLI/VCS/LINUX64"

if __name__ == "__main__":
    # 顶层用**纯透传包装** rtl_lab_top: 它只例化 DUT 并加 $dumpfile/$dumpvars
    # (cocotb 是 Python TB, 没地方放系统任务; VCS 的 +vcs+dumpvars 在本环境未生效)。
    # 端口同名同宽, 测试代码 dut.xxx 无需改动。
    #
    # --fsdb: 链接 Verdi PLI 并定义 DUMP_FSDB → 额外产出 sim_build/rtl_lab.fsdb
    #         (默认不带, 与原来一致; 编译参数经 build_args 透传给 vcs)
    build_args = ["-debug_access+all"]
    if "--fsdb" in sys.argv:
        build_args += [
            "+define+DUMP_FSDB",
            "-P", f"{FSDB_PLI_DIR}/novas.tab", f"{FSDB_PLI_DIR}/pli.a",
        ]
        # 运行时 simv 还要能加载 FSDB dumper 动态库 (libsscore_vcs201809.so):
        # 它在 PLI 目录里; 否则报 "Failed to load FSDB dumper" 且不产出 fsdb。
        _old_ld = os.environ.get("LD_LIBRARY_PATH", "")
        os.environ["LD_LIBRARY_PATH"] = FSDB_PLI_DIR + (f":{_old_ld}" if _old_ld else "")
    report(run("rtl_lab_top", "test_rtl_lab", [
        "rtl/tx/oqpsk_modulator.sv",
        "rtl/common/chip_lut.sv",
        "rtl/common/half_sine_fir.sv",
        "tb/rtl_lab/rtl_lab_top.sv",
    ], build_args=build_args))
