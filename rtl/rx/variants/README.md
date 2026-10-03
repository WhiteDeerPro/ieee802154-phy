# rtl/rx/variants/ —— 接收链变体目录

> 变体 = 同接口/同时序的替代实现，由构建宏或源替换选择。
> 换装方式与主用关系见 `rtl/README.md` 与 `rtl/filelist.f`；
> 演进记录 `docs/23`；实验日志 `model/out/dual_mc/rtl_area_notes.md`；
> 集中评估 `model/out/dual_mc/oct8_phase/README.md`。

## despreader 度量变体（2026-10-04 演进）

| 文件 | 状态 | 结构 | 门级 cells (yosys synth) | 验证 |
|---|---|---|---|---|
| `despreader_oct8.sv` | 候选（MODE=1 = "零代价"档） | 8 边形幅度检测 (15/16, 15/32)；MODE=0/1/2 | 20,191 | 单元 bit-exact；整链五场景+200 帧零差；相位精扫 14.3% 波纹 |
| `despreader_oct8_pipe.sv` | 候选（时序储备） | 同 oct8 + 四级流水判决树（参考 `ip/mcdf`） | 20,759 | 单元逐符号一致（延迟 3 拍）；组合路径 46→16 |
| **`despreader_oct8_tdm.sv`** | **现役（2026-10-04 起）** | PN 列重排 + 判决串行化（4 拍×4 路）；MODE 参数 | **12,016 (MODE=2) / 13,227 (MODE=1)** | 单元 bit-exact；全回归 ALL PASS；200 帧 s20 零差、s6 −1（四边形代价） |
| `despreader_tdm.sv` | 候选（旧 TDM，已被超越） | 平方/argmax 时分复用（保留精确平方） | 26,776 | 判决逐符号一致 |

**现役基线（2026-10-04 切换）**：`build_variants.py` 的 `simv_ps_csq` =
同步器 csq + `+define+DESP_OCT8_TDM +DESP_QUAD`（**TDM + 四边形**）。

- **代价实测**：200 帧 @ snr6 相对原版 **−1 帧**（57 vs 58；字节流 4/60 帧差异）；
  snr20 200/200 零差；60 帧判据场景全部持平（全回归 ALL PASS）。
- **换档/回滚**：去掉 `+DESP_QUAD` → TDM + 八边形（13,227 cells，200 帧零差；
  全链约 +2.4k cells）。

## 同步器变体（主用 `rx/frontend/preamble_sync_csq.sv`；备选在此）

| 文件 | 状态 | 说明 |
|---|---|---|
| `preamble_sync.sv` | 对照（A16 历史基线） | `simv_ps_a16` / 影子对比 |
| `preamble_sync_b8.sv` | 次候选（已被超越） | 16→8 相位变体 |
| `preamble_sync_ref.sv` | 候选（未评估） | A16 的共享延迟线优化版 |
