# CFO Integration: Action Plan and Validation Strategy

## Current Status Assessment

### ✅ What's Already Done

1. **Core RTL modules exist and are integrated**:
   - `rtl/rx/legacy/cfo_est.sv` — Joint estimation (production-ready)
   - `rtl/rx/backend/cfo_rot.sv` — Derotation (production-ready)
   - `rtl/rx/legacy/rx_top.sv` — Full integration with adaptive trigger modes

2. **Integration features in rx_top.sv**:
   - Two trigger modes: external (test) vs. internal (autonomous detect)
   - Adaptive collection window offsets: `EST_CHIP_OFF` vs `EST_CHIP_OFF_AUTO`
   - Quality gating: `EST_REQ_QUAL` flag + `est_ok` check
   - Safety limits: `INC_LIMIT` = 500 kHz, consistency check `INC_TOL`
   - Optional voting accumulator (multi-frame learning, currently disabled)

3. **Testbench infrastructure**:
   - `tb/cfo_corr/` — Cocotb testbench with model comparison
   - Multiple test modes: basic, eye diagram, RTL lab validation
   - Analysis scripts for waveform inspection

4. **Model validation**:
   - `model/experiments/run_cfo_fix.py` — End-to-end BER validation
   - Results in `model/out/cfo_fix/` — Measured 59-65× BER improvement

### ⚠️ What Needs Immediate Attention

Based on the code comments in `rx_top.sv`, there are **critical issues** that need resolution:

1. **Line 25-27**: First-time estimation unreliable
   - CFO=0: estimates -365 kHz
   - CFO=100 kHz: estimates -103 kHz (wrong sign!)
   - CFO=450 kHz: estimates -22 kHz
   - **Root cause**: Likely collection window misalignment or trigger timing

2. **Line 30-32**: Consecutive estimates can be "mutually consistent but both wrong"
   - Example: CFO=450 kHz → estimates -337.6/-341.2 kHz (consistent, but 180° phase ambiguity)
   - Current mitigation: Require 3 consecutive agreements + quality check

3. **Line 42**: Cold-start acquisition problem
   - Median of first 15 estimates @ CFO=100 kHz: 72.3 kHz (true value 100 kHz)
   - Bias: -27.7 kHz systematic error during acquisition

4. **Line 50**: Voting accumulator disabled (`USE_VOTE = 1'b0`)
   - Reason: "estimates scattered across bins without derotation"
   - This suggests the system is trying to estimate **before** applying correction → chicken-egg problem not fully resolved

---

## Root Cause Analysis

### Problem 1: Why Are First Estimates Wrong?

**Hypothesis 1**: Collection window doesn't align with preamble
- Trigger point jitter causes `chip_off` to be incorrect
- Wrong `chip_off` → `cfo_est` divides by wrong reference chips → garbage output

**Hypothesis 2**: Phase ambiguity (180° flip)
- CORDIC `atan2` returns phase in [-π, π]
- If preamble correlation has 180° ambiguity, sign of CFO estimate flips
- Example: True CFO = +100 kHz → estimates -103 kHz (sign flip)

**Hypothesis 3**: Internal trigger timing is off
- Comment says detect arrives at 538±1 samples after frame start
- But delay is set to 6 cycles → 544 samples → `chip_off = 3`
- If actual delay drifts, `chip_off` becomes incorrect

### Problem 2: Why Do Wrong Estimates Cluster?

**Observation**: -337.6 and -341.2 kHz are very close (Δ = 3.6 kHz << 16 kHz tolerance)
- This passes the "consistency check" but both are wrong
- Suggests **systematic bias**, not random noise

**Likely cause**: Phase ambiguity lock
- Once locked to wrong phase reference, all subsequent estimates are biased the same way
- Derotation using wrong `inc_reg` → chips still rotated → next estimate also wrong

---

## Proposed Action Plan

### Phase 1: Diagnostic Deep-Dive (Week 1)

**Goal**: Understand why first estimates fail

#### Action 1.1: Add comprehensive diagnostic outputs

Integrate `cfo_est_diag.sv` (already created) into test flow:

```bash
# In tb/cfo_corr/test_cfo_corr.py, capture:
# - Q_cand[0:7] for all 8 alignment candidates
# - peak_mag2_raw vs peak_mag2_corrected
# - p_second, Q_second (runner-up detection)
# - high_confidence, multipath_flag
```

**Expected insights**:
- If all `Q_cand < 0.25`: Collection window missed preamble entirely
- If `Q_cand[0] ≈ Q_cand[4]`: 180° phase ambiguity (two strong peaks 4 chips apart)
- If `peak_mag2_corrected < peak_mag2_raw`: CFO correction made things worse (wrong sign)

#### Action 1.2: Sweep trigger timing

Test with external trigger at different offsets relative to true frame start:

```python
# model/experiments/test_trigger_sweep.py
for trigger_offset in range(-64, 65, 8):  # -64 to +64 samples, step 8
    for cfo_khz in [0, 50, 100, 150]:
        # Run cfo_est with chip_off adjusted for trigger_offset
        # Measure: estimation error, Q_best, est_ok rate
        # Plot: error vs. trigger_offset (should show sweet spot)
```

**Expected outcome**: Find optimal `chip_off` and quantify tolerance to trigger jitter

#### Action 1.3: Check phase ambiguity

Add phase disambiguation logic:

```python
# In model validation, check if estimate is 180° flipped
# by comparing pilot-symbol phase after correction
phase_error = angle(despread_output[0]) - expected_phase[0]
if abs(phase_error) > 90°:
    # Flip sign of phase_inc and re-test
    phase_inc_corrected = -phase_inc
```

### Phase 2: Fix Root Causes (Week 2)

Based on Phase 1 findings, implement fixes:

#### Fix Option A: Improve trigger timing

If trigger jitter is the issue:

1. **Widen collection window** from 2048 to 2560 samples (160 chips)
   - Covers ±32 samples of jitter
   - Cost: +25% compute, but only once per frame

2. **Use envelope-based coarse alignment** (Enhancement 3 from design doc)
   - Stage 1: Envelope correlation finds coarse timing (immune to CFO)
   - Stage 2: Phase consistency search over narrow window
   - Benefit: Robust to trigger jitter, 4× energy savings

#### Fix Option B: Resolve phase ambiguity

If sign flips are the issue:

1. **Use known SFD for disambiguation**:
   ```verilog
   // After CFO correction, despread the SFD (known sequence)
   // Check if phase matches expected
   // If flipped → negate phase_inc
   ```

2. **Or use differential CFO estimation**:
   - Estimate CFO from **chip-to-chip phase transitions**, not absolute phase
   - Differential phase is unambiguous (no 180° flip)
   - Already implemented in `cfo_est.sv` (span-2 differential) — should work!

#### Fix Option C: Multi-stage acquisition

If cold-start bias is the issue:

1. **Coarse estimate from short window** (512 samples)
   - Fast, noisy, but breaks chicken-egg deadlock
   - Apply coarse correction → improves alignment for next estimate

2. **Fine estimate from full window** (2048 samples)
   - After coarse correction, preamble is nearly aligned
   - Fine estimate is accurate

3. **Decision-directed refinement** (optional)
   - Use data symbols to track residual drift

### Phase 3: Re-enable Multi-Frame Learning (Week 3)

After fixes stabilize single-frame estimation:

#### Action 3.1: Re-enable voting accumulator

```systemverilog
// In rx_top.sv, set:
parameter USE_VOTE = 1'b1,  // Enable multi-frame voting
parameter VOTE_TH  = 3,     // 3 votes required for adoption
```

**Validation test**:
- Cold start with CFO = 100 kHz
- Measure: How many frames until first adoption? (Target: <20 frames)
- Measure: Adopted value accuracy (Target: within ±5 kHz of true)

#### Action 3.2: Implement per-node memory (Enhancement 2)

For network scenarios with repeated packets from same transmitter:

```systemverilog
// Add to rx_top.sv:
reg signed [PW-1:0] cfo_memory [0:15];  // 16-node table
input wire [3:0]    src_addr;           // From MAC layer

// On packet reception:
if (est_done && est_ok) begin
    // Exponential moving average: α = 1/8
    cfo_memory[src_addr] <= 
        cfo_memory[src_addr] + ((phase_inc - cfo_memory[src_addr]) >>> 3);
end

// On packet detection (before estimation):
inc_reg <= cfo_memory[src_addr];  // Use learned value as initial guess
```

**Benefit**: Second packet from same node starts with good estimate → faster lock

### Phase 4: System-Level Validation (Week 4)

#### Test 4.1: End-to-end BER sweep

```bash
cd model/experiments
python run_cfo_fix.py --mode full_system --cfo_range 0:500:10

# Expected output:
# BER < 1% for CFO up to 200 kHz (2× crystal spec)
# BER < 5% for CFO up to 450 kHz (4.7× spec)
```

#### Test 4.2: Multi-frame acquisition stress test

```python
# Scenario: 100 consecutive packets, CFO = 100 kHz, cold start
# Measure:
# - Frame 1-5: How many fail? (Expect: 0-2 with improved acquisition)
# - Frame 6+: Should be 100% reliable (learned CFO applied)
```

#### Test 4.3: Network simulation (multi-node)

```python
# 8 nodes, each with different CFO (±96 kHz uniformly distributed)
# Each node sends 50 packets interleaved
# Measure: PER per node (should be <1% after learning phase)
```

---

## Quick Wins (Can Do Today)

### 1. Run existing testbench with diagnostic outputs

```bash
cd tb/cfo_corr
python run_lab.py  # Check if this dumps diagnostics

# If not, modify test_rtl_lab.py to capture Q_cand values
```

### 2. Validate trigger timing in model

```bash
cd model/experiments
python -c "
from run_cfo_study import *
# Generate frame with CFO=100 kHz
# Manually shift trigger point ±50 samples
# Check: Does estimate accuracy drop?
"
```

### 3. Check for sign flips in existing data

```bash
cd model/out/cfo_fix
# Look at estimation results: are errors clustered around ±180° phase ambiguity?
# Plot histogram of (estimated_cfo - true_cfo) → should be single peak, not bimodal
```

### 4. Test the fix for chip_off

The comment in `rx_top.sv` line 55-57 says:
- External trigger: `chip_off = 0` works (50/50)
- Internal trigger: `chip_off = 3` works (50/50), but `chip_off = 0` fails (0/50)

**Validation**:
```bash
# In tb/cfo_corr or model, test both configurations
# Expected: chip_off should match (trigger_delay mod 16) / 2
```

---

## Recommended Immediate Actions (This Week)

### Priority 1: Verify trigger timing is correct

**Task**: Confirm `EST_CHIP_OFF_AUTO = 3` is optimal for internal trigger

**Method**:
1. Run testbench with internal trigger + CFO sweep
2. Capture `p_hat` (alignment point) distribution
3. If `p_hat` always = 0 or 7 → `chip_off` is wrong, detection is at boundary
4. Optimal: `p_hat` should be near 3-4 (middle of symbol period)

**Time**: 2 hours (run tests + analyze)

### Priority 2: Add Q_cand diagnostic to testbench

**Task**: Capture phase consistency for all 8 candidates

**Method**:
1. Instantiate `cfo_est_diag.sv` in `tb/cfo_corr/cfo_corr_top.sv`
2. Modify `test_cfo_corr.py` to log `Q_cand[0:7]` on each `est_done`
3. Plot Q vs. candidate index → should show single sharp peak

**Time**: 3 hours (RTL edit + testbench update + validation)

### Priority 3: Test phase ambiguity fix

**Task**: Check if sign flips explain -103 kHz estimate @ CFO=100 kHz

**Method**:
1. In model, after CFO correction, measure phase of first data symbol
2. Compare with expected phase (known SFD or pilot)
3. If consistently 180° off → implement sign disambiguation

**Time**: 2 hours (model analysis + optional fix implementation)

### Priority 4: Document current performance baseline

**Task**: Run full BER sweep with **current** rx_top.sv (before any fixes)

**Method**:
```bash
cd model/experiments
python run_cfo_fix.py --rtl_mode --cfo_range 0:200:20 --n_frames 50

# Output: BER table (should match docs/CFO_Analysis_and_Solution.md)
# If not matching → integration has regressed, need to debug
```

**Time**: 1 hour (run + compare with expected)

---

## Success Criteria

### Week 1 (Diagnostics)
- [ ] Trigger timing validated: `chip_off` is optimal (±2 samples)
- [ ] Phase consistency metrics captured: Q_best > 0.6 for valid frames
- [ ] Root cause identified for first-estimate failures

### Week 2 (Fixes)
- [ ] First estimate success rate > 80% (vs. current <30%)
- [ ] Sign ambiguity resolved (no more +100 → -103 kHz flips)
- [ ] Cold-start bias reduced: median error < 10 kHz (vs. current 27 kHz)

### Week 3 (Multi-frame)
- [ ] Voting accumulator re-enabled and working
- [ ] Acquisition completes in <15 frames (vs. current "scattered, can't converge")
- [ ] Adopted CFO within ±5 kHz of true value

### Week 4 (Validation)
- [ ] End-to-end BER < 1% for CFO up to 200 kHz (2× spec)
- [ ] Multi-node network simulation: PER < 1% per node after learning
- [ ] Field-ready: Can handle cold start + jitter + multi-frame scenarios

---

## Risk Mitigation

### Risk 1: Fixes don't improve first-estimate reliability

**Mitigation**: Fall back to "always use 3-frame voting" mode
- More latency (~3 packets to acquire), but guaranteed to converge
- Still better than no CFO correction (50% BER vs 1%)

### Risk 2: Trigger jitter exceeds collection window

**Mitigation**: Use envelope-based coarse stage (Enhancement 3)
- Adds ~500 operations, but robust to large jitter
- Or: Use wider collection window (2560 samples) + accept higher cost

### Risk 3: Multi-frame learning still doesn't work

**Mitigation**: Single-frame mode is production-ready
- Current code already has `USE_VOTE = 0` path working
- Multi-frame is optimization, not requirement

---

## Long-Term Vision (After Week 4)

### 1. Productization checklist
- [ ] Expose diagnostic registers to MAC layer (SPI/I2C readout)
- [ ] Add crystal drift monitoring (log `phase_inc` vs. temperature)
- [ ] Field deployment: Capture real-world CFO distribution

### 2. Advanced features (optional)
- [ ] Coherent despreading upgrade (requires tracking loop)
- [ ] Rake receiver for multipath combining
- [ ] Adaptive modulation (drop to lower rate if CFO too high)

### 3. Research directions
- [ ] Machine learning CFO estimator (train on real channel data)
- [ ] Joint CFO+SFO estimation (handle both simultaneously)
- [ ] Blind estimation (no preamble required)

---

## Conclusion

**Current state**: CFO correction is **integrated** but has **acquisition reliability issues**:
- First estimates are often wrong (sign flips, timing misalignment)
- Multi-frame learning disabled because estimates too scattered
- System works once locked, but cold-start is fragile

**Next steps**: 
1. **This week**: Diagnose trigger timing and phase ambiguity (2-6 hours work)
2. **Week 2**: Fix root causes (chip_off adjustment or disambiguation logic)
3. **Week 3**: Re-enable multi-frame learning and validate
4. **Week 4**: System-level validation and field readiness

**Recommended starting point**: Run Priority 1-4 "Quick Wins" to gather data, then decide on fixes.

---

**Document created**: 2025-01-XX  
**Status**: Action plan ready, awaiting execution  
**Owner**: CFO integration team  
**Next review**: After Priority 1-2 diagnostics complete
