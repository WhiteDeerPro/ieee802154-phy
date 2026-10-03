import subprocess
import sys
from pathlib import Path

ROOT = Path('/home/host/Desktop/workspace/communication')
sys.path.insert(0, str(ROOT / 'tb' / 'rx_dual_mc'))
import run_dual_mc as R  # noqa: E402

RST_OPT = '-pvalue+dual_mc_tb.RSTEN=1'   # 边界场景: RST_EN=1（与既有 simv_ps_* 一致）


def build(out, sync_rel, extra_args=None, extra_srcs=None):
    # 注意: SOURCES 现认为 preamble_sync_csq.sv（I-18 转正）——替换锚随之为 csq
    srcs = [str(ROOT / s) if s != 'rtl/rx/frontend/preamble_sync_csq.sv'
            else str(ROOT / sync_rel) for s in R.SOURCES]
    srcs += [str(ROOT / s) for s in (extra_srcs or [])]
    cmd = [f"{R.VCS_ENV['VCS_HOME']}/bin/vcs", "-full64", "-sverilog",
           "-timescale=1ns/1ps", "-o", str(R.SIM_DIR / out),
           "+incdir+" + str(ROOT / 'rtl' / 'rx'), RST_OPT] + (extra_args or []) + srcs
    csrc = R.SIM_DIR / f"csrc_{out}"
    csrc.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(cmd, cwd=csrc, env=R.VCS_ENV, capture_output=True, text=True)
    ok = r.returncode == 0
    print(f"{out}: {'ok' if ok else 'FAIL'}")
    if not ok:
        sys.stderr.write(r.stdout[-2500:])
    return ok


b1 = build('simv_ps_a16', 'rtl/rx/variants/preamble_sync.sv')
# 2026-10-04 基线切换: csq 主构建 = 同步器 csq + **despreader TDM+四边形**
#   （已验证: 单元 MODE=2 逐符号一致; 全回归 + 200 帧零差）。
#   旧基线（原版 despreader）: 去掉下面两个 define 与 extra_srcs 即重建。
b2 = build('simv_ps_csq', 'rtl/rx/frontend/preamble_sync_csq.sv',
           extra_args=['+define+DESP_OCT8_TDM', '+define+DESP_QUAD'],
           extra_srcs=['rtl/rx/variants/despreader_oct8_tdm.sv'])
sys.exit(0 if (b1 and b2) else 1)
