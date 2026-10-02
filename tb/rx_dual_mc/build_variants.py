import subprocess
import sys
from pathlib import Path

ROOT = Path('/home/host/Desktop/workspace/communication')
sys.path.insert(0, str(ROOT / 'tb' / 'rx_dual_mc'))
import run_dual_mc as R  # noqa: E402

RST_OPT = '-pvalue+dual_mc_tb.RSTEN=1'   # 边界场景: RST_EN=1（与既有 simv_ps_* 一致）


def build(out, sync_rel):
    # 注意: SOURCES 现认为 preamble_sync_csq.sv（I-18 转正）——替换锚随之为 csq
    srcs = [str(ROOT / s) if s != 'rtl/rx/frontend/preamble_sync_csq.sv'
            else str(ROOT / sync_rel) for s in R.SOURCES]
    cmd = [f"{R.VCS_ENV['VCS_HOME']}/bin/vcs", "-full64", "-sverilog",
           "-timescale=1ns/1ps", "-o", str(R.SIM_DIR / out),
           "+incdir+" + str(ROOT / 'rtl' / 'rx'), RST_OPT] + srcs
    csrc = R.SIM_DIR / f"csrc_{out}"
    csrc.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(cmd, cwd=csrc, env=R.VCS_ENV, capture_output=True, text=True)
    ok = r.returncode == 0
    print(f"{out}: {'ok' if ok else 'FAIL'}")
    if not ok:
        sys.stderr.write(r.stdout[-2500:])
    return ok


b1 = build('simv_ps_a16', 'rtl/rx/frontend/preamble_sync.sv')
b2 = build('simv_ps_csq', 'rtl/rx/frontend/preamble_sync_csq.sv')
sys.exit(0 if (b1 and b2) else 1)
