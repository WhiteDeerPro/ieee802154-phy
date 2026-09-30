# -*- coding: utf-8 -*-
"""observer.py —— 链路观测器: 跨帧证据累积 + 捕获状态机 + 控制输出

定位（docs/12 的"上层"、docs/15 §6）
==================================================================
把接收机看作一个**动态系统**：

    观测量 (每帧)              待估状态              控制量
    估计值 cfo + 质量 q   →    链路当前参数状态  →   消旋值/门限/观测粒度
    同步/解帧成败               (旋性, 定时, ...)     (给基带矫正模块)

`patterns.py` 提供"状态表 + 匹配/发现"（帧级、无状态）；
本模块补上另一半：**跨帧证据累积 + 捕获状态机 + 控制决策**。

为什么需要状态机（理论依据：GNSS 捕获的 Search→Verify→Lock 结构）
==================================================================
单帧判决在低 SNR / 未消旋下不可靠 —— 本项目实测：冷启动要 ~15 帧才碰到
一次高质量估计（`model/out/cfo_trigger/report.md` §9.5，前 14 帧 cf≤0.26
被质量门控拒、第 15 帧 cf=0.89 才捕获）。

正确做法不是"提高单帧灵敏度"（那会抬高虚警），而是三层结构：

1. **弱证据累积**：每帧只贡献"质量加权"的一份证据（泄漏积分，近期优先）；
   低质量帧不像 RTL 那样被硬丢弃，而是权重小——未消旋下的估计"宽而有中心"
   （实测中位数 72.3 kHz vs 真值 100 kHz），累积仍有信息；
2. **捕获确认**：证据够强才进 VERIFY，连续命中才 LOCK（M/N 确认，抗虚警）；
3. **热启动**：LOCK 过的状态写回持久表——下次同源直接给出 `cfo_hint`，
   捕获从 ~15 帧降到 ~1 帧（对应 GNSS 的 hot start：砍掉搜索空间）。

状态机::

    SEARCH ──证据达标──> VERIFY ──连续命中──> LOCK
       ↑                    │证据衰减            │ 连续失效
       └────────── LOST ←───┴────────────────────┘
                 (保留历史, 重捕获走"温启动")

本模块是 **ref**（`model/upper/`），不追求 ASIC 级成本；先把状态机、证据模型
与控制接口跑通，供固件/主机侧实现参考。
"""
from dataclasses import dataclass, field

import numpy as np


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class FrameObservation:
    """一帧的观测。字段全部可选 —— 有什么传什么，观测器按可用信息降级。

    cfo/quality 来自基带估计器（如 `cfo_est` 的 phase_inc 与 cf）；
    sync_ok/fcs_ok 是端到端成败（用于 LOCK 态的健康度判断）。
    """
    idx: int = 0                     # 帧序号（调用方可选，缺省由观测器自增）
    cfo: float | None = None         # CFO 估计（Hz）
    quality: float = 0.0             # 该估计的质量（0..1，如 cfo_est 的 cf）
    sync_ok: bool = False            # 本帧同步是否成功
    fcs_ok: bool = False             # 本帧解帧是否成功（端到端真值，若可得）


@dataclass
class Control:
    """观测器给基带矫正模块的控制输出。"""
    state: str                        # SEARCH / VERIFY / LOCK / LOST
    cfo_hint: float | None = None     # 建议消旋值（Hz）；LOCK 全信，VERIFY 试探
    want_raw: bool = False            # SEARCH/LOST: 请求上传原始前导（供 FFT 发现）
    confidence: float = 0.0           # 当前状态置信度（0..1）
    note: str = ""


@dataclass
class _Candidate:
    """一个待确认的链路状态候选（旋性）。"""
    cfo: float
    ev: float = 0.0                   # 累积证据（单位: 等效满分帧数）
    hits: int = 0                     # 累计命中帧数
    streak: int = 0                   # 当前连续命中帧数
    last: int = -1                    # 最近命中的帧序号
    conf: float = 0.0                 # 最近一次观测质量


# ---------------------------------------------------------------------------
# 观测器
# ---------------------------------------------------------------------------

class LinkObserver:
    """跨帧观测器：证据累积 -> 状态机 -> 控制输出。

    参数
      q_floor     质量地板：q 低于它视为无信息（权重 0）。默认 0.25，与非相干
                  解扩的判决门限同一量级；设 0 则"全用"（未消旋下不推荐）。
      acquire_th  进入 VERIFY 所需的累积证据（单位 = 等效满分帧数）。
                  1.5 ≈ 1.5 个满分帧，或 3 个 q=0.75 的帧。
      decay       每帧证据衰减（近期优先）。0.92 → 半衰期 ≈ 8.3 帧。
      verify_hits VERIFY -> LOCK 需要的连续命中帧数（M/N 的 M）。
      lost_run    LOCK -> LOST 需要的连续失联帧数。
      tol_hz      候选聚类容差（Hz）。落到容差外的估计另起候选。
      adapt       LOCK 态的 EMA 跟踪系数（0 = 不跟踪）。
    """

    SEARCH, VERIFY, LOCK, LOST = "SEARCH", "VERIFY", "LOCK", "LOST"

    def __init__(self, q_floor=0.25, acquire_th=1.5, decay=0.92,
                 verify_hits=2, lost_run=3, tol_hz=10e3, adapt=0.25):
        self.q_floor = float(q_floor)
        self.acquire_th = float(acquire_th)
        self.decay = float(decay)
        self.verify_hits = int(verify_hits)
        self.lost_run = int(lost_run)
        self.tol_hz = float(tol_hz)
        self.adapt = float(adapt)

        self.state = self.SEARCH
        self.cands: list[_Candidate] = []
        self.cur: _Candidate | None = None      # VERIFY/LOCK 指向的候选
        self._frame = 0

        # 统计（供报告）
        self.stats = dict(frames=0, acquired_at=None, lost=0, relock=0)
        # 持久记忆：LOCK 过的状态（热启动用）
        self.history: dict[int, float] = {}     # key -> cfo (Hz)

    # ------------------------------------------------------------- 主入口
    def observe(self, obs: FrameObservation) -> Control:
        """喂一帧观测，返回给基带的控制。"""
        self._frame += 1
        idx = obs.idx if obs.idx else self._frame
        self.stats["frames"] += 1

        # 1) 证据衰减（近期优先）：所有候选统一衰减，命中的再加
        for c in self.cands:
            c.ev *= self.decay

        # 2) 本帧证据：质量加权（低质量给小权重，不硬丢弃）
        w = self._weight(obs.quality)
        matched = None
        if obs.cfo is not None and w > 0.0:
            c = self._nearest(obs.cfo)
            if c is None:
                c = _Candidate(cfo=obs.cfo)
                self.cands.append(c)
            c.ev += w
            c.hits += 1
            c.streak += 1
            c.last = idx
            c.conf = obs.quality
            # LOCK 态 EMA 跟踪（跟随慢漂移）
            if c is self.cur and self.state == self.LOCK and self.adapt > 0:
                c.cfo += self.adapt * (obs.cfo - c.cfo)
            matched = c
        # 未被本帧支持的候选：连续命中链断（这是 M/N 里的"N"）
        for c in self.cands:
            if c is not matched:
                c.streak = 0

        # 3) 状态迁移
        self._transition(idx, obs, matched)

        # 4) 控制输出
        return self._control(obs)

    # ------------------------------------------------------------- 内部
    def _weight(self, q):
        """质量 -> 证据权重，归一化到 [0,1]。"""
        if q <= self.q_floor:
            return 0.0
        return (q - self.q_floor) / (1.0 - self.q_floor)

    def _nearest(self, cfo):
        best, bd = None, self.tol_hz
        for c in self.cands:
            d = abs(c.cfo - cfo)
            if d <= bd:
                best, bd = c, d
        return best

    def _best(self):
        return max(self.cands, key=lambda c: c.ev, default=None)

    def _transition(self, idx, obs, matched):
        if self.state in (self.SEARCH, self.LOST):
            b = self._best()
            if b is not None and b.ev >= self.acquire_th:
                self.state = self.VERIFY
                self.cur = b

        elif self.state == self.VERIFY:
            if matched is self.cur and self.cur.streak >= self.verify_hits:
                self.state = self.LOCK
                # 写回持久记忆（热启动）
                self.history[int(round(self.cur.cfo / self.tol_hz))] = self.cur.cfo
                if self.stats["acquired_at"] is None:
                    self.stats["acquired_at"] = idx
            elif self.cur is not None and self.cur.ev < self.acquire_th * 0.5:
                # 证据衰减到不足一半 -> 退回搜索（抗虚警）
                self.state = self.SEARCH
                self.cur = None

        elif self.state == self.LOCK:
            if self.cur is None or idx - self.cur.last >= self.lost_run:
                self.state = self.LOST
                self.cur = None
                self.stats["lost"] += 1

    def _control(self, obs):
        if self.state == self.LOCK:
            c = self.cur
            return Control(state=self.LOCK, cfo_hint=c.cfo,
                           confidence=min(1.0, c.ev / (2 * self.acquire_th)),
                           note=f"hits={c.hits}")
        if self.state == self.VERIFY:
            c = self.cur
            return Control(state=self.VERIFY, cfo_hint=c.cfo,
                           confidence=min(0.5, c.ev / (2 * self.acquire_th)),
                           note="试探消旋（粗）")
        # SEARCH / LOST
        return Control(state=self.state, want_raw=True,
                       confidence=0.0,
                       note="需要全信息观测（原始前导）")

    # ------------------------------------------------------------- 热启动
    def warm_start(self, cfo, conf=1.0):
        """用已知状态（来自历史/上层/MAC）直接初始化。

        对应 GNSS 的 hot start：把搜索空间从"全频段"缩到一个点。
        """
        c = _Candidate(cfo=float(cfo), ev=2 * self.acquire_th,
                       hits=1, streak=1, conf=conf, last=self._frame)
        self.cands = [c]
        self.cur = c
        self.state = self.LOCK
        return Control(state=self.LOCK, cfo_hint=c.cfo, confidence=conf,
                       note="warm start")

    def recall(self, near_hz=None):
        """从持久记忆里找一个状态（可给近似值）；没有则 None。"""
        if not self.history:
            return None
        if near_hz is None:
            return max(self.history.values())
        best, bd = None, None
        for v in self.history.values():
            d = abs(v - near_hz)
            if bd is None or d < bd:
                best, bd = v, d
        return best

    # ------------------------------------------------------------- 只读视图
    def summary(self):
        return dict(state=self.state, candidates=len(self.cands),
                    memory=len(self.history), **self.stats)
