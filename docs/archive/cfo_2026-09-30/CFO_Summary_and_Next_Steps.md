# CFO Correction: Summary and Next Steps

## What Was Done

### 1. Comprehensive Analysis Documents Created

📄 **`CFO_Analysis_and_Solution.md`** (10 sections, ~200 lines)
- Physics of CFO across three time scales (MF support, symbol, preamble)
- Two failure modes: sync collapse (dominant) vs. decision degradation
- Joint estimation algorithm explained in detail
- Three critical tricks learned from踩坑 (ISI removal, span-2 differential, quality check)
- Two-block RTL architecture (`cfo_est.sv` + `cfo_rot.sv`)
- Measured performance: 59-65× BER improvement at 50-96 kHz CFO
- Literature context: industry avoids vs. this project corrects
- CFO vs. SFO comparison (tighter tolerance, easier to fix)
- Open questions for future work

📄 **`CFO_Enhanced_Block_Design.md`** (6 enhancements + priority tiers)
- **Enhancement 1**: Decision-directed phase tracking loop (for long packets)
- **Enhancement 2**: Multi-frame CFO learning (per-node memory)
- **Enhancement 3**: Coarse-fine two-stage estimation (4× energy savings)
- **Enhancement 4**: Diagnostic outputs (high value, low cost) ✅ **Recommended**
- **Enhancement 5**: Adaptive estimation window (robustness to trigger jitter) ✅ **Recommended**
- **Enhancement 6**: FFT-based estimation (research direction, not needed here)
- Implementation priority tiers (Tier 1: immediate, Tier 2: if battery-powered, Tier 3: skip)

### 2. New RTL Module Created

📝 **`rtl/rx/cfo_est_diag.sv`** — Enhanced estimator with visibility
- Extends `cfo_est.sv` with diagnostic outputs (no functional changes to algorithm)
- **New outputs**:
  - `Q_cand[0:7]` — Phase consistency for all 8 candidates (debugging sync failures)
  - `peak_mag2_raw` / `peak_mag2_corrected` — Peak magnitude before/after CFO correction
  - `p_second`, `Q_second` — Runner-up candidate (ambiguity detection → multipath)
  - `high_confidence` — Flag when best >> second-best (clean channel)
  - `multipath_flag` — Multiple high-Q candidates detected
- **Parameterized**: `ENABLE_DIAG = 0` disables all diagnostics for production (zero area cost)
- **Use cases**:
  - Debug: "Why did sync fail?" → check Q values, multipath flag
  - Monitoring: Log `phase_inc` over time → track crystal drift
  - Channel assessment: Peak magnitudes → estimate SNR or fading depth

---

## Key Insights from Analysis

### Why CFO Matters in This Project

**Not because of non-coherent despreading** (already CFO-immune by design) —
but because **`preamble_sync.sv` uses coherent correlation**, which catastrophically fails at CFO > 7.8 kHz.

- At 96 kHz (worst-case crystal), preamble (128 µs) rotates **12.8 full turns**
- Coherent correlation vectors point in different directions → **destructive interference**
- Measured: BER jumps from 0.7% → 48% (worse than random)

### The Circular Dependency Deadlock

Classic chicken-and-egg:
- **Need sync** to get clean chips → estimate CFO
- **But sync is blind** without CFO correction (correlation peak collapses)

**Solution**: Joint estimation with **CFO-blind metric** (phase consistency)
- Search alignment point + frequency **simultaneously**
- Quality metric: `Q = |Σd| / Σ|d|` (phase coherence, insensitive to CFO)
- Breaks the deadlock

### Three Critical Tricks (All Were踩坑 Lessons)

1. **Divide by ideal reference**: Remove ±18.09° ISI artifact from pulse shaping
2. **Span-2 differential**: Same rail (I→I or Q→Q) = constant 16 samples, enables coherent averaging
3. **Quality check**: Coherence factor ≥ 25% → flag poor estimates

These tricks make the **difference between working and broken**.

### Comparison: CFO vs. SFO

| Aspect | CFO | SFO |
|--------|-----|-----|
| **Tolerance** | 3.3 ppm (sync), 58 ppm (decision) | 58 ppm (timing slip) |
| **Correction** | Phase derotation (1 complex multiply/sample) | Resampling (expensive) |
| **Priority** | **Fix first** (tighter tolerance) | Fix second |

CFO is **cheaper to fix** but **tighter** → correct it before SFO.

---

## Current Implementation Status

### Existing Modules (Production-Ready)

✅ **`rtl/rx/cfo_est.sv`** — Joint estimation (p_hat + phase_inc + phase_off)
- 2048-sample collection window (covers full preamble)
- 8 candidate alignment points (1 symbol period)
- CORDIC-based phase extraction (24-bit precision)
- Outputs: 3-bit alignment, 24-bit phase increment, quality flag

✅ **`rtl/rx/cfo_rot.sv`** — Derotation (continuous correction)
- Phase accumulator + 256-entry cos/sin LUT
- 1 complex multiply per sample
- Loads initial phase `phase_off` for reference alignment

✅ **`tb/cfo_corr/`** — Cocotb testbench
- Model comparison (bit-true validation)
- Measured: estimation error < 11 Hz (noiseless), 30-50 Hz (noisy)
- BER recovered from 33-79% → 0% across 0-450 kHz CFO range

✅ **Model validation** — `model/experiments/run_cfo_fix.py`
- Matches RTL bit-true
- Generates analysis plots (BER, estimation accuracy, correlation peaks)
- Output: `model/out/cfo_fix/`

### Integration Status

**Partially integrated** in `rx_top.sv`:
- Modules instantiated, trigger logic defined
- Two-pass architecture: detect → estimate → correct → re-sync
- **Needs**: Final timing closure and end-to-end system test

---

## Recommended Next Steps

### Priority 1: Complete Integration (Week 1-2)

1. **Finish `rx_top.sv` CFO path wiring**
   - Connect `cfo_est` → `cfo_rot` → `preamble_sync` (second pass)
   - Mux between external trigger (`preamble_detect`) and internal trigger
   - Implement `chip_off` / `skip_t3` configuration logic

2. **End-to-end system test** (`tb/rx_chain_e2e/`)
   - Run with CFO sweep: 0, 20, 50, 96, 150 kHz
   - Verify BER recovery matches model predictions
   - Test both trigger modes (internal sync + external AGC)

3. **Timing closure**
   - Critical path: `cfo_est` accumulator → CORDIC
   - Target: 16 MHz clock (62.5 ns period)
   - May need pipeline stage if CORDIC atan2 is multi-cycle

### Priority 2: Add Diagnostic Module (Week 2)

4. **Integrate `cfo_est_diag.sv`** (or patch diagnostics into existing `cfo_est.sv`)
   - Add diagnostic signals to `rx_top` interface (can be debug-bus multiplexed)
   - Create register bank for host readout (if SPI/I2C interface exists)
   - Document diagnostic fields for MAC firmware

5. **Validation test cases**
   - **Multipath scenario**: Two-tap channel with 0.5-chip delay
     → Verify `multipath_flag` asserts and `p_second` captures runner-up
   - **Low-SNR scenario**: SNR = -6 dB
     → Verify `high_confidence` de-asserts when estimates are noisy
   - **No-preamble scenario**: Feed pure noise
     → Verify all `Q_cand < 0.25` (no false locks)

### Priority 3: Adaptive Window (Week 3)

6. **Implement adaptive collection window**
   - Add `trigger_delay` input to `cfo_est`
   - Dynamic `NSMP = min(2048, 2048 - trigger_delay - margin)`
   - Test with `preamble_detect` trigger (measured jitter ±10 samples)

7. **Performance validation**
   - Compare: fixed 2048-sample window vs. adaptive 1536-sample
   - Measure: estimation noise increase (expect ~15% per √(N₁/N₂))
   - Verify: still << 62.5 kHz tolerance even at 1536 samples

### Optional: Energy Optimization (If Battery-Powered)

8. **Coarse-fine two-stage estimation** (only if targeting < 1 mW receiver)
   - Stage 1: Envelope correlation (32 candidates, real multiplies)
   - Stage 2: Phase consistency (8 candidates around coarse peak)
   - Expected savings: 4× energy on CFO estimation
   - **Caveat**: Fails in multipath → needs fallback to full search

9. **Gate clock to `cfo_est` when idle**
   - Estimation only needed once per packet (~0.1% duty cycle for periodic sensors)
   - Clock gating can save 99% of CFO estimator's idle power

---

## Field Deployment Checklist

When moving to production hardware:

### 1. Calibration and Test

- [ ] Crystal frequency offset calibration (measure actual ppm vs. spec)
- [ ] Temperature sweep test (-40°C to +85°C)
  - Log `phase_inc` vs. temperature → validate drift model
- [ ] Interference stress test (co-channel WiFi, Bluetooth)
  - Check `est_ok` flag and `Q_cand` distribution
- [ ] Long-range test (low SNR, near sensitivity limit)
  - Verify CFO correction extends range vs. no-correction baseline

### 2. Monitoring and Diagnostics

- [ ] Expose diagnostic registers to MAC/host
  - `phase_inc` → convert to Hz → log for crystal aging analysis
  - `Q_cand[p_hat]` → sync quality metric
  - `multipath_flag` → channel assessment
- [ ] Threshold tuning for `est_ok`
  - Current: coherence factor ≥ 0.25
  - May need adjustment based on field SNR distribution
- [ ] Alarm on repeated estimation failures
  - If `est_ok = 0` for > N consecutive packets → flag hardware issue

### 3. Performance Benchmarking

- [ ] Compare PER (Packet Error Rate) curves: with vs. without CFO correction
  - Expect: 10-15 dB sensitivity improvement at high CFO (96 kHz)
- [ ] Measure average current consumption (with clock gating)
  - CFO estimator should be < 1% of total RX power
- [ ] Network-level throughput test (multi-node)
  - Verify no CFO-related link failures

---

## Open Research Questions

These are **not blockers** for production, but interesting directions for future work:

### Q1: Can We Extend to Coherent Despreading?

Current receiver uses **non-coherent** despreading (`|Σ chip·conj(ref)|`).

**Coherent despreading** (`Σ chip·conj(ref)`, real part only) has **~3 dB advantage**
but requires **phase reference**.

With CFO correction in place:
- Phase is stable within symbol (±8 kHz residual << 62.5 kHz tolerance)
- Could switch to coherent mode for **higher sensitivity**
- Needs: phase tracking loop (Enhancement 1) for long packets

**Benefit**: Push sensitivity from -97 dBm to -100 dBm (3 dB improvement)

### Q2: How Does CFO Interact with Multipath?

Multipath creates **multiple correlation peaks** (one per path).

If paths arrive > 2 chips apart:
- Different `p_hat` candidates see different paths
- Each has high Q → `multipath_flag` asserts
- **Which path should we lock to?** (earliest = strongest LOS?)

**Current behavior**: Pick highest Q (usually strongest path).
**Alternative**: Combine multiple paths (Rake receiver, MRC).

### Q3: Can We Do "Blind" CFO Estimation (No Preamble)?

Current method needs **known preamble** (802.15.4 SHR = 8 identical symbols).

**Blind methods** (no training sequence):
- **Autocorrelation**: Exploit cyclostationary structure (fails for random data)
- **4th-order moments**: Frequency-independent (but noisy)
- **Decision-directed**: Use demodulated symbols as reference (needs low-CFO start)

**Verdict**: Not worth it for 802.15.4 (preamble is mandatory, gives excellent estimates).

---

## Conclusion

### What We Achieved

1. **Deep understanding** of CFO's impact on this specific receiver architecture
   - Identified dominant failure mode (sync collapse, not decision degradation)
   - Quantified tolerance limits (7.8 kHz sync vs. 62.5 kHz decision)

2. **Complete solution** with production-ready RTL + model validation
   - Joint estimation breaks circular dependency deadlock
   - Measured performance: 59-65× BER improvement, works to 450 kHz

3. **Enhancement roadmap** with prioritized recommendations
   - Tier 1 (immediate): Diagnostic outputs, adaptive window
   - Tier 2 (optional): Coarse-fine search if battery-powered
   - Tier 3 (skip): Heavyweight features (tracking loop, multi-frame learning, FFT)

### Why This Matters

**CFO correction is not optional** for this receiver — without it, the system fails catastrophically at ≥50 kHz offset (48% BER).

This is **different from academic papers** (many assume differential detection, which avoids CFO)
and **different from industry practice** (GNU Radio 802.15.4 uses non-coherent sync, no CFO estimation).

**This project's choice** (coherent preamble sync for best sensitivity) **requires CFO correction**.
The implemented solution is **optimal for the constraints**:
- One-shot (no loops, no warmup)
- Cheap (1 complex multiply/sample sustained, ~8K one-time search)
- Wide range (±500 kHz unambiguous, tested to 450 kHz)

### Next Critical Path

**Week 1-2: System integration and validation**
- Wire up two-pass CFO path in `rx_top.sv`
- End-to-end test with CFO sweep
- **Gate for tapeout**: BER < 1% @ SNR = -1 dB, CFO = 96 kHz

**Week 2: Diagnostic module**
- Add visibility for production debugging
- **Gate for field deployment**: Can remotely diagnose sync failures

---

## References

### Internal Documents
- `docs/11_文献调研_CFO与同步工程实践.md` — Literature survey (Chinese)
- `docs/CFO_Analysis_and_Solution.md` — This analysis (comprehensive)
- `docs/CFO_Enhanced_Block_Design.md` — Enhancement proposals
- `model/experiments/run_cfo_fix.py` — Model validation
- `model/out/cfo_fix/report.md` — Experimental results
- `rtl/rx/cfo_est.sv`, `cfo_rot.sv` — Production RTL
- `rtl/rx/cfo_est_diag.sv` — Enhanced diagnostic module (new)
- `tb/cfo_corr/` — Cocotb testbench

### External References
- **IEEE 802.15.4-2020**: Section 11.3 (2.4 GHz PHY specification)
- **GNU Radio gr-ieee802-15-4**: Open-source reference (differential detection, no CFO estimation)
- **Morelli & Mengali (1999)**: "Feedforward frequency estimation for PSK" (classic survey)
- **Kay (1989)**: "A fast and accurate single frequency estimator" (WLSE theory)

---

**Document created**: 2026-09-30
**Author**: CFO analysis and enhancement design
**Status**: Integration in progress, estimated completion Week 3
**Contact**: See project README for team information
