"""通用 runner: build + test 一条龙。用法见 tb/smoke。

本机仿真环境为 Synopsys VCS（原机用 iverilog，已迁移；此机无 iverilog）。
"""
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# —— VCS 仿真环境（可被外部环境变量覆盖）——
# 关键：VCS_TARGET_ARCH=amd64，否则 VCS 会把 x86_64 误判为 32 位 linux 而找不到编译器。
os.environ.setdefault("VCS_HOME", "/opt/synopsys/vcs201809")
os.environ.setdefault("VCS_TARGET_ARCH", "amd64")
os.environ.setdefault("SNPSLMD_LICENSE_FILE", "/opt/synopsys/Synopsys.dat")
os.environ.setdefault("LM_LICENSE_FILE", "/opt/synopsys/Synopsys.dat")

from cocotb_tools.runner import get_runner


def run(toplevel, test_module, sources, waves=False, build_args=None, test_args=None):
    """构建 + 跑测试一条龙。

    waves=True 让仿真器自己处理波形 —— VCS 下 cocotb 走 -qwavedb, **实测未生成文件**;
    要看波形请用 wrapper 方式 (见 ``tb/rtl_lab/rtl_lab_top.sv``: 一个只做透传 +
    $dumpfile/$dumpvars 的顶层包装, 端口同名同宽所以测试代码无需改动)。
    —— 不要试图用“额外编译一个空模块挂 initial”的做法: 未被例化的模块会被 VCS
    直接丢弃, 根本不会进入设计。
    build_args / test_args 直接透传给 runner.build / runner.test。
    """
    # 以调用本函数的 run.py 所在目录为验证点目录
    tb_dir = Path(sys.argv[0]).resolve().parent
    root = tb_dir.parents[1]
    runner = get_runner(os.getenv("SIM", "vcs"))
    build_dir = tb_dir / "sim_build"
    runner.build(
        sources=[root / s for s in sources],
        hdl_toplevel=toplevel,
        build_dir=build_dir,
        always=True,
        waves=waves,
        build_args=list(build_args or []),
    )
    runner.test(
        hdl_toplevel=toplevel,
        test_module=test_module,
        build_dir=build_dir,
        waves=waves,
        test_args=list(test_args or []),
    )
    return build_dir


def report(build_dir):
    """解析 results.xml 判定通过; 缺失或 failures/errors 非零都判 FAIL。"""
    xml = Path(build_dir) / "results.xml"
    if not xml.exists():
        print("FAIL: results.xml 缺失 (test 阶段异常)")
        sys.exit(1)
    top = ET.parse(xml).getroot()
    fails = errs = tests = 0
    for ts in top.iter("testsuite"):
        tests += int(ts.attrib.get("tests", 0))
        fails += int(ts.attrib.get("failures", 0))
        errs += int(ts.attrib.get("errors", 0))
    if fails or errs:
        print(f"FAIL: {fails} failures, {errs} errors / {tests} tests")
        sys.exit(1)
    print(f"PASS ({tests} tests)")
