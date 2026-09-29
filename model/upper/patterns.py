# -*- coding: utf-8 -*-
"""patterns.py —— 接收模式库: 记忆 / 匹配 / 发现 / 淘汰 (制式无关)

动机 (docs/08 I-7 与讨论记录)
------------------------------------------------------------------
接收机面对的不是"每帧独立未知的信道", 而是**有限且切换不频繁的模式集**:
网络拓扑稳定 ⇒ 一个节点只与受限数量的对端通信 ⇒ 每个对端对应一组稳定的
物理参数 (旋性/定时相位/轴), 且会被后续数千帧复用。

于是把"每帧盲估"换成"模式库"的四个动作:

    匹配 (match)      已有模式里谁对当前帧负责 —— O(N) 次归一化相关, 最便宜
    发现 (discover)   都不匹配时才调 FFT 从当前帧学一个新模式 —— 最贵
    淘汰 (evict)      长期未命中的模式移除 —— 有限记忆
    关联 (addr)       由上层 (MAC) 回填"这个模式属于谁" —— 本层不猜

成本与"不确定度 x 复用次数"成正比: 状态明确时只做便宜匹配; 状态模糊时才
付出昂贵发现。发现出来的参数会被后续数千帧复用, 摊销后极低。

注意: 这里假定"记忆与匹配可以由节点自身承担"(软件/固件), 因此不追求
ASIC 级别的实现成本 —— 本模块是 ref, 用于把数据结构与策略先跑通。
"""
from dataclasses import dataclass

import numpy as np


@dataclass
class Pattern:
    """一个接收模式: 一组会被后续多帧复用的物理参数。"""
    cfo: float                  # 载波频偏 (Hz) —— "旋性"
    phase: int                  # 抽取相位 (码片峰在码片周期内的偏移)
    hits: int = 0               # 命中次数
    born: int = 0               # 建立时的帧序号
    last_seen: int = 0          # 最近命中的帧序号
    addr: object = None         # 上层标识 (MAC 地址等), 由上层回填
    conf: float = 0.0           # 最近一次匹配度 (归一化相关)

    def key(self):
        return (int(round(self.cfo)), int(self.phase))

    def __str__(self):
        a = f" addr={self.addr}" if self.addr is not None else ""
        return (f"[cfo={self.cfo/1e3:+.1f}kHz phase={self.phase} "
                f"hits={self.hits} conf={self.conf:.2f}{a}]")


class PatternLibrary:
    """模式库: 匹配优先, 失配才发现, 长期不用则淘汰。

    参数
      reference     已知前导的码片参考软值 (复数数组, 一个符号周期的若干倍)
      sps           每码片采样数
      fs            采样率 (Hz)
      max_patterns  库容量 (有限记忆)
      tol_hz        两个模式视为"同一个"的频率容差
      match_th      匹配门限 (归一化相关, 0..1)
      age_frames    多少帧未命中则淘汰
    """

    def __init__(self, reference, sps=8, fs=16e6, max_patterns=4,
                 match_th=0.45, age_frames=200, disc_offsets=24, win=None,
                 tol_hz=8e3):
        self.ref = np.asarray(reference, dtype=complex)
        self.n = len(self.ref)
        self.sps = int(sps)
        self.fs = float(fs)
        self.max_patterns = int(max_patterns)
        self.match_th = float(match_th)
        self.tol_hz = float(tol_hz)
        self.age_frames = int(age_frames)
        self.disc_offsets = int(disc_offsets)
        self.win = np.hanning(self.n) if win is None else np.asarray(win)
        self.patterns = []
        self.stats = dict(match=0, discover=0, miss=0)
        self._n = 0          # 内部帧计数 (调用方未给 frame_idx 时自增)
        # FFT bin -> CFO (码片级序列, 等效采样率 = 码片率)
        self.bin_hz = self.fs / (self.n * self.sps)

    # ---------------------------------------------------------------- 基础
    def _extract(self, mf, f0, phase):
        idx = f0 + phase + self.sps * np.arange(self.n)
        if idx[-1] >= len(mf):
            return None
        return mf[idx]

    def score(self, mf, f0, cfo, phase):
        """归一化相关: |Σ z·conj(ref)| / (‖z‖·‖ref‖), ∈ [0,1]。"""
        v = self._extract(mf, f0, phase)
        if v is None:
            return 0.0
        m = np.arange(self.n)
        z = v * np.exp(-1j * 2 * np.pi * cfo * self.sps * m / self.fs)
        num = abs(np.sum(z * np.conj(self.ref)))
        den = np.sqrt(np.sum(np.abs(z) ** 2) * np.sum(np.abs(self.ref) ** 2)) + 1e-30
        return float(num / den)

    # ---------------------------------------------------------------- 匹配
    def match(self, mf, f0, frame_idx=None):
        """在已有模式里找最匹配者; 返回 (pattern|None, 分数)。"""
        best_p, best_s = None, 0.0
        for p in self.patterns:
            s = self.score(mf, f0, p.cfo, p.phase)
            if s > best_s:
                best_p, best_s = p, s
        if best_p is not None and best_s >= self.match_th:
            best_p.hits += 1
            best_p.conf = best_s
            if frame_idx is not None:
                best_p.last_seen = frame_idx
            return best_p, best_s
        return None, best_s

    # ---------------------------------------------------------------- 发现
    def discover(self, mf, f0, frame_idx=None):
        """FFT + 抽取相位众数表决 -> 新模式的 (cfo, phase)。

        众数而非谱锐度: 实测正确值会在连续多个抽取相位上重复出现,
        而错误值散落在边缘 (见 model/out/cfo_fft/report.md)。
        """
        ests = []
        for off in range(self.disc_offsets):
            v = self._extract(mf, f0, off)
            if v is None:
                continue
            z = v * np.conj(self.ref) * self.win
            sp = np.abs(np.fft.fft(z)) ** 2
            k = int(np.argmax(sp))
            kh = k if k <= self.n // 2 else k - self.n
            ests.append((kh * self.bin_hz, off))
        if not ests:
            return None
        bins = np.round(np.array([e[0] for e in ests]) / self.bin_hz).astype(int)
        vals, cnt = np.unique(bins, return_counts=True)
        k = vals[int(np.argmax(cnt))]
        cfo = k * self.bin_hz
        # 该 bin 内出现最多的抽取相位
        ph = [e[1] for e in ests if int(round(e[0] / self.bin_hz)) == k]
        phase = int(np.bincount(ph).argmax()) % self.sps
        # ---- 去重: 已存在接近的模式就只**更新**它, 不新建 ----
        # 不做的后果 (实测): 某个设备的匹配分数在门限附近抖动时,
        # 每次跌破门限都会 discover 出一个重复模式 (曾经 4 个设备变成 6 条记录)。
        for p in self.patterns:
            if abs(p.cfo - cfo) <= self.tol_hz and p.phase == phase:
                p.hits += 1
                if frame_idx is not None:
                    p.last_seen = frame_idx
                p.conf = self.score(mf, f0, p.cfo, p.phase)
                return p
        p = Pattern(cfo=cfo, phase=phase, hits=1,
                    born=frame_idx or 0, last_seen=frame_idx or 0)
        p.conf = self.score(mf, f0, p.cfo, p.phase)
        self.patterns.append(p)
        self.evict(frame_idx or 0)
        return p

    # ---------------------------------------------------------------- 淘汰
    def evict(self, frame_idx):
        """长期未命中的移除; 超容量时丢最久未用。"""
        if self.age_frames > 0:
            self.patterns = [p for p in self.patterns
                             if frame_idx - p.last_seen <= self.age_frames]
        while len(self.patterns) > self.max_patterns:
            self.patterns.remove(min(self.patterns, key=lambda p: p.last_seen))

    # ---------------------------------------------------------------- 主入口
    def observe(self, mf, f0, frame_idx=None):
        """匹配优先; 失配则发现。返回 (pattern, 分数, 动作)。"""
        if frame_idx is None:
            frame_idx = self._n
        self._n += 1
        p, s = self.match(mf, f0, frame_idx)
        if p is not None:
            self.stats["match"] += 1
            return p, s, "match"
        self.stats["miss"] += 1
        p = self.discover(mf, f0, frame_idx)
        if p is None:
            return None, s, "fail"
        self.stats["discover"] += 1
        return p, p.conf, "discover"


def load_reference(odd=None):
    """从 rtl/rx/c_ref_rom.svh 载入理想前导的码片软值 (256 项)。

    放在本模块是因为它只依赖文件格式, 与制式无关; 调用方决定用不用。
    """
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    txt = (root / "rtl" / "rx" / "c_ref_rom.svh").read_text()
    out = {}
    for name in ("C_REF_I", "C_REF_Q"):
        m = re.search(name + r"\s*\[0:255\]\s*=\s*'\{(.*?)\};", txt, re.S)
        vals = re.findall(r"16'sh([0-9A-Fa-f]+)", m.group(1))
        v = np.array([int(x, 16) for x in vals], dtype=np.int64)
        v = np.where(v >= 2 ** 15, v - 2 ** 16, v)
        out[name] = v.astype(float)
    return out["C_REF_I"] + 1j * out["C_REF_Q"]
