# -*- coding: utf-8 -*-
"""test_observer.py —— 链路观测器的状态机行为测试（合成观测, 秒级）

覆盖:
  1. 连续好帧 -> 快速捕获 (SEARCH -> VERIFY -> LOCK)
  2. 单帧高分不会锁定 (抗虚警: 证据不够)
  3. LOCK 后短时失联不丢锁 (扛 lost_run-1 帧)
  4. 连续失联 -> LOST -> 重捕获 (温启动)
  5. warm_start: 热启动直接 LOCK
  6. 场景切换: 状态跳变 -> 旧状态失锁、新状态重锁
  7. 多设备交替: 各自状态分别 LOCK (多状态并存)
  8. 共享分辨单元: 相近设备落入同一状态 (状态数 < 设备数)
运行: python model/tests/test_observer.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from upper.observer import FrameObservation, LinkObserver  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  OK   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}  {detail}")


def obs(i, cfo=None, q=0.0):
    return FrameObservation(idx=i, cfo=cfo, quality=q, sync_ok=q > 0.5)


def test_acquire_clean():
    print("== 1. 连续好帧 -> 捕获 ==")
    ob = LinkObserver()
    states = [ob.observe(obs(i, 100e3, 0.9)).state for i in range(1, 5)]
    check("前 2 帧在 SEARCH/VERIFY 之间", states[0] in ("SEARCH", "VERIFY"), states)
    check("第 3 帧内 LOCK", "LOCK" in states, states)
    c = ob.observe(obs(5, 100e3, 0.9))
    check("锁定值正确", abs(c.cfo_hint - 100e3) < 5e3, c.cfo_hint)
    check("捕获已计数", ob.stats["acquired"] >= 1)


def test_no_single_shot():
    print("== 2. 单帧高分不锁定（抗虚警）==")
    ob = LinkObserver()
    ob.observe(obs(1, 100e3, 0.9))
    states = [ob.observe(obs(i)).state for i in range(2, 8)]
    check("后续无观测时始终未 LOCK", all(s != "LOCK" for s in states), states)


def test_hold_through_glitch():
    print("== 3. LOCK 后短时失联不丢锁 ==")
    ob = LinkObserver()
    for i in range(1, 5):
        ob.observe(obs(i, 100e3, 0.9))
    s1 = ob.observe(obs(5)).state
    s2 = ob.observe(obs(6)).state
    check("失联 2 帧仍 LOCK", s1 == "LOCK" and s2 == "LOCK", (s1, s2))


def test_lost_and_relock():
    print("== 4. 连续失联 -> LOST -> 重捕获 ==")
    ob = LinkObserver()
    for i in range(1, 5):
        ob.observe(obs(i, 100e3, 0.9))
    ob.observe(obs(5))
    ob.observe(obs(6))
    s_lost = ob.observe(obs(7)).state
    check("失联 3 帧转 LOST", s_lost == "LOST", s_lost)
    seq = [ob.observe(obs(i, 100e3, 0.9)).state for i in range(8, 11)]
    check("恢复后 3 帧内重锁", "LOCK" in seq, seq)


def test_warm_start():
    print("== 5. 热启动 ==")
    ob = LinkObserver()
    c = ob.warm_start(100e3)
    check("warm_start 立即 LOCK", c.state == "LOCK" and abs(c.cfo_hint - 100e3) < 1, c)
    ob2 = LinkObserver()
    for i in range(1, 5):
        ob2.observe(obs(i, 77e3, 0.9))
    got = ob2.recall(near_hz=76e3)
    check("recall 能取回历史状态", got is not None and abs(got - 77e3) < 5e3, got)


def test_scene_switch():
    print("== 6. 状态跳变（旧失锁、新重锁）==")
    ob = LinkObserver()
    for i in range(1, 5):
        ob.observe(obs(i, 100e3, 0.9))
    states, hints = [], []
    for i in range(5, 14):
        c = ob.observe(obs(i, 200e3, 0.9))
        states.append(c.state)
        hints.append(c.cfo_hint)
    check("最终重新 LOCK", states[-1] == "LOCK", states)
    check("锁定到新值 200 kHz",
          hints[-1] is not None and abs(hints[-1] - 200e3) < 5e3, hints[-1])
    check("旧状态确已失锁", ob.stats["lost"] >= 1, ob.stats)


def test_multi_device():
    print("== 7. 多设备交替：各自状态分别 LOCK ==")
    ob = LinkObserver(tol_hz=10e3)
    # 设备 A: 50 kHz（常发）, B: 120 kHz（常发）, C: 260 kHz（离群, 偶发）
    stream = [50e3, 50e3, 120e3, 50e3, 260e3, 50e3, 120e3, 50e3, 50e3, 120e3,
              50e3, 50e3, 120e3, 50e3, 50e3, 120e3, 120e3, 50e3]
    for i, f in enumerate(stream, 1):
        ob.observe(obs(i, f, 0.9))
    locked = ob.locked_states()
    check("至少 2 个状态进入 LOCK", len(locked) >= 2, locked)
    check("50 kHz 附近有锁定状态", any(abs(f - 50e3) < 6e3 for f in locked), locked)
    check("120 kHz 附近有锁定状态", any(abs(f - 120e3) < 6e3 for f in locked), locked)


def test_shared_state():
    print("== 8. 共享分辨单元（状态数 < 设备数）==")
    ob = LinkObserver(tol_hz=12e3)          # 分辨单元 12 kHz
    # 三个晶振相近的设备: 50 / 55 / 58 kHz —— 应落进同一状态
    stream = [50e3, 55e3, 58e3, 50e3, 55e3, 58e3, 50e3, 55e3, 58e3, 50e3]
    for i, f in enumerate(stream, 1):
        ob.observe(obs(i, f, 0.9))
    locked = ob.locked_states()
    check("三个相近设备共享 1 个锁定状态", len(locked) == 1, locked)
    check("状态中心在群中心附近", abs(locked[0] - 54e3) < 8e3, locked)


if __name__ == "__main__":
    for fn in (test_acquire_clean, test_no_single_shot, test_hold_through_glitch,
               test_lost_and_relock, test_warm_start, test_scene_switch,
               test_multi_device, test_shared_state):
        fn()
    print(f"\nRESULT: {PASS}/{PASS+FAIL} PASS")
    sys.exit(1 if FAIL else 0)
