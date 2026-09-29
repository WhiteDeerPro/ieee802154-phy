# -*- coding: utf-8 -*-
"""
baseband.link —— 链路编排 (制式无关)
=====================================

把数字通信链路显式建模为「信号上下文 + 有序阶段」, 链上的每一个位置都是一个
**具名观测点**, 可在任意位置插入损伤 / 校正 / 探针:

    载荷 ─┬─ 扰码 ─┬─ 扩频 ─┬─ 成形 ─┬═[信道]═┬─ 同步 ─┬─ 匹配滤波 ─┬─ 解扩 ─┬─ 解帧 ─┬─ 载荷
          │        │        │        │         │        │            │        │        │
       payload → bits →  symbols → chips →   tx   →   rx   →   rx_chips → rx_symbols → rx_payload
                                          └────── 观测点 (probe points) ──────┘

三个概念:
  ``Signal``  一次链路运行的信号上下文; 每个具名字段就是一个观测点
  ``Stage``   一个处理步骤 ``fn(sig) -> None``, 原地更新 sig
  ``Chain``   阶段的有序集合, 分 ``tx`` / ``channel`` / ``rx`` 三段

设计约定:
  - **制式无关**: 本模块不认得 802.15.4 / QAM / QPSK, 只负责编排与观测点定义;
    具体阶段由制式实例 (如 ``phy_802154``) 组装成链路配方。换制式 = 换配方,
    本模块与 measure / visualize 均无需改动。
  - **阶段可插拔**: 损伤注入、校正、同步都是普通阶段。新增一种损伤 = 新增一个
    ``Stage``, 不必改动本模块或既有脚本 (见 ``impairments`` 的 ``*_stage`` 工厂)。
  - **观测点在 measure 层消费**: 星座图读 ``symbols`` / ``rx_symbols``, 眼图读 ``rx``,
    BER 读 ``rx_symbols`` vs ``symbols`` 或 ``rx_payload`` vs ``payload``,
    同步位置 / 估计量读 ``meta``。画图一律走 ``visualize``。

用法::

    chain = (Chain()
             .then_tx(framing, spreading, modulation)
             .then_channel(imp.cfo_stage(50e3), imp.awgn_stage(-1.0, pulse_energy))
             .then_rx(matched_filter, sync, despread))
    sig = chain.run(payload=psdu)
    print(measure.link_report(sig))
"""

from dataclasses import dataclass, field
from typing import Any, Callable


# ---------------------------------------------------------------------------
# 信号上下文
# ---------------------------------------------------------------------------

@dataclass
class Signal:
    """链路信号上下文 —— 每个字段是一个观测点。

    命名约定: ``rx_*`` 前缀表示接收机恢复出的量, 与发送端同名量**逐位/逐符号可
    直接比对** (``rx_symbols`` vs ``symbols``, ``rx_payload`` vs ``payload``),
    这正是 measure 层算 BER / EVM 的入口。

    字段值为 ``None`` 表示链路上该点尚未产生 (或该制式不经过此点, 例如不扩频的
    制式就不会有 ``chips``)。
    """
    # —— 发送方向 ——
    payload: Any = None      # 输入载荷 (如 PSDU 字节)
    bits: Any = None         # 码流: 扰码/编码后、调制前的最终比特流
    symbols: Any = None      # 基带码: 符号流 (扩频前)
    chips: Any = None        # 扩频后码片流
    tx: Any = None           # 发送波形 (复基带)
    # —— 信道 ——
    rx: Any = None           # 接收波形 (含损伤, 未修正)
    # —— 接收方向 ——
    rx_chips: Any = None     # 恢复码片软值
    rx_soft: Any = None      # 接收星座软值 (复数, 判决前) —— 星座图与 EVM 的对象
    rx_symbols: Any = None   # 恢复基带码 (符号, 判决后)
    rx_bits: Any = None      # 恢复码流
    rx_payload: Any = None   # 恢复载荷
    soft_ref: Any = None     # 参考星座点 (复数, 与 rx_soft 同尺度/同长) —— EVM 基准
    # —— 伴随信息 (同步位置 / 估计量 / 信道 / 探针 …) ——
    meta: dict = field(default_factory=dict)

    def probe(self, name: str) -> Any:
        """取一个由 ``probe()`` 阶段采集的快照 (不存在则 KeyError)。"""
        return self.meta["probes"][name]

    def has(self, name: str) -> bool:
        """某观测点是否已产生 (非 None)。"""
        return getattr(self, name, None) is not None

    def observables(self) -> list:
        """已产生的观测点名字列表 (按链路顺序), 便于自省与报告。"""
        order = ["payload", "bits", "symbols", "chips", "tx",
                 "rx", "rx_chips", "rx_soft", "rx_symbols", "rx_bits", "rx_payload"]
        return [n for n in order if self.has(n)]


# ---------------------------------------------------------------------------
# 阶段
# ---------------------------------------------------------------------------

Stage = Callable[[Signal], None]
"""阶段协议: ``fn(sig) -> None``, 原地更新 sig。

约定: 阶段 **不得修改输入数组的内容**, 只应「读旧值 → 算新值 → 赋值回 sig 字段」。
(``baseband`` 现有纯函数均满足此约定: ``sig.rx = add_cfo(sig.rx, ...)``。)
这样同一份 ``tx`` / ``rx`` 可以被多个阶段与探针安全共享。
"""


def stage(fn, *, field: str = "rx", name: str = None, **kw) -> Stage:
    """把纯函数 ``fn(x, **kw)`` 提升为一个链路阶段。

    这就是「留接口」的地方: 任何作用在某一观测点上的变换 —— 损伤注入、校正、
    滤波、检测 —— 都能一行接入链路, 而无需改动编排层。

    field = 阶段读写的 ``Signal`` 字段名 (默认 ``rx``: 信道段与接收段都在
            ``rx`` 上接力; 传 ``tx`` 可做发送端预处理)。
    name  = 阶段显示名 (缺省用函数名), 供 ``Chain.describe()`` 打印链路结构。
    kw    = 透传给 ``fn`` 的参数。值可以是常量, 也可以是 ``f(sig) -> value``
            的可调用对象 —— 后者在运行到该阶段时才求值, 用于「参数依赖链上
            前面阶段结果」的场景, 例如按估计出的 CFO 做逆重采样::

                stage(imp.add_sfo, ppm=lambda sig: -sig.meta["cfo_est"] / FC * 1e6)
    """
    def apply(sig: Signal) -> None:
        args = {k: (v(sig) if callable(v) else v) for k, v in kw.items()}
        setattr(sig, field, fn(getattr(sig, field), **args))

    apply.__name__ = name or f"{getattr(fn, '__name__', 'stage')}({field})"
    apply.__wrapped__ = fn
    return apply


def probe(name: str, field: str = "rx") -> Stage:
    """探针阶段: 把当前观测点快照存进 ``sig.meta['probes'][name]``。

    用于对比「同一信号在链路不同位置」的状态 (如 CFO 注入前 / 消旋后),
    而不必改动链上任何其它阶段。
    """
    def apply(sig: Signal) -> None:
        sig.meta.setdefault("probes", {})[name] = getattr(sig, field)

    apply.__name__ = f"probe({name})"
    return apply


# ---------------------------------------------------------------------------
# 链路
# ---------------------------------------------------------------------------

@dataclass
class Chain:
    """链路: 三段有序阶段 + 便捷构建 / 运行。

    tx       发送方向 (载荷 → 发送波形), 依次作用于 ``payload … tx``
    channel  信道      (发送波形 → 接收波形), 依次作用于 ``rx``
    rx       接收方向 (接收波形 → 恢复载荷), 依次作用于 ``rx … rx_payload``

    两个未配置阶段时也有明确语义: 空 tx 段 = 载荷已在上下文中就绪;
    空 channel 段 = 理想信道 (``rx = tx``, 无损伤); 空 rx 段 = 不做恢复。
    """
    tx: list = field(default_factory=list)
    channel: list = field(default_factory=list)
    rx: list = field(default_factory=list)
    name: str = "chain"

    # ---- 构建 (链式, 返回 self 便于连写) ----
    def then_tx(self, *stages: Stage) -> "Chain":
        self.tx.extend(stages)
        return self

    def then_channel(self, *stages: Stage) -> "Chain":
        self.channel.extend(stages)
        return self

    def then_rx(self, *stages: Stage) -> "Chain":
        self.rx.extend(stages)
        return self

    # ---- 运行 ----
    def transmit(self, sig: Signal) -> Signal:
        """跑发送段: 载荷 → 发送波形。"""
        for st in self.tx:
            st(sig)
        return sig

    def apply_channel(self, sig: Signal) -> Signal:
        """跑信道段: 发送波形 → 接收波形。

        信道入口约定 ``rx = tx`` (理想信道); 各损伤阶段在 ``rx`` 上依次接力,
        因此列表顺序即损伤作用顺序 (例如 多径 → AWGN → CFO → 定时)。
        """
        if sig.rx is None:
            sig.rx = sig.tx
        for st in self.channel:
            st(sig)
        return sig

    def receive(self, sig: Signal) -> Signal:
        """跑接收段: 接收波形 → 恢复载荷。"""
        for st in self.rx:
            st(sig)
        return sig

    def run(self, sig: Signal = None, **payload: Any) -> Signal:
        """跑完整链路, 返回信号上下文。

        sig     已有的上下文 (可预置 ``payload`` 或任意观测点); 缺省新建。
        payload 新建上下文时填入的字段, 如 ``run(payload=psdu)``。
        """
        if sig is None:
            sig = Signal(**payload)
        return self.receive(self.apply_channel(self.transmit(sig)))

    # ---- 自省 ----
    def describe(self) -> str:
        """链路结构的可读描述 (阶段名逐段列出)。"""
        def seg(stages):
            return " → ".join(getattr(s, "__name__", repr(s)) for s in stages) or "(直通)"
        return (f"[{self.name}]\n"
                f"  TX      : {seg(self.tx)}\n"
                f"  CHANNEL : {seg(self.channel)}\n"
                f"  RX      : {seg(self.rx)}")

    def __len__(self) -> int:
        return len(self.tx) + len(self.channel) + len(self.rx)


def chain_from(recipe: dict, name: str = "chain") -> Chain:
    """由 ``{"tx": [...], "channel": [...], "rx": [...]}`` 字典构建链路。

    便于把「链路配方」写成数据 (可存配置 / 可 diff), 而不是代码。
    """
    return Chain(tx=list(recipe.get("tx", [])),
                 channel=list(recipe.get("channel", [])),
                 rx=list(recipe.get("rx", [])),
                 name=name)
