"""BLK 旋钮 RTL 验证: 编译 BLK=16/128 的 pbuf 变体, 同场景跑, 与线性对照比 SNR。
预期（§39 Python 模型）: BLK16 → ~42.8dB, BLK128 → ~41.7dB; BLK64 → 41.7dB（已测）。
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_dual_mc as R              # noqa: E402
import diag_pbuf_bfp as D            # noqa: E402
import numpy as np                   # noqa: E402


def build_blk(n):
    out = R.SIM_DIR / f"simv_bfp_blk{n}"
    src = Path(f"/tmp/preamble_buf_blk{n}.sv")
    t = (ROOT / 'rtl/rx/frontend/preamble_buf.sv').read_text()
    t2 = t.replace('parameter integer BLK   = 64', f'parameter integer BLK   = {n}')
    assert t2 != t, "BLK param not found"
    src.write_text(t2)
    srcs = [str(src) if s == 'rtl/rx/frontend/preamble_buf.sv' else str(ROOT / s)
            for s in R.SOURCES]
    csrc = R.SIM_DIR / f"csrc_bfp_blk{n}"
    csrc.mkdir(parents=True, exist_ok=True)
    cmd = [f"{R.VCS_ENV['VCS_HOME']}/bin/vcs", "-full64", "-sverilog",
           "-timescale=1ns/1ps", "-o", str(out),
           "+incdir+" + str(ROOT / 'rtl' / 'rx')] + srcs
    r = subprocess.run(cmd, cwd=csrc, env=R.VCS_ENV, capture_output=True, text=True)
    ok = r.returncode == 0
    print(f"blk{n} build: {'ok' if ok else 'FAIL'}")
    if not ok:
        sys.stderr.write(r.stdout[-2000:])
        sys.exit(1)
    return out


def main():
    dl = D.load_dump(D.OUT / "pbuf_lin.txt")
    li, lq = dl[:, 1].astype(float), dl[:, 2].astype(float)
    print("BLK | RTL 实测 SNR | Python 模型预期")
    for n, exp in ((16, 42.8), (128, 41.7)):
        simv = build_blk(n)
        D.gen_and_run(simv, D.OUT, f"blk{n}")
        da = D.load_dump(D.OUT / f"pbuf_blk{n}.txt")
        fi, fq = da[:, 1].astype(float), da[:, 2].astype(float)
        pairs = D.aligned_snr(fi, fq, li, lq)
        lag, snr = max(pairs, key=lambda t: t[1])
        print(f"{n:4d} | {snr:6.1f} dB (lag{lag:+d}) | {exp:.1f} dB")


if __name__ == "__main__":
    main()
