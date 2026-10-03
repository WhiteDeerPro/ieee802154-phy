"""zigbee_top 端到端回环: TX 发一帧 → (数字理想回环) → RX 解码 (fcs_ok + 载荷一致)。"""
import sys
from pathlib import Path

import cocotb
import numpy as np
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer


@cocotb.test()
async def loopback_frame(dut):
    cocotb.start_soon(Clock(dut.clk, 62.5, unit="ns").start())
    dut.tx_start.value = 0
    dut.tx_len.value = 0
    dut.tx_data.value = 0
    dut.tx_data_valid.value = 0
    dut.rst_n.value = 0
    await Timer(200, unit="ns")
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1
    for _ in range(32):
        await FallingEdge(dut.clk)

    rng = np.random.default_rng(3)
    psdu = bytes(rng.integers(0, 256, size=20).tolist())

    # ---- 发帧 ----
    while int(dut.tx_busy.value) == 1:
        await FallingEdge(dut.clk)
    dut.tx_len.value = len(psdu)
    dut.tx_start.value = 1
    await FallingEdge(dut.clk)
    dut.tx_start.value = 0
    sent = 0
    tx_peak = 0
    while int(dut.tx_done.value) == 0:
        await FallingEdge(dut.clk)
        if int(dut.tx_smp_dv.value) == 1:
            v = int(dut.tx_i.value)
            v = v - 4096 if v >= 2048 else v
            tx_peak = max(tx_peak, abs(v))
        if int(dut.tx_data_ready.value) == 1 and sent < len(psdu):
            dut.tx_data.value = psdu[sent]
            dut.tx_data_valid.value = 1
            sent += 1
        else:
            dut.tx_data_valid.value = 0
    dut.tx_data_valid.value = 0
    print(f'TX 完成: sent={sent}/{len(psdu)} | tx_i 峰值(发送期) = {tx_peak}')

    # ---- 等 RX（轮询; 帧约 5.6ms=90K 拍, 给足 margin）----
    got_a, got_b = [], []
    fd_a = fd_b = 0
    det_seen = 0
    ph_seen = set()
    chip_dv_seen = 0
    fs_seen = 0
    desp_dv_seen = 0
    sfdE_max = 0
    def s12(raw, bits=12):
        raw = int(raw) & ((1 << bits) - 1)
        return raw - (1 << bits) if raw >= (1 << (bits - 1)) else raw
    mf_pk = chip_pk = rota_pk = 0
    scan_seen = set()
    armed_seen = pend_seen = pdrise_seen = pd_rst_seen = 0
    detect_first = -1
    t_abs = 0
    for _ in range(140000):
        await FallingEdge(dut.clk)
        if int(dut.rx_data_valid_a.value) == 1:
            got_a.append(int(dut.rx_data_a.value))
        if int(dut.rx_data_valid_b.value) == 1:
            got_b.append(int(dut.rx_data_b.value))
        if int(dut.rx_frame_done_a.value) == 1:
            fd_a = 1
        if int(dut.rx_frame_done_b.value) == 1:
            fd_b = 1
        if int(dut.rx_detect.value) == 1:
            det_seen = 1
        ph_seen.add(int(dut.rx_phase_out.value))
        try:
            if int(dut.u_rx.u_fe.chip_dv.value) == 1:
                chip_dv_seen = 1
            if int(dut.u_rx.u_be_a.frame_start.value) == 1:
                fs_seen = 1
            if int(dut.u_rx.u_be_a.u_desp.sym_dv.value) == 1:
                desp_dv_seen = 1
            e = int(dut.u_rx.u_be_a.u_sfd.sfd_E.value)
            if e > sfdE_max:
                sfdE_max = e
            m = abs(s12(int(dut.u_rx.u_fe.mf_i.value), 16))
            if m > mf_pk:
                mf_pk = m
            c = abs(s12(int(dut.u_rx.u_fe.chip_i.value), 16))
            if c > chip_pk:
                chip_pk = c
            r = abs(s12(int(dut.u_rx.rot_a_i.value), 16))
            if r > rota_pk:
                rota_pk = r
            scan_seen.add(int(dut.u_rx.u_fe.scan_phase.value))
        except Exception:
            pass
        t_abs += 1
        try:
            if int(dut.u_rx.u_fe.latch_armed.value) == 1:
                armed_seen = 1
        except Exception:
            pass
        try:
            if int(dut.u_rx.u_fe.latch_pend.value) == 1:
                pend_seen = 1
        except Exception:
            pass
        try:
            if int(dut.u_rx.u_fe.g_scan.pd_rise.value) == 1:
                if pdrise_seen == 0:
                    print(f'  [pd_rise 首次 @ t={t_abs}]')
                pdrise_seen = 1
        except Exception:
            pass
        try:
            if pd_rst_seen == 0 and int(dut.u_rx.u_fe.pd_rst.value) == 1:
                pd_rst_seen = 1
                print(f'  [pd_rst 首次 @ t={t_abs}]')
        except Exception:
            pass
        try:
            if detect_first < 0 and int(dut.rx_detect.value) == 1:
                detect_first = t_abs
                print(f'  [detect 首次升 @ t={t_abs}]')
        except Exception:
            pass
        if fd_a and fd_b:
            break
    print(f'诊断: detect 曾升={det_seen} | phase 出现过 {sorted(ph_seen)[:8]}...')
    print(f'诊断2: chip_dv 曾动={chip_dv_seen} | frame_start 曾升={fs_seen} | '
          f'desp.sym_dv 曾动={desp_dv_seen} | sfd_E 峰值={sfdE_max} (门限 3e13)')
    print(f'诊断3(逐级峰值): mf_i={mf_pk} | chip_i(去交错)={chip_pk} | '
          f'rot_a_i(A 消旋后)={rota_pk}')
    try:
        print(f'诊断4(相位): scan_phase 最终={int(dut.u_rx.u_fe.scan_phase.value)} | '
              f'phase_fix={int(dut.u_rx.u_fe.phase_fix.value)} | '
              f'scan_phase 出现过 {sorted(scan_seen)[:20]}')
    except Exception as e:
        print('诊断4: 不可读', e)
    print(f'诊断5(latch 链): pd_rst 曾={pd_rst_seen} | pd_rise 曾={pdrise_seen} | '
          f'latch_armed 曾={armed_seen} | latch_pend 曾={pend_seen} | detect 首升 t={detect_first}')

    fcs_a = int(dut.rx_fcs_ok_a.value)
    fcs_b = int(dut.rx_fcs_ok_b.value)
    any_ok = int(dut.rx_any_fcs_ok.value)
    print(f'RX: A len={len(got_a)} fcs={fcs_a} done={fd_a} | B len={len(got_b)} fcs={fcs_b} done={fd_b}')
    print(f'any_fcs_ok={any_ok} phase={int(dut.rx_phase_out.value)} detect={int(dut.rx_detect.value)}')

    match_a = bytes(got_a[:len(psdu)]) == psdu
    match_b = bytes(got_b[:len(psdu)]) == psdu
    print(f'payload match: A={match_a} B={match_b}')
    assert sent == len(psdu), 'PSDU 未送完'
    assert any_ok == 1, '回环失败: 无通道解出帧'
    assert match_a or match_b, '回环失败: 载荷不一致'
    print('=== zigbee_top 回环 PASS: TX→RX 帧一致 ===')
