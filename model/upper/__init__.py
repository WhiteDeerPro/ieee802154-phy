# -*- coding: utf-8 -*-
"""upper —— 上层 / 主机侧组件 (不属于物理层)

这里放"可以不在 Zigbee 单元内完成"的东西: 参数记忆、模式匹配、跨帧累积等。
物理层只负责执行 (如消旋), 详见 docs/12_分层与职责边界_旋性与参数管理.md。
"""
from . import patterns

__all__ = ["patterns"]
