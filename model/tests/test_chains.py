# -*- coding: utf-8 -*-
"""
test_chains —— 链路重构回归: 新链路库 vs 迁移前 run 脚本的原始逻辑
====================================================================

把各 run 脚本**迁移前**的帧循环逐字复制为 ``ref_*`` 函数, 与 ``chains`` 组装的
等价链路在**相同 rng 种子**下逐比特比对。任何 DIFF 都说明重构改变了数值行为。

覆盖的链形态 (对应迁移动机):
  ref_ber          简化链 + AWGN + honest 同步            (run_ber)
  ref_impair_*     简化链 + 各损伤 + 3 种同步模式          (run_impairments)
  ref_frontend     AWGN+IQ+DC+CFO → CFO估纠 + DC/IQ LS 校正 (run_frontend)
  ref_multipath_eq genie 定时 + LS 信道估计 + MMSE 均衡     (run_multipath_eq)

运行: python model/tests/test_chains.py  (需在 model/ 目录下)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

import chains
import measure
import phy_802154 as phy
from baseband import impairments as imp

PSDU_LEN = 20
N_FRAMES = 200
SYNC_WIN = phy.SPS


def _psdu(rng):
    return bytes(rng.integers(0, 256, size=PSDU_LEN).tolist())


def _bits(syms, rx):
    x = np.asarray(rx, dtype=int) ^ np.asarray(syms, dtype=int)
    return int(sum(bin(int(v)).count("1") for v in x))


# ===========================================================================
# 参照实现: 迁移前 run 脚本的原逻辑 (逐字复制)
# ===========================================================================

def ref_ber(snr, rng):
    """run_ber.run_point 原逻辑。"""
    bit_err = bit_tot = 0
    for _ in range(N_FRAMES):
        syms = phy.ppdu_symbols(_psdu(rng))
        tx = phy.modulate_oqpsk(phy.symbols_to_chips(syms))
        y = phy.add_awgn(tx, snr, rng=rng)
        mf = phy.matched_filter(y)
        p, _ = phy.correlate_preamble(mf)
        if phy.chip_peak_index(p, len(syms) * 32 - 1) >= len(mf):
            bit_err += 4 * len(syms); bit_tot += 4 * len(syms); continue
        bit_err += _bits(syms, phy.rx_despread(mf, p, len(syms)))
        bit_tot += 4 * len(syms)
    return bit_err, bit_tot


def ref_impairment(snr, rng, cfo_hz=0.0, eps=0.0, mp=None, sync="assist"):
    """run_impairments.run_config 原逻辑 (sync: genie|assist|honest)。"""
    bit_err = bit_tot = 0
    for _ in range(N_FRAMES):
        syms = phy.ppdu_symbols(_psdu(rng))
        tx = phy.modulate_oqpsk(phy.symbols_to_chips(syms))
        if mp is not None:
            tx = imp.add_multipath(tx, mp["gains"], mp["delays"])
        y = phy.add_awgn(tx, snr, rng=rng)
        if cfo_hz:
            y = imp.add_cfo(y, cfo_hz)
        if eps:
            y = imp.add_timing_offset(y, eps)
        mf = phy.matched_filter(y)
        p, _, corr = phy.correlate_preamble(mf, return_corr=True)
        if sync == "genie":
            p = 0
        elif sync == "assist":
            lo, hi = max(0, -SYNC_WIN), min(len(corr) - 1, SYNC_WIN)
            p = lo + int(np.argmax(corr[lo:hi + 1]))
        if phy.chip_peak_index(p, len(syms) * 32 - 1) >= len(mf):
            bit_err += 4 * len(syms); bit_tot += 4 * len(syms); continue
        bit_err += _bits(syms, phy.rx_despread(mf, p, len(syms)))
        bit_tot += 4 * len(syms)
    return bit_err, bit_tot


def ref_frontend(rng, use_cfo=False, use_dciq=False, correct=True):
    """run_frontend.run_scenario 原逻辑 (损伤顺序: AWGN→IQ→DC→CFO)。"""
    SNR = -1.0
    CFO_HZ, DC_I, DC_Q = 50e3, 0.35, 0.30
    IQ_GAIN_DB, IQ_PHASE_DEG = 2.0, 10.0
    bit_err = bit_tot = 0
    for _ in range(N_FRAMES):
        syms = phy.ppdu_symbols(_psdu(rng))
        tx = phy.modulate_oqpsk(phy.symbols_to_chips(syms))
        y = phy.add_awgn(tx, SNR, rng=rng)
        if use_dciq:
            y = imp.add_iq_imbalance(y, IQ_GAIN_DB, IQ_PHASE_DEG)
            y = imp.add_dc_offset(y, DC_I, DC_Q)
        if use_cfo:
            y = imp.add_cfo(y, CFO_HZ)
        if use_cfo and correct:
            f_c = phy.estimate_cfo_coarse(phy.matched_filter(y))
            f_f, _ = phy.estimate_cfo_fine(y, f_c)
            y = phy.correct_cfo(y, f_f)
        mf = phy.matched_filter(y)
        p, _, corr = phy.correlate_preamble(mf, return_corr=True)
        if correct:
            p = int(np.argmax(corr[:max(1, 8 * phy.SPS)]))
        if phy.chip_peak_index(p, len(syms) * 32 - 1) >= len(mf):
            bit_err += 4 * len(syms); bit_tot += 4 * len(syms); continue
        if use_dciq and correct:
            alpha, beta, d = phy.estimate_dc_iq(phy.extract_preamble_chips(mf, p),
                                                phy.known_preamble_chips(derail=False))
            rx = phy.correct_frame_chips(mf, p, len(syms), alpha, beta, d)
        else:
            rx = phy.rx_despread(mf, p, len(syms))
        bit_err += _bits(syms, rx)
        bit_tot += 4 * len(syms)
    return bit_err, bit_tot


def ref_multipath_eq(snr, rng, mp, mode, L=6):
    """run_multipath_eq.run_config 原逻辑 (genie 定时 p=0)。"""
    es_h = float(np.sum(phy.half_sine(phy.SPS) ** 2))
    nv = (es_h / 10 ** (snr / 10)) * es_h
    bit_err = bit_tot = 0
    for _ in range(N_FRAMES):
        syms = phy.ppdu_symbols(_psdu(rng))
        tx = phy.modulate_oqpsk(phy.symbols_to_chips(syms))
        if mp is not None:
            tx = imp.add_multipath(tx, mp["gains"], mp["delays"])
        mf = phy.matched_filter(phy.add_awgn(tx, snr, rng=rng))
        chips = phy.sample_chips(mf, 0, len(syms) * 32)
        if mode == "eq":
            h = phy.estimate_channel_ls(phy.sample_chips(mf, 0, phy.PREAMBLE_SYMS * 32),
                                        phy.preamble_chips(), L)
            rx = phy.despread_coherent(phy.equalize_mmse(chips, h, nv)).astype(int)
        elif mode == "base_coh":
            rx = phy.despread_coherent(chips).astype(int)
        else:
            rx = phy.despread_chips(chips).astype(int)
        bit_err += _bits(syms, rx)
        bit_tot += 4 * len(syms)
    return bit_err, bit_tot


def ref_ber_early_stop(snr, rng, min_errs=100, max_pkts=3000):
    """run_ber.run_point 原逻辑 (含早停)。

    陷阱: 原实现在 sync_fail 分支 ``continue``, 因而跳过了早停判定 ——
    本函数逐字保留这一点, 用来锁定迁移后的等价性。
    """
    bit_err = bit_tot = 0
    for _ in range(max_pkts):
        syms = phy.ppdu_symbols(_psdu(rng))
        tx = phy.modulate_oqpsk(phy.symbols_to_chips(syms))
        y = phy.add_awgn(tx, snr, rng=rng)
        mf = phy.matched_filter(y)
        p, _ = phy.correlate_preamble(mf)
        if phy.chip_peak_index(p, len(syms) * 32 - 1) >= len(mf):
            bit_err += 4 * len(syms); bit_tot += 4 * len(syms); continue
        bit_err += _bits(syms, phy.rx_despread(mf, p, len(syms)))
        bit_tot += 4 * len(syms)
        if bit_err >= min_errs:
            break
    return bit_err, bit_tot


# ===========================================================================
# 新链路库的等价组装
# ===========================================================================

def new_ber(snr, rng):
    be = bt = 0
    for _ in range(N_FRAMES):
        sig = chains.simulate(_psdu(rng), [chains.awgn(snr, rng)],
                              full=False, sync="honest")
        e, t = measure.frame_bit_errors(sig); be += e; bt += t
    return be, bt


def new_ber_early_stop(snr, rng, min_errs=100, max_pkts=3000):
    """对应 ref_ber_early_stop 的新实现 (含「同步失败帧不触发早停」的历史行为)。"""
    chain = chains.link([chains.awgn(snr, rng)], full=False, sync="honest")
    bit_err = bit_tot = 0
    for _ in range(max_pkts):
        sig = chain.run(payload=_psdu(rng))
        e, t = measure.frame_bit_errors(sig)
        bit_err += e; bit_tot += t
        if sig.meta.get("sync_fail"):
            continue
        if bit_err >= min_errs:
            break
    return bit_err, bit_tot


def new_impairment(snr, rng, cfo_hz=0.0, eps=0.0, mp=None, sync="assist"):
    stages = []
    if mp is not None:
        stages.append(imp.multipath_stage(mp["gains"], mp["delays"]))
    stages.append(chains.awgn(snr, rng))
    if cfo_hz:
        stages.append(imp.cfo_stage(cfo_hz))
    if eps:
        stages.append(imp.timing_stage(eps))
    mode = {"assist": "window", "genie": "genie", "honest": "honest"}[sync]
    be = bt = 0
    for _ in range(N_FRAMES):
        sig = chains.simulate(_psdu(rng), stages, full=False,
                              sync=mode, win=SYNC_WIN)
        e, t = measure.frame_bit_errors(sig); be += e; bt += t
    return be, bt


def new_frontend(rng, use_cfo=False, use_dciq=False, correct=True):
    stages = [chains.awgn(-1.0, rng)]
    if use_dciq:                                   # 与 ref 同序: IQ 再 DC
        stages += [imp.iq_stage(2.0, 10.0), imp.dc_stage(0.35, 0.30)]
    if use_cfo:
        stages.append(imp.cfo_stage(50e3))
    be = bt = 0
    for _ in range(N_FRAMES):
        sig = chains.simulate(
            _psdu(rng), stages, full=False,
            cfo=("two_stage" if (use_cfo and correct) else None),
            sync=("window" if correct else "honest"), win=8 * phy.SPS - 1,
            dc_iq=(use_dciq and correct))
        e, t = measure.frame_bit_errors(sig); be += e; bt += t
    return be, bt


def new_multipath_eq(snr, rng, mp, mode, L=6):
    stages = ([imp.multipath_stage(mp["gains"], mp["delays"])] if mp else [])
    stages.append(chains.awgn(snr, rng))
    be = bt = 0
    for _ in range(N_FRAMES):
        sig = chains.simulate(
            _psdu(rng), stages, full=False, sync="genie",
            n_taps=(L if mode == "eq" else 0),
            noise_var=chains.eq_noise_var(snr),
            coherent=(mode in ("eq", "base_coh")))
        e, t = measure.frame_bit_errors(sig); be += e; bt += t
    return be, bt


# ===========================================================================
# 驱动
# ===========================================================================

def main():
    phy._selftest()
    results = []

    def check(tag, ref, new):
        ok = ref == new
        results.append(ok)
        print(f"  {'MATCH' if ok else 'DIFF <<<'}  {tag:<34} 旧={ref}  新={new}")
    print("== 1. run_ber 语义 (简化链 + AWGN + honest 同步) ==")
    for snr in (-4.0, -3.0, -2.5, -2.0, -1.0):
        check(f"awgn {snr:+.1f} dB",
              ref_ber(snr, np.random.default_rng(4242)),
              new_ber(snr, np.random.default_rng(4242)))
    # 早停路径 (run_ber 实际使用的模式): 锁定「同步失败帧不触发早停」的历史行为
    for snr in (-3.0, -2.0):
        check(f"awgn {snr:+.1f} dB + 早停",
              ref_ber_early_stop(snr, np.random.default_rng(2026)),
              new_ber_early_stop(snr, np.random.default_rng(2026)))

    print("== 2. run_impairments 语义 (3 种同步模式 + 损伤) ==")
    check("genie + CFO 50kHz",
          ref_impairment(-1.0, np.random.default_rng(77), cfo_hz=50e3, sync="genie"),
          new_impairment(-1.0, np.random.default_rng(77), cfo_hz=50e3, sync="genie"))
    check("honest + CFO 20kHz",
          ref_impairment(-1.0, np.random.default_rng(77), cfo_hz=20e3, sync="honest"),
          new_impairment(-1.0, np.random.default_rng(77), cfo_hz=20e3, sync="honest"))
    check("assist + eps 0.3chip",
          ref_impairment(-1.0, np.random.default_rng(77), eps=0.3, sync="assist"),
          new_impairment(-1.0, np.random.default_rng(77), eps=0.3, sync="assist"))
    check("assist + eps 0.5chip",
          ref_impairment(-1.0, np.random.default_rng(77), eps=0.5, sync="assist"),
          new_impairment(-1.0, np.random.default_rng(77), eps=0.5, sync="assist"))
    for nm in ("los_2tap", "indoor_3tap", "bad_3tap"):
        check(f"assist + multipath {nm}",
              ref_impairment(-1.0, np.random.default_rng(77), mp=imp.SCENARIOS[nm]),
              new_impairment(-1.0, np.random.default_rng(77), mp=imp.SCENARIOS[nm]))

    print("== 3. run_frontend 语义 (CFO 估纠 + DC/IQ LS 校正) ==")
    for tag, kw in (("CFO, corrected", dict(use_cfo=True)),
                    ("CFO, not corrected", dict(use_cfo=True, correct=False)),
                    ("DC+IQ, corrected", dict(use_dciq=True)),
                    ("DC+IQ, not corrected", dict(use_dciq=True, correct=False)),
                    ("CFO+DC+IQ, corrected", dict(use_cfo=True, use_dciq=True))):
        check(tag, ref_frontend(np.random.default_rng(555), **kw),
              new_frontend(np.random.default_rng(555), **kw))

    print("== 4. run_multipath_eq 语义 (genie + LS 估计 + MMSE 均衡) ==")
    for mode in ("base", "base_coh", "eq"):
        check(f"los_2tap / {mode}",
              ref_multipath_eq(-1.0, np.random.default_rng(31), imp.SCENARIOS["los_2tap"], mode),
              new_multipath_eq(-1.0, np.random.default_rng(31), imp.SCENARIOS["los_2tap"], mode))
        check(f"indoor_3tap / {mode}",
              ref_multipath_eq(-1.0, np.random.default_rng(31), imp.SCENARIOS["indoor_3tap"], mode),
              new_multipath_eq(-1.0, np.random.default_rng(31), imp.SCENARIOS["indoor_3tap"], mode))

    print("== 5. CFO 估计分支覆盖 (stage_cfo_correct 的三个 est) ==")
    for est in ("two_stage", "periodogram", "preamble_diff"):
        sig = chains.simulate(_psdu(np.random.default_rng(5)),
                              [chains.awgn(-1.0, np.random.default_rng(1)),
                               imp.cfo_stage(50e3)],
                              full=False, sync="honest", cfo=est)
        est_khz = sig.meta["cfo_est"] / 1e3
        good = abs(est_khz - 50.0) < 2.0
        results.append(good)
        print(f"  {'OK  ' if good else 'FAIL'}  cfo={est:<15} "
              f"估计 {est_khz:.2f} kHz (真值 50.00)")

    print()
    n_pass = sum(results)
    print(f"RESULT: {n_pass}/{len(results)} MATCH")
    if n_pass != len(results):
        print("FAIL: 重构改变了数值行为, 需逐项排查")
        sys.exit(1)
    print("PASS: 新链路库与迁移前脚本逐比特一致")


if __name__ == "__main__":
    main()
