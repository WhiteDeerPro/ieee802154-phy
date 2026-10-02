# RTL 总览（v1.1）

`rx_dual` = **共享前端 + 双通道执行段**：单芯片两个接收器（A/B 各覆盖一台设备/一类参数），
共享一份匹配滤波前端。数字侧接收链（802.15.4 2.4 GHz PHY, O-QPSK+DSSS, 250 kbps）。

## 目录结构

- `common/`：`half_sine_fir`（匹配滤波核）/ `pn9_whiten` / `crc16_fcs` / `chip_lut` /
  `awgn_gen`（CLT 数字 AWGN 注入源，测试/标定基础设施；`docs/20` §4）
- `rx/frontend/`：`rx_frontend`（共享前端：MF → 扫描同步+相位 latch → `preamble_detect`
  → `preamble_buf` → `deinterleave`）+ 同步器候选族（见下）
- `rx/backend/`：`rx_chip_backend`（`cfo_rot` → `sfd_detect` → `despreader` → `rx_deframer`）× 2
- `rx/top/`：`rx_dual`（顶层）
- `rx/legacy/`：历史/参考实现（**勿删**；各文件头有 [状态] 标注）
- `tx/`：`tx_framer` / `oqpsk_modulator`

## 同步器版本（`rx/frontend/`）

| 文件 | 状态 | 说明 |
|---|---|---|
| `preamble_sync_csq.sv` | **现役（v1.2 起, I-18 转正）** | Csq v3b：能量定相 + 连续滑窗相干；6,456 cells（−23% vs A16）；回归 + 影子对比打平；**默认 SOURCES** |
| `preamble_sync.sv` | **对照（A16 历史基线）** | 保留作对照变体（`simv_ps_a16` / 影子对比）；默认编译已切 Csq |
| `preamble_sync_b8.sv` | 次候选（已被超越） | 16→8 相位变体；面积/性能均不优于 Csq，保留对照 |
| `preamble_sync_ref.sv` | 候选（未评估） | A16 的共享延迟线优化版（notes §13 方案） |

选用方式：`tb/rx_dual_mc/build_variants.py`（编译 `simv_ps_*`）；评估/切换流程见 `docs/18`、`docs/19`。

## 关键参数与设计点

- `W=16` 定点（全链重规划口径，见 `model/out/dual_mc/rtl_area_notes.md` §8）；
  16 MHz 主时钟、8 采样/码片、码片率 2 Mchip/s；
- `preamble_buf`：**1024 环 + BFP8 块浮点**（存储 −84%，量化 SNR 41.7 dB，BLK 旋钮 16/64/128 已验证）
  ——设计/验证记录见 notes §37–§40；
- 面积口径（yosys 逻辑级）：全设计 13,917 cells / 39,776 membits（Csq v3b 版）；
  A16 版 36,919 cells。**仅供规模参考，非签核数据**。

## 仿真 / 验证入口（`tb/rx_dual_mc/` 为主）

- 边界回归：`run_boundary_regression.py`（注入 8/8 + edge + 主流量；ALL PASS 基线）；
- 蒙特卡洛：`run_dual_mc.py`（多设备交替发帧 → 双通道 FCS 统计）；
- 同步器变体编译：`build_variants.py`（A16/Csq）；
- pbuf/BFP 专项：`diag_pbuf_bfp.py`、`run_blk_variants.py`（+`PBUF_DBG`/`PBUF_RAWCOL` 调试开关）；
- 记录总表：`model/out/dual_mc/rtl_area_notes.md`（§1–§40，设计与实验日志）。
