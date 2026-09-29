# -*- coding: utf-8 -*-
"""
baseband —— 通用基带算法库 (制式无关)

与具体制式 (802.15.4 / O-QPSK) 解耦的基带信号处理组件, 供 phy_802154 等
制式实例复用; 将来接入 BLE 或其它 OQPSK/PSK 系统可直接调用本包。

分层 (数字通信「码域 → 波形域」):
  码域 (离散 ±1/整数):
    coding         CRC + LFSR 扰码
    spreading      DSSS 扩频/解扩
  波形域 (复基带):
    modulation     成形脉冲 + O-QPSK 调制 + 匹配滤波
    channels       AWGN / 多径 / Rayleigh 信道
    impairments    CFO/定时/DC/IQ/量化/SFO 损伤注入
    sync           消旋 correct_cfo (载波同步)
    frontend       DC/IQ 联合 LS 前端校正
    equalization   信道估计 LS + 频域 MMSE 均衡
    link           链路编排 (Signal / Stage / Chain) —— 把上面这些接成完整链路

分层参考: CommPy 的 channels/filters/modulation/impairments 平铺,
          MATLAB Communications Toolbox 的调制器/信道/同步器/均衡器对象。
"""

from . import (coding, spreading, modulation, channels, impairments, sync,
               frontend, equalization, link)

__all__ = ["coding", "spreading", "modulation", "channels", "impairments",
           "sync", "frontend", "equalization", "link"]
