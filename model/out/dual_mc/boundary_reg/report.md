# 边界回归报告

## 1. 注入边界（snr20 帧 9：±{0,1000,1500,3000}）

| simv_ps_a16 | +0 | OK |
| simv_ps_a16 | +1000 | OK |
| simv_ps_a16 | +1500 | OK |
| simv_ps_a16 | +3000 | OK |
| simv_ps_csq | +0 | OK |
| simv_ps_csq | +1000 | OK |
| simv_ps_csq | +1500 | OK |
| simv_ps_csq | +3000 | OK |

## 2. edge 抖动扫描（140 帧）

- simv_ps_a16: 94/140（判据 ≥93） PASS
- simv_ps_csq: 94/140（判据 ≥92） PASS

## 3. 主流量

- snr20 simv_ps_a16: FRMA 60/60（判据 ≥59） PASS
- snr20 simv_ps_csq: FRMA 60/60（判据 ≥59） PASS
- snr6 simv_ps_a16: FRMA 17/60（判据 ≥16） PASS
- snr6 simv_ps_csq: FRMA 17/60（判据 ≥16） PASS
- mixdev1 simv_ps_a16: FA 127 / FB 50（判据 FA≥126, FB≥49） PASS
- mixdev1 simv_ps_csq: FA 128 / FB 50（判据 FA≥126, FB≥49） PASS

**总结: ALL PASS**（耗时 162s）