# CFO (Carrier Frequency Offset) Analysis and Solution

## Executive Summary

**CFO is THE critical non-ideality in this 802.15.4 receiver** — it kills the receiver in two distinct ways:
1. **Synchronization failure** (dominant): CFO rotates the preamble, collapsing the correlation peak
2. **Decision degradation** (secondary): Phase rotation within symbols hurts coherent detection

**The implemented solution**: Joint preamble-assisted estimation of alignment point + frequency offset, followed by whole-frame derotation. Cost: ~8K multiply-accumulates once per frame + 1 complex multiply per sample.

---

## 1. CFO Physics: Three Time Scales

CFO = crystal oscillator mismatch between TX and RX (typ ±20 ppm, worst ±40 ppm)
- At 2.45 GHz carrier: ±96 kHz worst-case frequency offset
- Manifests as **phase rotation** in baseband: `φ(t) = 2π·Δf·t`

### Three Critical Time Windows

| Domain | Duration | Phase Accumulation @ 96 kHz | Impact |
|--------|----------|---------------------------|---------|
| **Matched Filter support** | 8 samples = 0.5 µs | 17° | Negligible (within pulse shape) |
| **Symbol duration** | 32 chips = 16 µs | 554° ≈ 1.5 turns | **Non-coherent limit: 62.5 kHz** |
| **Preamble duration** | 256 chips = 128 µs | 4423° ≈ 12.3 turns | **Coherent sync limit: 7.8 kHz** |

**Key insight**: The preamble (128 µs) accumulates **12.8 full rotations** at 96 kHz CFO.
- Coherent correlation template: `sum(rx * conj(ref))` → vectors point in 12 different directions → **destructive interference** → peak collapses
- Non-coherent detection: `sum(|rx * conj(ref)|)` → magnitude unchanged → immune to rotation

---

## 2. Two Failure Modes (Measured)

### Mode A: Synchronization Failure (Dominant)

**Problem**: `preamble_sync.sv` uses **coherent correlation** to find frame start:
```
acc = Σ v[n] · conj(c_ref[n])     // 2048 samples, 128 µs window
peak = |acc|
```

At 96 kHz CFO, the preamble rotates 12.8 turns → correlation collapses → sync picks **noise peaks** instead.

**Measured impact** (from `model/out/cfo_study/`):
- **0 kHz CFO**: BER = 7.37e-03 (baseline @ SNR=-1 dB)
- **20 kHz CFO**: BER = 9.46e-03 (sync starts to drift)
- **50 kHz CFO**: BER = 43.7% (**death zone**: sync completely blind)
- **96 kHz CFO**: BER = 48.1% (worse than random guessing due to false sync)

Root cause: **Circular dependency deadlock**
- Need sync to get clean chips → estimate CFO
- But sync is blind without CFO correction

### Mode B: Decision Degradation (Secondary)

Non-coherent despreading `|Σ chip[m]·conj(ref[m])|` is immune to **constant phase offset**,
but **not to phase rotation within the 32-chip symbol** (16 µs).

**Tolerance**: 62.5 kHz = 360°/(32 chips × 8 samples/chip)
- Below 62.5 kHz: decision vectors still point roughly the same direction within a symbol
- Above: rotation significant enough to cause destructive cancellation

---

## 3. The Solution: Joint Estimation

### Why "Joint"?

**Cannot do "sync first, then estimate CFO"** — sync is already broken by CFO (circular dependency).

**Solution**: Search alignment point and CFO **simultaneously** using a CFO-immune quality metric.

### Algorithm (from `cfo_est.sv` / `run_cfo_fix.py`)

For each candidate alignment point `p ∈ [0, 31]` (one symbol period):
1. Extract preamble chips at alignment `p`: `v[m] = mf[p + m*SPS + offset[m]]`
2. Remove known modulation: `z[m] = v[m] / c_ref[m]`
   - `c_ref[m]` = ideal preamble chip values (pre-computed ROM)
   - `z[m]` should be **pure phase** (CFO-induced rotation only)
   - **Critical**: Must divide out the ±18.09° ISI from pulse shaping (see below)
3. Differential phase: `d[m] = z[m+2] · conj(z[m])`
   - Span = 2 chips (same rail, I or Q) = 16 samples = 1 µs
   - Removes **data modulation**, leaves only **CFO-induced rotation**
   - Each `d[m]` should have phase `2π·Δf·1µs` if aligned correctly
4. Phase consistency metric: `Q = |Σ d[m]| / Σ|d[m]|`
   - Correct alignment → all `d[m]` in phase → `Q ≈ 1`
   - Wrong alignment → random phases → `Q ≈ 1/√N ≈ 0.09` (N=126 terms)
5. Pick `p_hat = argmax(Q)`
6. Frequency estimate: `f_hat = arg(Σ d[m]) / (2π · 1µs)`

**Unambiguous range**: ±500 kHz (Nyquist of 1 µs differential interval)
- Worst-case 96 kHz uses only 19% of range → no folding

### Three Critical Tricks (All Were踩坑 Lessons)

#### Trick 1: Divide by Ideal Reference

**Problem**: O-QPSK half-sine pulse has **structural ISI**:
- Even chip sampled at peak (t=7): main lobe = 4.000, trailing Q-chip tail = 1.3066
- Phase artifact: `atan(1.3066/4.000) = 18.09°`
- This **18°** swamps the few-degree CFO phase difference!

**Solution**: `z[m] = v[m] / c_ref[m]` removes both data and ISI structure.

#### Trick 2: Span-2 Differential (Same Rail)

**Problem**: O-QPSK I/Q are offset by half chip → adjacent chips alternate between:
- I-chip → Q-chip: 12 samples apart
- Q-chip → I-chip: 4 samples apart
- Phase increments are **not constant** → cannot average coherently

**Solution**: Span-2 differential (I→I or Q→Q) = constant 16 samples = 1 µs

#### Trick 3: Quality Check (Coherence Factor)

**Output**: `est_ok = (|Σd|_1 >= Σ|d|_1 / 4)`
- Good estimate: phase coherence ≥ 25%
- Poor estimate: likely misaligned or too much noise → fall back to non-correction mode

---

## 4. Implementation: Two-Block Architecture

### Block 1: `cfo_est.sv` (Estimation, Once per Frame)

**Inputs**:
- `i_in, q_in [W-1:0]`: Matched filter output (21-bit signed)
- `start`: Single pulse to begin collection
- `chip_off [7:0]`: Collection window offset (for external trigger compatibility)
- `skip_t3`: Patch flag for trigger timing (see code comments)

**Outputs**:
- `p_hat [2:0]`: Alignment point (chip phase 0-7)
- `phase_inc [23:0]`: Phase increment per sample (2π full scale)
- `phase_off [23:0]`: Initial phase at frame start φ₀
- `done`: Single pulse when estimation complete
- `est_ok`: Quality flag (coherence factor ≥ 0.25)

**Pipeline**:
1. **COLLECT** (2048 cycles): Scan 8 candidate alignments, accumulate differential phases
2. **CMP** (1 cycle): Compare 8 candidates, pick best
3. **CORDA** (26 cycles): CORDIC to compute `arg(Σd) → phase_inc`
4. **CORDB** (26 cycles): CORDIC to compute `arg(Σz) → phase_off`

**Cost**: ~8K complex multiply-accumulates (once per frame, pipelined with data path)

### Block 2: `cfo_rot.sv` (Correction, Continuous)

**Algorithm**: `out[n] = in[n] · exp(-j·n·phase_inc)`

**Implementation**:
- Phase accumulator (24-bit, wraps naturally)
- LUT-based cos/sin (8-bit index → 16-bit values, 256 entries)
- One complex multiply per sample: 4 real multiplies + 2 adds

**Inputs**:
- `i_in, q_in`: MF output stream
- `phase_inc [23:0]`: From `cfo_est`
- `phase_off [23:0]`: Frame start phase (loaded on `load` pulse)
- `load`: Reset phase accumulator to `-phase_off`

**Why load `-phase_off`?**
To align the derotated preamble to the **real axis** for the subsequent non-conjugate correlation in despreading. The phase reference point is set such that the first chip after derotation has phase 0.

**Cost per sample**: 1 complex multiply = 4 real multiplies + 2 adds (~40 nJ @ 16 MHz)

---

## 5. Measured Performance (from `model/out/cfo_fix/`)

### BER Recovery

| CFO [kHz] | Raw BER | Fixed BER | Reduction |
|-----------|---------|-----------|-----------|
| 0         | 7.37e-03 | 7.37e-03 | — (baseline) |
| 5         | 7.37e-03 | 7.37e-03 | — (below threshold) |
| 20        | 9.46e-03 | 7.37e-03 | **back to baseline** |
| 50        | 43.7%    | 7.37e-03 | **59× improvement** |
| 96        | 48.1%    | 7.37e-03 | **65× improvement** |
| 150       | 47.5%    | 3.21e-04 | **1480× improvement** |
| 300       | 58.2%    | 5.61e-04 | **1037× improvement** |
| 450       | 62.7%    | 1.28e-03 | **490× improvement** |

### Estimation Accuracy

- **Bias**: −2.8 kHz (constant, independent of CFO magnitude)
- **Std dev**: 8.4 kHz (1σ @ SNR = -1 dB)
- **Unambiguous range**: ±500 kHz (well beyond ±96 kHz crystal worst-case)
- **No folding**: Bias doesn't grow with CFO (differential method advantage)

### Key Takeaway

**CFO correction restores the receiver to baseline performance up to 450 kHz** (4.7× the crystal spec).
The residual estimation error (±8 kHz std) is **far below the 62.5 kHz non-coherent tolerance**.

---

## 6. Literature Context (from `docs/11_文献调研_CFO与同步工程实践.md`)

### Industry Practice: Avoid vs. Correct

**GNU Radio `gr-ieee802-15-4` (309★, reference implementation)**:
- **No CFO estimation module at all**
- Strategy: **Differential detection everywhere**
  - Preamble sync: symbol-by-symbol phase difference `arg(s[n]/s[n-1])`
  - Despreading: hard-decision Hamming distance on chip bits
- Advantage: Completely CFO-immune (differential removes common phase)
- Cost: ~3 dB SNR penalty vs. coherent detection

**Academic Papers**:
- [2008] TSMC 0.18µm Zigbee baseband: "packet detection algorithm used to estimate large CFO"
  - Three-stage: coarse CFO (from sync) → demodulation → residual phase tracking
  - Cost: 78k gates, 1.7 mW, PER < 0.01 @ SNR < 5 dB
- [2020] Symbol-by-symbol detector: "4 preamble symbols sufficient for CFO estimation"
  - Estimates **residual CFO** (assumes coarse correction elsewhere)
  - 0.17× runtime vs. non-coherent (cheaper after correction)

### This Project's Position

**Hybrid approach**: Non-coherent despreading (data path) + coherent sync (must fix CFO)
- Despreading: Already CFO-immune by design (non-coherent energy detection)
- Sync: Coherent correlation → **must correct CFO** or sync fails catastrophically

This is **why CFO matters here** — not because of decision degradation (we're non-coherent),
but because **preamble sync uses coherent correlation and breaks without correction**.

---

## 7. Comparison: CFO vs. SFO (Sampling Frequency Offset)

| Aspect | CFO | SFO |
|--------|-----|-----|
| **Source** | Crystal mismatch at carrier (2.45 GHz) | Crystal mismatch at sampling (16 MHz) |
| **Spec** | ±40 ppm worst → ±96 kHz | ±40 ppm → ±640 Hz sample rate drift |
| **Manifestation** | Phase rotation `2π·Δf·t` | Timing drift (sample time mismatch) |
| **Tolerance** | Sync: 7.8 kHz; Decision: 62.5 kHz | ~58 ppm (±928 Hz) before timing slip |
| **Correction method** | Phase derotation (1 complex multiply/sample) | Resampling + interpolation (expensive) |
| **Estimation** | Preamble differential phase (once per frame) | Timing error detector (continuous loop) |
| **Priority** | **Higher** (3.3 ppm tolerance) | Lower (58 ppm tolerance) |

**Key difference**: CFO is a **common phase offset** → fix once and forget.
SFO is **timing drift** → needs continuous tracking and resampling.

**Conclusion**: CFO is **cheaper to fix** but **tighter tolerance** → correct it first.

---

## 8. Open Questions / Future Work

### Q1: Can we avoid the 8K-term search?

**Idea**: Use **envelope correlation** for coarse alignment (CFO-immune),
then run the phase-consistency search over a narrower window (e.g., ±4 samples).

Envelope: `|mf[n]|` is unaffected by phase rotation → correlation peak stays put.
- Coarse sync: `argmax( |Σ |mf[n]| · |ref[n]|| )` → get within 1 chip
- Fine sync: Run phase-consistency search over ±1 chip (16 candidates instead of 256)

**Savings**: 16× reduction in search space → ~500 multiply-accumulates.

### Q2: Does decision-directed phase tracking help?

**Current**: Estimate once (preamble), correct entire frame with fixed `phase_inc`.

**Alternative**: Add **decision-directed feedback** after despreading:
- Despread → get symbol decisions
- Compare with expected phase → update `phase_inc` per symbol
- For longer frames (>20 bytes), residual CFO drift may accumulate

**Literature**: [2008] paper uses this as third stage. Needed for **long packets** or **coherent despreading**.

### Q3: Can we simplify for short packets?

**Observation**: At SNR = -1 dB, even 96 kHz CFO gives **8.4 kHz estimation std dev**.
After correction, residual is ~8 kHz « 62.5 kHz tolerance.

**Question**: For very short packets (<10 symbols), can we skip correction entirely
and just use **wider sync window** + **relaxed threshold**?

**Trade-off**: Lower detection probability vs. zero correction cost.

---

## 9. RTL Integration Status

### Implemented Modules

✅ `rtl/rx/legacy/cfo_est.sv`: Joint estimation (p_hat + phase_inc + phase_off)
✅ `rtl/rx/backend/cfo_rot.sv`: Derotation (phase accumulator + LUT + complex multiply)
✅ `tb/cfo_corr/`: Cocotb testbench with model comparison
✅ Model validation: `model/experiments/run_cfo_fix.py` matches RTL bit-true

### Integration Points (in `rx_top.sv`)

```
rx_matched_filter → cfo_est (triggered by preamble_sync or external)
                 ↓
                 ├→ phase_inc, phase_off, p_hat
                 ↓
rx_matched_filter → cfo_rot → preamble_sync (re-sync on corrected signal)
                             ↓
                             despreader → deframer → MAC
```

**Two-pass architecture**:
1. First pass: Detect preamble in raw MF output (may be unreliable with high CFO)
2. Run `cfo_est` to get correction parameters
3. Second pass: Apply `cfo_rot`, re-run sync on corrected signal
4. Despread using corrected chips

### Trigger Modes

**Mode 1: Internal trigger** (`preamble_sync` output)
- Pro: Self-contained, no external dependencies
- Con: May false-trigger or miss detection under high CFO

**Mode 2: External trigger** (`preamble_detect` AGC-based)
- Pro: CFO-immune AGC burst detection
- Con: ~460-sample jitter → must use shorter collection window

Current implementation supports both via `chip_off` and `skip_t3` runtime parameters.

---

## 10. Key Insights for Other Projects

### Lesson 1: Sync vs. Decision — Know Your Bottleneck

- **Non-coherent decision** → CFO tolerance = symbol duration (16 µs → 62.5 kHz)
- **Coherent sync** → CFO tolerance = preamble duration (128 µs → 7.8 kHz)
- **Sync kills you first** → 8× tighter tolerance

### Lesson 2: Circular Dependencies → Joint Estimation

Classic chicken-and-egg: "need timing to estimate frequency, need frequency to sync timing"
- **Solution**: Search both dimensions simultaneously with a **blind metric** (phase consistency)
- Costs more compute, but breaks the deadlock

### Lesson 3: Differential = CFO Immunity

Adjacent-sample differential `z[n]/z[n-1]` removes **common phase**:
- CFO adds `exp(j·2π·Δf·n/fs)` to every sample
- Ratio: `exp(j·2π·Δf·n/fs) / exp(j·2π·Δf·(n-1)/fs) = exp(j·2π·Δf/fs)`
- Only **one sample's worth** of phase rotation (constant), not accumulated

**Cost**: ~3 dB SNR loss vs. coherent integration. **Benefit**: No CFO correction needed.

### Lesson 4: LUT + Accumulator > Recursive Rotation

For per-sample derotation `exp(-j·n·θ)`:
- **Recursive**: `w[n] = w[n-1] · exp(-j·θ)` → 1 complex multiply per sample, **but error accumulates**
- **LUT + accumulator**: `w[n] = LUT[n·θ mod 2π]` → same cost, **no accumulation error**

With 24-bit accumulator + 8-bit LUT index, phase quantization = 1.4° « CFO-induced rotation.

---

## Conclusion

**CFO is the dominant impairment in this 802.15.4 receiver** — not because it breaks the
non-coherent despreader (it doesn't), but because it **collapses the coherent preamble correlation peak**.

**The solution** (joint estimation + derotation) is **cheap, one-shot, and works up to 450 kHz**:
- Estimation: ~8K MAC operations once per frame (amortized ~4 MAC/sample for 20-byte packet)
- Correction: 1 complex multiply per sample (~0.25 µW @ 16 MHz)
- No loops, no feedback, no FFT

**Key enabler**: Differential phase measurement (span-2 chips, 1 µs) gives:
- ±500 kHz unambiguous range (5× crystal spec)
- CFO-blind quality metric (phase consistency)
- Breaks the sync-estimation circular dependency

This is **must-have** for any O-QPSK receiver using coherent preamble sync.
