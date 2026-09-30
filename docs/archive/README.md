# archive —— 历史素材（不生效）

本目录存放**已失效或已被整合**的文档，仅作溯源之用，**不要作为工作依据**。

## cfo_2026-09-30/

6 篇英文草稿（2026-09-30 23:36–23:52 批量生成）：

`CFO_Action_Plan.md` · `CFO_Analysis_and_Solution.md` · `CFO_Architecture_Diagram.txt` ·
`CFO_Enhanced_Block_Design.md` · `CFO_README.md` · `CFO_Summary_and_Next_Steps.md`

**2026-10-01 审计结论**（逐篇核对 `model/out/*/report.md` 与 `docs/08/11/12/14`）：

- **有效内容已整合**：物理量级（7.8/62.5/500 kHz）、联合估计算法、三个坑、BER/精度表、
  COLLECT/CMP/CORDA/CORDB 结构、位宽 —— 全部吸收进
  [`docs/15_CFO估计与消旋_实测结论与上层分工.md`](../../15_CFO估计与消旋_实测结论与上层分工.md)。
- **其余部分不可作为依据**：
  - Week 1–4 排期从未执行，且前提有误（详见 docs/15 开头与本文档落款处的占位日期）；
  - 把已实测失效的修法当作待办或"已创建"：投票累积器、两次/三次一致采纳、`cfo_est_diag.sv`
    （该文件零引用，2026-10-01 已删除）、RTL 内按节点 `cfo_memory` 表；
  - 与 `docs/12` 的架构决策冲突（旋性的记忆/匹配归上层，单元只做消旋）；
  - 引用不存在的文件、不存在的命令行参数（如 `run_cfo_fix.py --mode/--cfo_range`）、
    不存在的团队与占位日期。

**保留原因**：原始素材溯源。确认不再需要时，删除本目录即可。
