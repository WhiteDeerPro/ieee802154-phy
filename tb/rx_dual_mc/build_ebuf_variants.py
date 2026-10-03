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


def build_if_missing(out, extra=()):
    if (R.SIM_DIR / out).exists():
        print(f"{out}: exists, skip")
        return True
    return build(out, extra)


# ebuf 截位变体（preamble_sync VSHIFT 参数; 见 notes §57/§59）
bins = [
    ('simv_ebuf_v0',  []),                                    # VSHIFT=0（等价性基线）
    ('simv_ebuf_v12', ['-pvalue+dual_mc_tb.VSHIFT=12']),      # 12 位落点（v12）
    ('simv_ebuf_v13', ['-pvalue+dual_mc_tb.VSHIFT=13']),      # 11 位（边界钉定）
    ('simv_ebuf_v14', ['-pvalue+dual_mc_tb.VSHIFT=14']),      # 10 位（进攻）
    ('simv_ebuf_v16', ['-pvalue+dual_mc_tb.VSHIFT=16']),      # 8 位（进攻）
]
ok = True
for out, extra in bins:
    ok &= build_if_missing(out, extra)
sys.exit(0 if ok else 1)
