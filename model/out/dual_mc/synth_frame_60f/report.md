# synth_frame 报告（RX 全程三段式）

- ①决策码流: seed=21, 60 帧 × PSDU 20B
- ②合成: n_smp=920860, CKS=00d221f7, clip_frac=0.0e+00
- ③接入: simv_ps_csq（双路）; FRMA=[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59] FRMB=[]
- payload（解算 BYTA vs 决策 PSDU, 逐帧逐字节）:
  帧0: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧1: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧2: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧3: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧4: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧5: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧6: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧7: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧8: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧9: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧10: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧11: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧12: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧13: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧14: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧15: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧16: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧17: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧18: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧19: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧20: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧21: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧22: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧23: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧24: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧25: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧26: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧27: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧28: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧29: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧30: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧31: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧32: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧33: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧34: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧35: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧36: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧37: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧38: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧39: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧40: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧41: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧42: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧43: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧44: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧45: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧46: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧47: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧48: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧49: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧50: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧51: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧52: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧53: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧54: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧55: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧56: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧57: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧58: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
  帧59: 解算 BYTA 20B / 决策 PSDU 20B → 逐字节一致 ✓
- **PASS**