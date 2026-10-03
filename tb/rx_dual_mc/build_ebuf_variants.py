import subprocess
import sys
from pathlib import Path

ROOT = Path('/home/host/Desktop/workspace/communication')
sys.path.insert(0, str(ROOT / 'tb' / 'rx_dual_mc'))
import run_dual_mc as R  # noqa: E402

RST_OPT = '-pvalue+dual_mc_tb.RSTEN=1'   # 与既有 simv_ps_* 一致的边界场景口径


def build(out, extra=()):
    cmd = [f"{R.VCS_ENV['VCS_HOME']}/bin/vcs", "-full64", "-sverilog",
           "-timescale=1ns/1ps", "-o", str(R.SIM_DIR / out),
           "+incdir+" + str(ROOT / 'rtl' / 'rx'), RST_OPT, *extra]
    cmd += [str(ROOT / s) for s in R.SOURCES]
    csrc = R.SIM_DIR / f"csrc_{out}"
    csrc.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(cmd, cwd=csrc, env=R.VCS_ENV, capture_output=True, text=True)
    ok = r.returncode == 0
    print(f"{out}: {'ok' if ok else 'FAIL'}")
    if not ok:
        sys.stderr.write(r.stdout[-2500:])
    return ok


# ebuf 截位变体（preamble_sync VSHIFT 参数; 见 notes §57）
b0 = build('simv_ebuf_v0')                                    # VSHIFT=0（默认, 等价性基线）
b1 = build('simv_ebuf_v12', ['-pvalue+dual_mc_tb.VSHIFT=12'])  # VSHIFT=12（12 位落点）
sys.exit(0 if (b0 and b1) else 1)
