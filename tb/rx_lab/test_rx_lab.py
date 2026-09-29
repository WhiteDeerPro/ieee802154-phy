# -*- coding: utf-8 -*-
"""
rx_lab —— RX 全链数据导出实验（与 tb/rtl_lab 对称）
=====================================================

激励: 黄金模型 TX 波形 ×8 + AWGN → clamp 到 12bit（模拟 ADC）→ 喂给 RTL RX 全链
      ADC → MF → preamble_sync → despreader → rx_deframer → PSDU

只做三件事, 不做信号处理:
    1. 驱动并采集 RX 链的各阶段输出（MF 输出 / 码片 / 符号 / PSDU 字节）;
    2. **用断言守住正确性** —— PSDU 逐字节、FCS、同步检出、帧结束;
    3. 导出到 ``model/out/rx_lab/``, 交给 ``model/experiments/run_rx_lab.py``
       做模型侧对照与可视化。

顶层用 ``rx_lab_top.sv``（纯透传 + $dumpfile/$dumpvars）, 因此仿真同时产出
``sim_build/rx_lab.vcd``, 含各阶段内部 wire, 可直接用 GTKWave/Verdi 看 ——
自动加载见 ``tb/rx_lab/gtkwave_auto.tcl``。
"""
import sys
from pathlib import Path

import cocotb
import numpy as np
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer

MODEL = Path(__file__).resolve().parents[2] / "model"
sys.path.insert(0, str(MODEL))
import phy_802154 as phy                                       # noqa: E402

OUT = MODEL / "out" / "rx_lab"
GAP, SCALE, NOISE, TAIL = 400, 8, 60.0, 600
PH_THRESH, SFD_THRESH = 2 * 10 ** 11, 3 * 10 ** 13
SHR_SYMBOLS = 10
ADC_W = 12
ADC_MAX = (1 << (ADC_W - 1)) - 1
ADC_MIN = -(1 << (ADC_W - 1))
PSDU_LEN = 127                     # 802.15.4 最大包, 也把 VCD 做够大
SEED = 2026


def adc_q(v):
    """模拟 ADC: 四舍五入 + 饱和到 12bit 有符号。"""
    return max(ADC_MIN, min(ADC_MAX, int(round(v))))


@cocotb.test()
async def rx_lab_capture(dut):
    cocotb.start_soon(Clock(dut.clk, 62.5, unit="ns").start())
    for sig, v in (("i_in", 0), ("q_in", 0), ("dv_in", 0),
                   ("ph_thresh", PH_THRESH), ("sfd_thresh", SFD_THRESH)):
        getattr(dut, sig).value = v
    dut.rst_n.value = 0
    await Timer(200, unit="ns")
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1

    rng = np.random.default_rng(SEED)
    psdu = bytes(rng.integers(0, 256, size=PSDU_LEN).tolist())
    syms_tx = phy.tx_symbols(psdu)
    i_f, q_f = phy.modulate_oqpsk_fixed(syms_tx.tolist())

    # ---- 激励: TX 波形 ×8 + AWGN, 前后加静默, 全部经 ADC 量化 ----
    raw_i = [adc_q(NOISE * rng.standard_normal()) for _ in range(GAP)]
    raw_q = [adc_q(NOISE * rng.standard_normal()) for _ in range(GAP)]
    for k in range(len(i_f)):
        raw_i.append(adc_q(i_f[k] * SCALE + NOISE * rng.standard_normal()))
        raw_q.append(adc_q(q_f[k] * SCALE + NOISE * rng.standard_normal()))
    raw_i += [adc_q(NOISE * rng.standard_normal()) for _ in range(TAIL)]
    raw_q += [adc_q(NOISE * rng.standard_normal()) for _ in range(TAIL)]

    exp_psdu, exp_ok, exp_len = phy.rx_deframe_symbols(syms_tx[SHR_SYMBOLS:])
    assert exp_ok and exp_psdu == psdu, "黄金镜像自检失败 (期望 PSDU/FCS 不符)"

    # ---- 采集: PSDU 字节流 + 同步信息 + 各阶段中间量 ----
    got_bytes, valid_cnt = [], 0
    mf_i_log, chip_i_log, sym_log = [], [], []
    detect_seen = locked_phase = None
    frame_done = 0
    mf_dv_cnt = chip_dv_cnt = sym_dv_cnt = 0

    async def monitor():
        nonlocal valid_cnt, detect_seen, locked_phase, frame_done
        nonlocal mf_dv_cnt, chip_dv_cnt, sym_dv_cnt
        while n_driven[0] < total or not done[0]:
            await FallingEdge(dut.clk)
            if int(dut.data_valid.value):
                got_bytes.append(int(dut.data_out.value))
                valid_cnt += 1
            if int(dut.detect.value) and detect_seen is None:
                detect_seen = 1
                locked_phase = int(dut.locked_phase.value)
            if int(dut.frame_done.value):
                frame_done = 1
            # 中间量: 只在帧附近记录, 免得存几十万拍
            if recording[0]:
                mf_i_log.append(int(dut.u_dut.mf_i.value))
                chip_i_log.append(int(dut.u_dut.chip_i.value))
                sym_log.append(int(dut.u_dut.sym.value))
                mf_dv_cnt += int(dut.u_dut.mf_dv.value)
                chip_dv_cnt += int(dut.u_dut.chip_dv.value)
                sym_dv_cnt += int(dut.u_dut.sym_dv.value)

    total = len(raw_i)
    n_driven = [0]
    done = [False]
    recording = [True]              # 全程录制 (约 7 万拍 x 3 路, 压缩后 ~1MB)
    cocotb.start_soon(monitor())

    for k in range(total):
        await FallingEdge(dut.clk)
        dut.i_in.value = raw_i[k]
        dut.q_in.value = raw_q[k]
        dut.dv_in.value = 1
        n_driven[0] = k + 1
    dut.dv_in.value = 0

    # 等 frame_done (或超时)
    extra = 0
    while not frame_done and extra < 20000:
        await FallingEdge(dut.clk)
        extra += 1
    done[0] = True
    await Timer(100, unit="ns")

    got = bytes(got_bytes)

    # ---------------- 断言 ----------------
    assert detect_seen, "RX 链未检出前导 (detect 从未拉高)"
    assert locked_phase is not None, "locked_phase 未捕获"
    assert frame_done, f"未收到 frame_done (等了 {extra} 拍)"
    assert int(dut.fcs_ok.value) == 1, "RTL FCS 校验失败"
    assert valid_cnt == exp_len, f"输出字节数 {valid_cnt} != PHR 声明长度 {exp_len}"
    assert got == exp_psdu, \
        f"PSDU 不一致: 首个差异在第 {next((i for i,(a,b) in enumerate(zip(got, exp_psdu)) if a!=b), -1)} 字节"
    assert mf_dv_cnt > 0 and chip_dv_cnt > 0 and sym_dv_cnt > 0, "中间量未采到有效数据"

    # ---------------- 导出 ----------------
    OUT.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        OUT / "rx_lab.npz",
        psdu=np.frombuffer(got, dtype=np.uint8),
        exp_psdu=np.frombuffer(exp_psdu, dtype=np.uint8),
        psdu_len=np.int32(exp_len),
        fcs_ok=np.int32(1),
        detect=np.int32(detect_seen),
        locked_phase=np.int32(locked_phase),
        valid_cnt=np.int32(valid_cnt),
        mf_i=np.asarray(mf_i_log, dtype=np.int64),
        chip_i=np.asarray(chip_i_log, dtype=np.int64),
        sym=np.asarray(sym_log, dtype=np.int64),
        mf_dv_cnt=np.int32(mf_dv_cnt),
        chip_dv_cnt=np.int32(chip_dv_cnt),
        sym_dv_cnt=np.int32(sym_dv_cnt),
        scale=np.int32(SCALE), noise=np.float64(NOISE),
        adc_bits=np.int32(ADC_W),
        ph_thresh=np.int64(PH_THRESH), sfd_thresh=np.int64(SFD_THRESH),
        note="RX 全链导出: ADC 12bit 输入 → MF → sync → despread → deframe; "
             "中间量取自 e2e_top 内部 wire, 已对齐到采集起点",
    )
    hdr = "idx,mf_i,chip_i,sym\n"
    rows = "".join(f"{k},{mf_i_log[k]},{chip_i_log[k]},{sym_log[k]}\n"
                   for k in range(min(512, len(mf_i_log))))
    (OUT / "rx_lab_head.csv").write_text(hdr + rows, encoding="utf-8")

    dut._log.info(
        f"rx_lab_capture PASS: PSDU {valid_cnt}B 逐字节一致, FCS OK, "
        f"detect=1 (locked_phase={locked_phase}), 中间量记录 {len(mf_i_log)} 拍 "
        f"(mf_dv/chip_dv/sym_dv = {mf_dv_cnt}/{chip_dv_cnt}/{sym_dv_cnt}) -> 已导出 {OUT}")
