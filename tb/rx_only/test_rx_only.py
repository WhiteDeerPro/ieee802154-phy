"""rx_only: 三段式 RX 全程验证 + 同台对照 —— ①tb 决策码流/场景文件 ②信道合成
③adc 注入。双 case: A) 标准合成（同 mc_gen 口径）; B) 场景 snr20 的 mem.bin 前段。"""
import sys
from pathlib import Path

import cocotb
import numpy as np
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, RisingEdge, Timer

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tb" / "rx_chain_e2e"))
import phy_802154 as phy        # noqa: E402
import mc_gen                   # noqa: E402

FS = 16e6
SCALE = 8.0
SNR_DB = 20.0
CFO_HZ = 100e3


def synth_stream(psdu: bytes, seed=11):
    """①决策码流 → ②信道合成: gap + 帧(×scale+AWGN) + tail + 续噪声 + CFO。"""
    rng = np.random.default_rng(seed)
    sigma = mc_gen.noise_sigma(SCALE, SNR_DB)
    syms = phy.tx_symbols(psdu).tolist()
    i_f, q_f = phy.modulate_oqpsk_fixed(syms)
    n = max(len(i_f), len(q_f))
    i_f = np.pad(np.asarray(i_f, dtype=float), (0, n - len(i_f)))
    q_f = np.pad(np.asarray(q_f, dtype=float), (0, n - len(q_f)))
    i_f = i_f * SCALE + rng.standard_normal(n) * sigma
    q_f = q_f * SCALE + rng.standard_normal(n) * sigma
    i_gap = rng.standard_normal(500) * sigma
    q_gap = rng.standard_normal(500) * sigma
    i_tail = rng.standard_normal(600) * sigma
    q_tail = rng.standard_normal(600) * sigma
    i_ext = rng.standard_normal(100000) * sigma
    q_ext = rng.standard_normal(100000) * sigma
    i_all = np.concatenate([i_gap, i_f, i_tail, i_ext])
    q_all = np.concatenate([q_gap, q_f, q_tail, q_ext])
    rot = np.exp(2j * np.pi * CFO_HZ * np.arange(len(i_all)) / FS)
    z = (i_all + 1j * q_all) * rot
    return (np.clip(np.round(z.real), -2048, 2047).astype(int),
            np.clip(np.round(z.imag), -2048, 2047).astype(int))


def load_scene_mem(tag='snr20', n=30000):
    """场景 mem.bin 前 n 采样: i=[11:0], q=[23:12]（12 位有符号）。"""
    raw = np.fromfile(ROOT / 'model' / 'out' / 'dual_mc' / tag / 'mem.bin',
                      dtype='>u4', count=n)      # 注意: mc_gen 以 >u4（大端）写盘
    def s12(x):
        x = x.astype(np.int64) & 0xFFF
        return np.where(x >= 2048, x - 4096, x).astype(int)
    return s12(raw), s12(raw >> 12)


async def run_stream(dut, i_all, q_all, tag):
    """③ adc 注入 + 观测; 返回统计 dict。"""
    dut.adc_i.value = 0
    dut.adc_q.value = 0
    dut.adc_dv.value = 0
    dut.rst_n.value = 0
    for _ in range(16):                      # 与 dual_mc_tb 对齐: 复位 16 拍
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await FallingEdge(dut.clk)               # 释放后 negedge 起播

    got = []
    fd = 0
    ph_seen = set()
    sfdE_max = 0
    fs_seen = 0
    det_first = -1
    for k in range(len(i_all)):
        dut.adc_i.value = int(i_all[k])
        dut.adc_q.value = int(q_all[k])
        dut.adc_dv.value = 1
        await FallingEdge(dut.clk)
        if int(dut.data_valid.value) == 1:
            got.append(int(dut.data.value))
        if int(dut.frame_done.value) == 1:
            fd = 1
        ph_seen.add(int(dut.phase_out.value))
        try:
            e = int(dut.u_rx.u_be_a.u_sfd.sfd_E.value)
            if e > sfdE_max:
                sfdE_max = e
            if int(dut.u_rx.u_be_a.frame_start.value) == 1:
                fs_seen = 1
            if det_first < 0 and int(dut.detect.value) == 1:
                det_first = k
        except Exception:
            pass
    for _ in range(64):
        await FallingEdge(dut.clk)
    return dict(tag=tag, fd=fd, fcs=int(dut.fcs_ok.value),
                any_ok=int(dut.any_fcs_ok.value), got=got,
                ph=sorted(ph_seen)[:8], sfdE=sfdE_max, fs=fs_seen,
                det=det_first, plen=int(dut.psdu_len.value))


def report(st, psdu=None):
    line = (f'[{st["tag"]}] sfd_E={st["sfdE"]} | frame_start={st["fs"]} | '
            f'detect 首升 k={st["det"]} | phase={st["ph"]} | '
            f'done={st["fd"]} fcs={st["fcs"]} any={st["any_ok"]} | '
            f'收 {len(st["got"])}B len={st["plen"]}')
    print(line, flush=True)
    if psdu is not None and st['got'][:len(psdu)] == list(psdu):
        print('  → payload 一致 ✓')


@cocotb.test()
async def case_a_synth(dut):
    cocotb.start_soon(Clock(dut.clk, 62.5, unit="ns").start())
    psdu = bytes(range(20))
    i_all, q_all = synth_stream(psdu)
    st = await run_stream(dut, i_all, q_all, 'A 标准合成')
    report(st, psdu)
    assert st['any_ok'] == 1 and st['got'][:len(psdu)] == list(psdu), 'case A 失败'


@cocotb.test()
async def case_b_scene(dut):
    cocotb.start_soon(Clock(dut.clk, 62.5, unit="ns").start())
    i_all, q_all = load_scene_mem('snr20', 30000)
    st = await run_stream(dut, i_all, q_all, 'B 场景 mem')
    report(st)
    assert st['any_ok'] == 1 or st['fd'] == 1, 'case B 失败（场景流也未解出）'
