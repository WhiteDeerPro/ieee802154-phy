"""用 cocotb Python runner 驱动 VCS，无需 GNU make。"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import runutil  # noqa: F401  触发 VCS 环境变量 setdefault（见 runutil 顶部）

from cocotb_tools.runner import get_runner

SIM = os.getenv("SIM", "vcs")
HERE = Path(__file__).parent


def test_smoke():
    runner = get_runner(SIM)
    sources = [HERE / "smoke_tb.sv"]
    runner.build(
        sources=sources,
        hdl_toplevel="smoke_tb",
        build_dir=HERE / "sim_build",
        always=True,
    )
    runner.test(
        hdl_toplevel="smoke_tb",
        test_module="test_smoke",
        build_dir=HERE / "sim_build",
    )


if __name__ == "__main__":
    results = test_smoke() or []
    failed = [r for r in results if not r.passed] if results else []
    if failed:
        print(f"SMOKE FAIL: {len(failed)} test(s) failed")
        sys.exit(1)
    print("SMOKE PASS")
