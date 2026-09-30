# -*- coding: utf-8 -*-
"""observer.py —— 链路观测器: 跨帧证据累积 + 捕获状态机 + 控制输出

定位（docs/12 的"上层"、`docs/16` 的详细设计）
==================================================================
把接收机看作一个**动态系统**：

    观测量 (每帧)              待估状态              控制量
    估计值 cfo + 质量 q   →    链路当前参数状态  →   消旋值/门限/观测粒度
    同步/解帧成败               (旋性, 定时, ...)     (给基带矫正模块)

`patterns.py` 提供"状态表 + 匹配/发现"（帧级、无状态）；
本模块补上另一半：**跨帧证据累积 + 捕获状态机 + 控制决策**。

多状态并存（与网络现实对齐）
==================================================================
网络拓扑稳定 ⇒ 一个节点只与受限数量的对端通信 ⇒ 它看到的物理状态是
**有限集且切换不频繁**。且**状态（分辨单元）比设备少**：一群晶振相近的
设备落在同一个分辨单元里；少数离群参数需要单独服务。

因此观测器**为每个状态维护独立的状态机**（不是"全局只有一个 LOCK"）:
一帧观测先按容差归到某个候选，候选各自累积证据、各自迁移状态；
输出控制时选"当前帧最相关"的那个（匹配到的，或最近活跃的）。

单状态机的框架（捕获理论: GNSS 的 Search→Verify→Lock）不变：

    SEARCH ──证据达标──> VERIFY ──连续命中──> LOCK
       ↑                    │证据衰减            │ 连续失效
       └────────── LOST ←───┴────────────────────┘

- **弱证据累积**：每帧一条质量加权的证据（泄漏积分，近期优先）；
  低质量帧不硬丢弃、只给小权重 —— 实测未消旋下估计"宽而有中心"
  （`model/out/observer/report.md`：累积后 0.9 kHz 误差 vs 单帧硬判决 78.5 kHz）。
- **捕获确认**：M/N 连续命中后才 LOCK（抗"高质量假象"）。
- **热启动**：LOCK 过的状态写回持久表 —— 同源再现直接给 `cfo_hint`。

本模块是 **ref**（`model/upper/`），不追求 ASIC 级实现成本。
"""
from dataclasses import dataclass, field

import numpy as np


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class FrameObservation:
    """一帧的观测。字段全部可选 —— 有什么传什么，观测器按可用信息降级。"""
    idx: int = 0                     # 帧序号（可选，缺省由观测器自增）
    cfo: float | None = None         # CFO 估计（Hz）
    quality: float = 0.0             # 该估计的质量（0..1，如 cfo_est 的 cf）
    sync_ok: bool = False            # 本帧同步是否成功
    fcs_ok: bool = False             # 本帧解帧是否成功（端到端真值，若可得）


@dataclass
class Control:
    """观测器给基带矫正模块的控制输出。"""
    state: str                        # SEARCH / VERIFY / LOCK / LOST
    cfo_hint: float | None = None     # 建议消旋值（Hz）
    want_raw: bool = False            # SEARCH/LOST: 请求上传原始前导（供发现）
    confidence: float = 0.0           # 当前状态置信度（0..1）
    note: str = ""


@dataclass
class _Candidate:
    """一个链路状态候选（分辨单元），自带状态机。"""
    cfo: float
    ev: float = 0.0                   # 累积证据（单位: 等效满分帧数）
    hits: int = 0                     # 累计命中
    streak: int = 0                   # 当前连续命中
    last: int = -1                    # 最近命中的帧序号
    conf: float = 0.0                 # 最近一次观测质量
    born: int = 0
    state: str = "SEARCH"             # 本候选自己的状态


# ---------------------------------------------------------------------------
# 观测器
# ---------------------------------------------------------------------------

class LinkObserver:
    """多状态观测器：每状态独立证据累积与状态机，输出当前帧的控制。

    参数
      q_floor     质量地板（权重零点）。默认 0.25，与 cfo_est 的质量门控同量级。
      acquire_th  进入 VERIFY 所需的累积证据（单位 = 等效满分帧数）。
      decay       每帧证据衰减（近期优先）。0.92 → 半衰期 ≈ 8.3 帧。
      verify_hits VERIFY -> LOCK 需要的连续命中帧数。
      lost_run    LOCK -> LOST 需要的连续失联帧数。
      tol_hz      分辨单元宽（候选聚类容差）。**决定状态数**：容差越粗，
                  越多的设备被同一个状态服务（但残余频差也越大）。
      adapt       LOCK 态的 EMA 跟踪系数。
      max_cands   候选上限（超出按最久未用淘汰）。
      age_frames  未命中候选的存活帧数（LOCK 候选豁免）。
    """

    SEARCH, VERIFY, LOCK, LOST = "SEARCH", "VERIFY", "LOCK", "LOST"

    def __init__(self, q_floor=0.25, acquire_th=1.5, decay=0.92,
                 verify_hits=2, lost_run=3, tol_hz=10e3, adapt=0.25,
                 max_cands=8, age_frames=200):
        self.q_floor = float(q_floor)
        self.acquire_th = float(acquire_th)
        self.decay = float(decay)
        self.verify_hits = int(verify_hits)
        self.lost_run = int(lost_run)
        self.tol_hz = float(tol_hz)
        self.adapt = float(adapt)
        self.max_cands = int(max_cands)
        self.age_frames = int(age_frames)

        self.cands: list[_Candidate] = []
        self._frame = 0
        self.stats = dict(frames=0, acquired=0, lost=0)
        self.history: dict[int, float] = {}      # 持久记忆（热启动）

    # ------------------------------------------------------------- 主入口
    def observe(self, obs: FrameObservation) -> Control:
        self._frame += 1
        idx = obs.idx if obs.idx else self._frame
        self.stats["frames"] += 1

        # 1) 统一衰减（近期优先）
        for c in self.cands:
            c.ev *= self.decay

        # 2) 本帧证据 -> 归入候选（质量加权）
        matched = None
        w = self._weight(obs.quality)
        if obs.cfo is not None and w > 0.0:
            c = self._nearest(obs.cfo)
            if c is None:
                c = _Candidate(cfo=obs.cfo, born=idx)
                self.cands.append(c)
            c.ev += w
            c.hits += 1
            c.streak += 1
            c.last = idx
            c.conf = obs.quality
            if c.state == self.LOCK and self.adapt > 0:
                c.cfo += self.adapt * (obs.cfo - c.cfo)      # EMA 跟踪
            matched = c

        # 3) 状态迁移（每候选独立）
        for c in self.cands:
            if c is not matched:
                c.streak = 0
            self._advance(c, idx)

        # 4) 淘汰
        self._evict(idx)

        # 5) 控制输出
        return self._control(matched)

    # ------------------------------------------------------------- 内部
    def _weight(self, q):
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

    def _advance(self, c, idx):
        if c.state == self.SEARCH:
            if c.ev >= self.acquire_th:
                c.state = self.VERIFY
        elif c.state == self.VERIFY:
            if c.streak >= self.verify_hits:
                c.state = self.LOCK
                self.stats["acquired"] += 1
                self.history[self._key(c.cfo)] = c.cfo
            elif c.ev < self.acquire_th * 0.5:
                c.state = self.SEARCH          # 证据衰减 -> 退回（抗虚警）
        elif c.state == self.LOCK:
            if idx - c.last >= self.lost_run:
                c.state = self.LOST
                self.stats["lost"] += 1
        elif c.state == self.LOST:
            if c.ev >= self.acquire_th:
                c.state = self.VERIFY          # 温启动重捕获

    def _evict(self, idx):
        keep = []
        for c in self.cands:
            if c.state == self.LOCK or idx - c.last <= self.age_frames:
                keep.append(c)
        self.cands = keep
        while len(self.cands) > self.max_cands:
            self.cands.remove(min(self.cands, key=lambda c: c.last))

    def _key(self, cfo):
        return int(round(cfo / self.tol_hz))

    def _control(self, matched):
        if matched is not None:
            c = matched
            if c.state == self.LOCK:
                return Control(state=self.LOCK, cfo_hint=c.cfo,
                               confidence=min(1.0, c.ev / (2 * self.acquire_th)),
                               note=f"hits={c.hits}")
            if c.state == self.VERIFY:
                return Control(state=self.VERIFY, cfo_hint=c.cfo,
                               confidence=min(0.5, c.ev / (2 * self.acquire_th)),
                               note="试探消旋（粗）")
            return Control(state=self.SEARCH, want_raw=True,
                           note=f"候选累积 {c.ev:.2f}/{self.acquire_th}")
        # 未匹配：报告最相关的保留状态（下一帧很可能仍来自同一对端）
        locked = [c for c in self.cands if c.state == self.LOCK]
        if locked:
            c = max(locked, key=lambda x: x.last)
            return Control(state=self.LOCK, cfo_hint=c.cfo,
                           confidence=min(1.0, c.ev / (2 * self.acquire_th)),
                           note="hold（无本帧观测）")
        lost = [c for c in self.cands if c.state == self.LOST]
        if lost:
            c = max(lost, key=lambda x: x.last)
            return Control(state=self.LOST, cfo_hint=None, want_raw=True,
                           note="重捕获中")
        return Control(state=self.SEARCH, want_raw=True, note="无匹配")

    # ------------------------------------------------------------- 热启动
    def warm_start(self, cfo, conf=1.0):
        """用已知状态（历史/上层/MAC）直接初始化一个 LOCK 候选。"""
        c = _Candidate(cfo=float(cfo), ev=2 * self.acquire_th, hits=1,
                       streak=1, conf=conf, last=self._frame, state=self.LOCK)
        self.cands.append(c)
        self.history[self._key(c.cfo)] = c.cfo
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
    def locked_states(self):
        """已锁定的状态列表（分辨单元的中心值）。"""
        return sorted(c.cfo for c in self.cands if c.state == self.LOCK)

    def summary(self):
        from collections import Counter
        cnt = Counter(c.state for c in self.cands)
        return dict(state=dict(cnt), candidates=len(self.cands),
                    memory=len(self.history), **self.stats)
