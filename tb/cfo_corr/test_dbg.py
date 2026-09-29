"""临时调试: 读 cordic 迭代结束时的 x/y/z, 判断是收敛问题还是输出编码问题。"""
import sys
from pathlib import Path
import cocotb
import numpy as np
from cocotb.triggers import FallingEdge

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_cfo_corr import build_frame, drive, reset, sgn, N_PRE_SMP

def sgn_w(v, w):
    v = int(v)
    return v - (1 << w) if v >= (1 << (w - 1)) else v

@cocotb.test()
async def dbg_cordic(dut):
    await reset(dut)
    rng = np.random.default_rng(11)
    psdu = bytes(rng.integers(0, 256, size=8).tolist())
    i_mf, q_mf = build_frame(psdu, 50e3)
    dut.est_start.value = 1
    await FallingEdge(dut.clk)
    dut.est_start.value = 0
    await drive(dut, i_mf, q_mf, N_PRE_SMP)
    for _ in range(80):
        await FallingEdge(dut.clk)
        if dut.est_done.value == 1:
            break
    C = dut.u_est.u_cord
    bi = sgn_w(dut.u_est.bi.value, 48); bq = sgn_w(dut.u_est.bq.value, 48)
    cx = sgn_w(C.x.value, 28); cy = sgn_w(C.y.value, 28); cz = sgn_w(C.z.value, 27)
    print(f"输入: bi={bi} bq={bq}  期望角度={np.degrees(np.arctan2(bq,bi)):.3f} deg")
    print(f"  -> 期望 cord_phi = {int(round(np.arctan2(bq,bi)/(2*np.pi)*(1<<24)))}")
    print(f"CORDIC 结束: x={cx} y={cy} z={cz}  it={int(C.it.value)} busy={int(C.busy.value)}")
    print(f"  z 对应角度 = {cz/(1<<24)*360:.3f} deg   残余 y/x 角度 = {np.degrees(np.arctan2(cy,cx)):.4f} deg")
    print(f"  实际 cord_phi = {sgn_w(dut.u_est.u_cord.phase.value, 24)}")
