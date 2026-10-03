# synth_frame 报告（RX 全程三段式）

- ①决策码流: seed=77, 3 帧 × PSDU 20B
- ②合成: n_smp=46038, CKS=000e2e89, clip_frac=0.0e+00
- ③接入: simv_ps_csq（双路）; FRMA=[0, 1, 2] FRMB=[]
- payload（解算 BYTA vs 决策 PSDU, 逐帧逐字节）:
  帧0: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧1: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧2: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
- **PASS**