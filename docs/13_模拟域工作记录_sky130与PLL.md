# 模拟域工作记录：sky130 环境、标准单元与开源 PLL 跑通

> 2026-09-30 一轮模拟域工作的汇总。核心成果：**sky130 真模型环境打通**
> （ngspice-47 + volare PDK + 最小加载集），并**跑通两个开放项目**
> （`wulffern/sun_pll_sky130nm` 的环振与完整 PLL）。
> 本文是"下次继续"的入口文档：环境在哪、坑在哪、资产在哪、下一步做什么。

---

## 0. 一句话

数字侧之外，模拟侧现在也有一条**可复现、秒级出图**的最小通路：
**ngspice-47（自编译）+ sky130 PDK（volare）+ 最小加载集（0.17 s）**。
用它跑通了 sun_pll 的 8 级环振（Kvco 曲线）与完整 PLL——8MHz 参考版锁定
**258.5 MHz**（÷32，目标 256，误差 1%）。

---

## 1. 环境清单（下次直接用）

| 件 | 位置 / 命令 | 备注 |
|---|---|---|
| **ngspice 47** | `~/opt/ngspice/bin/ngspice` | 自编译；configure 参数：`--prefix=$HOME/opt/ngspice --without-x --disable-debug --without-readline`；含 KLU + OSDI（本机缺 readline 头文件，批处理无影响） |
| **sky130 PDK** | `~/.volare/volare/sky130/versions/c6d73a35…/sky130A` | volare 装的（927MB，sky130A+B，含 ngspice/xschem/magic/klayout 库）；版本号是 commit hash，`sky130A` 只是 variant |
| **Python** | 项目根 `.venv/bin/python`（或 `uv run`） | 有 numpy / matplotlib |
| **仿真库** | `analog/sky130/sky130_lib.py` | PDK 自动定位 / 器件 include 组合 / **带心跳的 runner** |

## 2. 最小加载集（核心配方）

```spice
.param mc_mm_switch=0
.param mc_pr_switch=0
.option scale=1.0u
.include "<PDK>/libs.tech/ngspice/all.spice"                      ; 工艺参数
.include "<PDK>/libs.tech/ngspice/corners/tt/nonfet.spice"        ; 非 FET 参数
.include "<PDK>/libs.ref/sky130_fd_pr/spice/<器件>__tt.pm3.spice"         ; 器件模型
.include "<PDK>/libs.ref/sky130_fd_pr/spice/<器件>__mismatch.corner.spice" ; 参数配对，缺一不可
```

- R/C 用 `<PDK>/libs.tech/ngspice/r+c/res_typical__cap_typical{,_lin}.spice`
  （`res_generic_m3/l1` 等的定义源是它 include 的 `sky130_fd_pr__model__r+c.model.spice`）
- 器件名：`sky130_fd_pr__nfet_01v8` / `pfet_01v8`（+`_lvt`/`_hvt`）/ `res_high_po` / `res_generic_m3` …

## 3. 坑列表（按杀伤力排序）

1. **deck 第一行必须是注释**——否则被当 title 吃掉（`.param` 静默失效，极难查）
2. **模型与 mismatch 文件必须成对 include**——只 incl 模型报 `Undefined parameter [xxx_slope]`
3. **加载库段必须用 `.lib <file> <section>`**——用 `.include <file> <section>` 会把 8 个
   corner 段全部解析（CPU 100% 持续 7 分钟）最后仍报错退出；`.lib` 方式 20 s 完成
4. **`.option scale=1.0u` 必须**；`mc_mm_switch`/`mc_pr_switch` 必须自行定义 0
5. **PDK 独立 `res_high_po.model.spice` 与 ngspice-47 不兼容**（报 "12 formal but 2 actual
   params" / `Syntax error: letter [$]`）——**不要 include 它**，r+c 链里有正常版本
6. **tran 使用 `.ic` 初值必须加 `uic`**——否则工作点解会把初值抹平（环振不起振的元凶之一）
7. **子电路内部节点访问要带实例前缀**：`v(XDUT.VLPF)`、`v(xdut.N_0)`——直接 `v(VLPF)` 报 "no such vector"
8. **LPE 网表带 `$ **FLOATING` 行内注释**——ngspice 不吃 `$`，需清洗（脚本已自动处理）
9. **环振的"环"要闭合**：N 级反相器接成环时，第 N 级输出必须回到第 1 级输入（接错成悬空节点 → 永不振荡）
10. 加载时间 ≈ 库文件数 × 0.2 s（全库 675 文件）；**仿真时间与电路规模相关，加载只与库规模相关**——ngspice 每次启动都重新解析，没有缓存

## 4. 资产清单（`analog/sky130/`）

| 文件 | 是什么 |
|---|---|
| `sky130_lib.py` | 仿真库：PDK 定位 / 器件 include / **心跳 runner**（长任务每 5–15 s 报进度+内存） |
| `mos_curves.py` | NMOS Id-Vg / Id-Vd 特性曲线（秒级出图） |
| `stdcell_demo.py` | inv_1 VTC + 5 级环振（拓扑尺寸抄自 PDK 的 LVS 网表 `cdl/sky130_fd_sc_hd.cdl`） |
| `sun_pll_design.spice` | sun_pll 的 Sch 视图网表（391 行，MIT，未改动） |
| `sun_pll_lpe.spi` | sun_pll 的 LPE 完整版图网表（1311 行，349 MOS + 74 R + 881 C，MIT，未改动） |
| `sun_pll_rosc.py` / `sun_pll_rosc.png` | 环振 Kvco 扫描（VDD 1.1→1.6V，6 点 6 秒） |
| `sun_pll_full.py` / `sun_pll_full.png` | 完整 PLL 全程瞬态 + 锁定动态四联图（含 `--plot-only` 模式） |
| `sun_pll_doc/` | 项目自带渲染：顶层原理图 SVG（"16 MHz x 32 = 512 MHz PLL"）、版图 PNG、PFD 图 |

## 5. 结果速查

| 实验 | 结果 |
|---|---|
| NMOS Id-Vg（W/L=2/0.15） | Vgs=1.8V → **1.03 mA**（与 Id-Vd 交叉验证一致） |
| inv_1 VTC | **Vm=0.79 V**，直流增益 18.5（dc step 10mV 限制分辨率） |
| 5 级环振（inv_1 等效） | **3.91 GHz**，tp=25.6 ps/级（无负载） |
| sun_pll ROSC | VDD 1.1→1.6V → 150→697 MHz，**Kvco≈1.15 GHz/V**（项目 README 自述"Kvco 太高"，吻合） |
| sun_pll 完整 PLL（8MHz ref） | **锁定 258.5 MHz**（÷32 → 目标 256，误差 1%）；VDD_ROSC=1.279V(std 9mV) |
| sun_pll 完整 PLL（16MHz ref） | 锁定 472 MHz（目标 512，-7.6%）；VLPF=1.788V 近电源轨 = 推力饱和 |

## 6. 待办 / 已知问题

- **16MHz 版 PLL 未达 512 MHz**：VLPF 顶到近电源轨，疑似 `reltol=1e-3`（原版 1e-4）偏松或
  BUF 驱动不足；可收紧 reltol 复跑对照。项目自身也有 known issue（slow corner 下 OTA 驱动不足），同类型
- **SG13G2 遗留**：模型是 PSP103（需 OSDI 动态库 `psp103.osdi`），当前未拿到该文件，
  SG13G2 项目暂停在模型加载阶段；sky130 路线不受影响
- **模拟域可继续**：混频器/PA/LNA 的管级仿真（配合 ZigBee 上下变频，见 doc 11 与 CC2420 架构）、
  ADC（可克隆 `wulffern/sun_sar9b_sky130nm`）
- **数字侧衔接**：模拟侧 LO 频率误差 ↔ 模型 **CFO**（`rtl/rx/cfo_est.sv` 的对象）；
  ÷2 正交失配 ↔ **IQ imbalance**（`model/algo/dc_block.py` 抑制的对象）

## 7. 外部参考

- sun_pll 项目：https://github.com/wulffern/sun_pll_sky130nm （MIT, Carsten Wulff）
- 架构参考：TI **CC2420** datasheet —— 低中频 2MHz + **4.8GHz VCO ÷2 产生正交 LO**（IQ 混频）；
  802.15.4 收发机不做零中频的原因：OQPSK 半正弦成型在载波中心有能量，直接落在 DC offset / 1-f 噪声上
