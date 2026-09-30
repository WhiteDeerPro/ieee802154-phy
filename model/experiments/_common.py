# -*- coding: utf-8 -*-
"""_common.py —— 实验脚本公共样板

本目录（experiments/）的脚本原本各自复制三类样板；这里统一收口，**新脚本请直接用**，
存量脚本的迁移清单见 `docs/08` I-21。

统一的三件事：

1. matplotlib 后端（无显示环境必须 Agg）与中文字体；
2. 输出目录 ``model/out/<name>/`` 的创建；
3. 需要完整"实验实例"能力（report.md / 标准图件）时，用 ``model/instance.py``
   的 ``Instance``（它已覆盖第 2 项并附 report 汇总）。

脚本头部用法::

    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 引导 model/
    import _common

    plt = _common.init()                     # Agg + 中文字体，返回 pyplot
    OUT = _common.out_dir("impairments")     # model/out/impairments/（已创建）

注：上面 `sys.path` 那两行无法省 —— 库模块（chains / measure / phy_802154 /
visualize）在上一级 ``model/``，而 Python 只自动把脚本自身目录加入 sys.path；
``_common`` 位于本目录（experiments/），引导后直接可见。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent     # model/
OUT_ROOT = ROOT / "out"


def init(cjk=True):
    """matplotlib 初始化：Agg 后端 + 中文字体（幂等）。返回 pyplot。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    if cjk:
        import visualize
        visualize.use_cjk_font()
    return plt


def out_dir(name):
    """返回 ``model/out/<name>/``（不存在则创建）。"""
    d = OUT_ROOT / name
    d.mkdir(parents=True, exist_ok=True)
    return d
