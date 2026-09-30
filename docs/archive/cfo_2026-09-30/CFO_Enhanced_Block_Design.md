# Enhanced CFO Correction Block: Advanced Features

## Motivation

The existing `cfo_est.sv` + `cfo_rot.sv` architecture works well for **single-frame bursts**,
but real-world scenarios may benefit from:

1. **Residual tracking**: Long packets where single preamble estimate drifts over time
2. **Multi-frame learning**: Successive packets from same node → refine CFO estimate
3. **Fast acquisition**: Coarse-then-fine estimation to reduce search space
4. **Diagnostic outputs**: Per-candidate metrics for sync debugging

This document explores **three enhancement directions** with design sketches and research insights.

---

## Enhancement 1: Decision-Directed Phase Tracking Loop

### Problem: Long Packet Drift

Current design: Estimate CFO once from preamble → apply fixed `phase_inc` to entire frame.

**Issue for long packets** (e.g., >127-byte max 802.15.4 frame = 6.4 ms):
- Preamble estimation error: ±8.4 kHz std (@ SNR = -1 dB)
- Phase drift over 6.4 ms: `2π · 8.4 kHz · 6.4 ms = 338°`
- For **coherent despreading** (not used here, but relevant for other modes),
  this drift exceeds coherent phase tolerance (~90°)

### Solution: Decision-Directed Feedback

**Architecture**:
```
     ┌────────────────────────────────────────┐
     │  Preamble-based coarse estimate        │
     │  f_coarse = joint estimation           │
     └─────────────┬──────────────────────────┘
                   ↓
     ┌────────────────────────────────────────┐
     │  cfo_rot with f_coarse                 │
     │  derotated_chips[n]                    │
     └─────────────┬──────────────────────────┘
                   ↓
     ┌────────────────────────────────────────┐
     │  Despreader → symbol decisions         │
     │  sym_hat[k] (every 32 chips)           │
     └─────────────┬──────────────────────────┘
                   ↓
     ┌────────────────────────────────────────┐
     │  Phase error detector                  │
     │  ε[k] = arg(Σ chip[m]·conj(ref[m]))  │
     │  where ref = remodulated sym_hat       │
     └─────────────┬──────────────────────────┘
                   ↓
     ┌────────────────────────────────────────┐
     │  Loop filter (IIR)                     │
     │  f_residual[k+1] = f_res[k] + μ·ε[k]  │
     └─────────────┬──────────────────────────┘
                   ↓
     ┌────────────────────────────────────────┐
     │  Phase increment update                │
     │  phase_inc = f_coarse + f_residual     │
     └────────────────────────────────────────┘
```

### Design Parameters

**Phase error detector** (per symbol, after despreading):
```verilog
// Remodulate the decision to get expected chip pattern
wire [31:0] ref_chips = CHIP_MAPPING[sym_decision];
// Correlate actual chips with expected (use first 8 chips to avoid ISI)
complex_t corr = 0;
for (m = 0; m < 8; m++)
    corr += chips[m] * conj(ref_chips[m]);
// Phase error = angle of correlation
phase_error = atan2(corr.q, corr.i);
```

**Loop filter** (first-order IIR):
```verilog
// μ = loop bandwidth parameter (smaller = more filtering, slower response)
// Typical: μ = 2^-8 to 2^-12
parameter LOOP_GAIN_SHIFT = 10;  // μ = 1/1024
f_residual <= f_residual + (phase_error >>> LOOP_GAIN_SHIFT);
```

**Complexity**:
- Phase error detector: 8 complex multiplies + 1 CORDIC per symbol (every 16 µs)
- Loop filter: 1 add + 1 shift per symbol
- **Amortized cost**: ~0.5 complex multiply per sample (for 32-chip symbol)

### When to Use

✅ **Enable** if:
- Packets > 64 bytes (3.2 ms)
- Using **coherent despreading** (not in current project, but for future upgrades)
- Low SNR environment where preamble estimate has high variance

❌ **Skip** if:
- Packets ≤ 20 bytes (current typical)
- Non-coherent despreading (already CFO-tolerant)
- Cost-sensitive application (tracking loop adds ~20% area)

---

## Enhancement 2: Multi-Frame CFO Learning and Prediction

### Problem: Repeated Packets from Same Node

In a network with persistent links (e.g., periodic sensor reports), the **same transmitter**
sends multiple packets over time:
- Each packet gives an independent CFO estimate
- CFO is **quasi-static** (changes slowly with temperature drift, ~0.1 ppm/°C)
- Current design: **throws away** CFO estimate after each packet

### Solution: Per-Node CFO Memory

**Architecture**:
```
     ┌────────────────────────────────────────┐
     │  MAC extracts source address           │
     │  src_addr (16-bit short address)       │
     └─────────────┬──────────────────────────┘
                   ↓
     ┌────────────────────────────────────────┐
     │  CFO estimate from preamble            │
     │  f_new = cfo_est output                │
     └─────────────┬──────────────────────────┘
                   ↓
     ┌────────────────────────────────────────┐
     │  Lookup table: addr → f_pred           │
     │  (small SRAM or register file)         │
     │                                        │
     │  If src_addr exists:                   │
     │    f_pred_prev = LUT[src_addr]        │
     │    f_pred_new = α·f_new + (1-α)·f_prev│
     │  Else:                                 │
     │    f_pred_new = f_new                  │
     │                                        │
     │  LUT[src_addr] ← f_pred_new           │
     └─────────────┬──────────────────────────┘
                   ↓
     ┌────────────────────────────────────────┐
     │  Use f_pred as initial guess           │
     │  for next packet from this node        │
     └────────────────────────────────────────┘
```

### Design: Exponential Moving Average (EMA)

**Update rule**:
```
f_pred[n+1] = α · f_meas[n] + (1 - α) · f_pred[n]
```

Where:
- `f_meas[n]` = preamble-based estimate from packet n
- `f_pred[n]` = predicted CFO before packet n
- `α ∈ [0, 1]` = smoothing factor
  - `α = 1` → no memory (current behavior)
  - `α = 1/2` → equal weight to new and old
  - `α = 1/8` → heavy smoothing (good for noisy estimates)

**Implementation** (fixed-point):
```verilog
// α = 1/8 (ALPHA_SHIFT = 3)
parameter ALPHA_SHIFT = 3;
f_pred_next = f_pred + ((f_meas - f_pred) >>> ALPHA_SHIFT);
```

**Memory size**:
- Per-node entry: 24 bits (phase_inc) + timestamp (optional)
- For 16 nodes: 16 × 24 = 384 bits = 48 bytes
- For 256 nodes: 256 × 24 = 6144 bits = 768 bytes

### Benefits

1. **Faster acquisition**: First packet from known node starts with good f_pred
   → narrower search window or skip estimation entirely
2. **Robustness**: Average over multiple packets → reduce noise variance
   - Preamble estimate: σ = 8.4 kHz (single packet)
   - After 8-packet EMA: σ ≈ 8.4 / √8 = 3.0 kHz
3. **Outlier rejection**: If `|f_meas - f_pred| > threshold`, flag as suspect
   → detect interference or spoofing

### Extensions

**Temperature-aware prediction**:
```
f_pred[n+1] = f_pred[n] + drift_rate · Δt
```
Where `drift_rate ≈ -0.1 ppm/°C · f_carrier` (for typical crystals).

Requires:
- Temperature sensor input
- More sophisticated state machine

**Only for advanced applications** (e.g., outdoor IoT with wide temp swings).

---

## Enhancement 3: Coarse-Fine Two-Stage Estimation

### Problem: 8K-Term Search is Expensive

Current design searches **32 candidates × 256 chips = 8192 complex multiply-accumulates**.

For **low-power applications**, this is the dominant energy cost (once per packet).

### Solution: Envelope-Based Coarse Stage

**Key insight**: Envelope `|mf[n]|` is **unaffected by CFO** (magnitude-only).
- Envelope correlation peak position = **timing**, independent of phase rotation
- Use envelope to get **coarse alignment within ±0.5 chip** (4 samples)
- Then run phase-consistency search over narrow window (8 candidates instead of 256)

**Two-stage architecture**:

#### Stage 1: Envelope Correlation (Coarse Timing)

```verilog
// Precompute reference envelope (constant, can be ROM)
wire [15:0] env_ref[0:255];  // |c_ref[m]|, 256 chips

// Sliding window envelope correlation
reg [31:0] env_acc[0:31];    // 32 candidates (1 symbol period)
for (c = 0; c < 32; c++) begin
    for (m = 0; m < 256; m++) begin
        env_acc[c] += |mf[c*SPS + m*SPS + offset[m]]| * env_ref[m];
    end
end

// Coarse peak: p_coarse = argmax(env_acc)
```

**Cost**: 32 × 256 = 8192 **real** multiply-accumulates (vs. complex in current design)
- Real multiply ≈ 1/4 energy of complex multiply
- **25% of original cost**

#### Stage 2: Phase-Consistency Search (Fine Timing + CFO)

```verilog
// Narrow search around p_coarse: [p_coarse - 4, p_coarse + 4]
for (p = p_coarse - 4; p <= p_coarse + 4; p++) begin
    // Same as current cfo_est.sv, but only 8 candidates
    for (m = 0; m < 256; m++) {
        z[m] = mf[p + m*SPS + offset[m]] / c_ref[m];
        d[m] = z[m+2] * conj(z[m]);
    }
    acc[p] = sum(d);
    Q[p] = |acc[p]| / sum(|d|);
end
// Fine peak: p_fine = argmax(Q), f_hat = arg(acc[p_fine]) / (2π · 1µs)
```

**Cost**: 8 × 256 × (cost of phase consistency) ≈ **3% of original** (phase consistency is more expensive than envelope correlation)

**Total cost**: Stage 1 (25%) + Stage 2 (3%) = **28% of original search**

### Design Trade-offs

**Pros**:
- **~4× energy savings** on estimation
- Still **exact same accuracy** (fine stage is identical to current method)
- Envelope correlation is simpler (no complex multiplies)

**Cons**:
- **Two-pass** architecture → more control logic
- Envelope correlation **fails in multipath** (envelope peak may not align with coherent peak)
- Narrow window assumption requires **low timing jitter** from AGC/trigger

### When to Use

✅ **Use coarse-fine** if:
- **Battery-powered** application (energy-critical)
- **AWGN channel** (multipath rare or weak)
- **Reliable external trigger** (e.g., AGC-based `preamble_detect`)

❌ **Stick with full search** if:
- **Multipath-heavy** environment (indoor, urban)
- **Unreliable trigger** (high jitter or false alarms)
- **Energy not critical** (mains-powered base station)

---

## Enhancement 4: Diagnostic and Visibility Outputs

### Motivation: Debugging and System Monitoring

During development and field deployment, visibility into CFO correction helps diagnose:
- **Sync failures**: Was it CFO, noise, or interference?
- **Crystal drift**: Is the oscillator aging or temperature-sensitive?
- **Multipath**: Do different packets have inconsistent CFO estimates?

### Proposed Diagnostic Signals

Add to `cfo_est.sv`:

```verilog
// Per-candidate quality metrics (8 values)
output reg [15:0] Q_cand [0:7];        // Phase consistency Q for each candidate

// Magnitude-squared of correlation peak (before and after correction)
output reg [31:0] peak_mag2_raw;       // |Σ v·conj(c_ref)|² before CFO correction
output reg [31:0] peak_mag2_corrected; // After correction (should be higher)

// Second-best candidate quality (for ambiguity detection)
output reg [15:0] Q_second;            // Q of runner-up candidate
output reg [2:0]  p_second;            // Runner-up alignment

// Estimation confidence flag
output reg        high_confidence;     // (Q_best - Q_second) > threshold
```

### Use Cases

**1. Sync failure diagnosis**:
```
if (!sync_success) {
    if (Q_best < 0.25) {
        // Low phase consistency → likely NOT a valid preamble (noise or interference)
    } else if ((Q_best - Q_second) < 0.1) {
        // Ambiguous candidates → multipath or partial overlap
    } else if (peak_mag2_corrected < threshold) {
        // Even after CFO correction, peak is weak → noise or fading
    }
}
```

**2. Crystal monitoring** (log CFO estimates over time):
```python
# Host-side data analysis
cfo_estimates = np.array(logged_phase_inc) * FS / (2**24)  # Convert to Hz
plt.plot(timestamps, cfo_estimates)
plt.xlabel("Time [s]")
plt.ylabel("CFO [Hz]")
plt.title("Crystal drift over temperature cycle")
```

**3. Multipath detection**:
If multiple candidates have high Q (e.g., Q[0] = 0.8, Q[3] = 0.6),
likely two strong paths with relative delay ≈ 3 chips.

---

## Enhancement 5: Adaptive Estimation Window

### Problem: External Trigger Has Variable Delay

When using `preamble_detect` (AGC-based) as trigger:
- Trigger arrives **~460 samples after frame start** (measured)
- Jitter: ±10 samples
- Current fixed window (2048 samples) may **overshoot into data segment**

**Data contamination**: Data symbols are **not** the same as preamble → break phase consistency assumption.

### Solution: Adaptive Window Based on Trigger Quality

```verilog
// Configuration: trigger source
parameter TRIG_SRC = "internal";  // "internal" or "external"

// For external trigger, use shorter window to stay within preamble
localparam NSMP = (TRIG_SRC == "external") ? 1536 : 2048;
//   1536 samples = 192 chips (75% of preamble)
//   460 (trigger delay) + 1536 = 1996 < 2048 (preamble end)

// Quality scales with √N:
//   Full window (2048 smp): σ_f ≈ 8.4 kHz
//   Reduced window (1536):  σ_f ≈ 9.7 kHz  (+15% noise)
// Still well below 62.5 kHz tolerance.
```

**Alternative**: Dynamic window based on **detected trigger time**:
```verilog
wire [11:0] trig_time;  // Timestamp of trigger pulse
wire [11:0] window_end = 12'd2048 - trig_time;
// Stop collection when t == window_end
```

---

## Enhancement 6: Frequency-Domain Estimation (Research Direction)

### Alternative Approach: FFT-Based CFO Estimation

Current method (time-domain differential phase) is **optimal for short preambles** (256 chips).

For **very long preambles** or **initial acquisition**, frequency-domain methods may offer advantages.

### Algorithm Sketch

**Key idea**: CFO manifests as a **spectral shift** in the frequency domain.

1. **FFT of preamble** (2048-point FFT on received samples)
2. **FFT of reference** (pre-computed, stored in ROM)
3. **Cross-correlation** in frequency: `X_rx[k] · conj(X_ref[k])`
4. **Peak search** in cross-correlation → spectral shift in bins → CFO

**Advantages**:
- **Parallel processing**: FFT can leverage hardware accelerators (if available)
- **Wide capture range**: Can search ±FS/2 = ±8 MHz (entire baseband)
- **Robustness to timing jitter**: Frequency-domain is less sensitive to ±1 sample misalignment

**Disadvantages**:
- **FFT cost**: 2048-point complex FFT ≈ 22k complex operations (butterfly stages)
- **Overkill for ±96 kHz**: Bin spacing = 16 MHz / 2048 = 7.8 kHz → only need ±12 bins
- **Latency**: FFT pipeline (6-10 cycles/stage × 11 stages) >> simple accumulator

### When to Consider

Only if:
- System **already has FFT** (e.g., OFDM mode in a multi-standard radio)
- Need to handle **very large CFO** (>> ±100 kHz, e.g., Doppler in mobile systems)
- **Initial acquisition** scenario (don't know CFO at all, must search wide range)

For 802.15.4 with ±96 kHz spec, **time-domain is better**.

---

## Recommended Implementation Priority

For **next-stage enhancement**, prioritize by ROI:

### Tier 1: High Value, Low Cost
1. ✅ **Diagnostic outputs** (Section 4)
   - Cost: +10 signals, no logic change
   - Benefit: Debug visibility, production monitoring
   - **Implement immediately** in next RTL revision

2. ✅ **Adaptive window** (Section 5)
   - Cost: 1 parameter + 1 comparator
   - Benefit: Robustness to external trigger jitter
   - **Implement immediately** (already needed for `preamble_detect` integration)

### Tier 2: Medium Value, Medium Cost
3. 🔶 **Coarse-fine estimation** (Section 3)
   - Cost: +25% area (envelope correlation stage)
   - Benefit: 4× energy savings on estimation
   - **Implement** if targeting battery-powered endpoint

### Tier 3: High Cost, Application-Specific
4. ⚠️ **Decision-directed tracking** (Section 1)
   - Cost: +20% area (loop filter + phase detector)
   - Benefit: Long packet support (>64 bytes), coherent despreading
   - **Skip** for current 802.15.4 (non-coherent, short packets)
   - **Revisit** if upgrading to coherent receiver or proprietary long-packet mode

5. ⚠️ **Multi-frame learning** (Section 2)
   - Cost: 384-6144 bits SRAM + LUT logic
   - Benefit: Faster acquisition, outlier detection
   - **Skip** for single-channel star network
   - **Implement** for mesh network with many persistent links

6. ⛔ **FFT-based estimation** (Section 6)
   - Cost: 22k operations + FFT hardware
   - Benefit: Wide capture range (not needed here)
   - **Don't implement** (overkill for ±96 kHz)

---

## Conclusion

The current CFO correction architecture is **already excellent** for 802.15.4 short-packet bursts:
- Handles worst-case ±96 kHz with margin (tested to 450 kHz)
- One-shot estimation (no loops, no warmup)
- Cheap per-sample correction (1 complex multiply)

**Recommended additions**:
1. **Diagnostic outputs** → immediate value for debugging and monitoring
2. **Adaptive window** → needed for external trigger robustness
3. **Coarse-fine search** → optional, for battery-powered applications

**Skip** the heavyweight features (tracking loop, multi-frame learning, FFT) unless moving to
a **fundamentally different use case** (long packets, coherent despreading, or mesh networks).

---

## References and Further Reading

### Academic Papers
- **Luise & Reggiannini (1995)**: "Carrier frequency recovery in all-digital modems for burst-mode transmissions"
  - Classic reference on feedforward vs. feedback CFO estimation
- **Morelli & Mengali (1999)**: "Feedforward frequency estimation for PSK: A tutorial review"
  - Survey of non-data-aided (blind) vs. data-aided methods
- **Kay (1989)**: "A fast and accurate single frequency estimator"
  - Theory behind weighted phase-average estimators (WLSE)

### Industrial Standards
- **IEEE 802.15.4-2020**: Section 11.3 (2.4 GHz PHY)
  - Does NOT specify CFO correction (left to implementation)
  - Only specifies **transmitter** crystal tolerance (±40 ppm)
- **Zigbee Specification**: No mandatory CFO correction
  - Interoperability relies on robust (differential) physical layer

### Open-Source Implementations
- **gr-ieee802-15-4** (GNU Radio): Differential detection, no CFO estimation
- **Contiki-NG** (IoT OS): Software-defined radio, uses hardware CFO correction (if available)
- **OpenWSN**: MAC-layer only, delegates PHY to radio chip (e.g., CC2520)

### Internal Project Files
- `docs/11_文献调研_CFO与同步工程实践.md`: Literature survey (Chinese)
- `model/experiments/run_cfo_fix.py`: Model implementation and validation
- `rtl/rx/cfo_est.sv`: Current RTL implementation
- `tb/cfo_corr/`: Cocotb testbench with bit-true model comparison
