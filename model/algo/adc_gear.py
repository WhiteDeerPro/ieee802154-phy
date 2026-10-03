# -*- coding: utf-8 -*-
"""
algo.adc_gear —— ADC 档位调节（gear shifting）参考实现
======================================================

为什么需要: SAR ADC 功耗随分辨率近线性/显著增长（docs/21 §9/§14; TI slyt605）——
按环境动态选档可省而不伤性能。核心难点"鸡生蛋"（选档要 SNR 估计、估计要 ADC
数据）按"不对称原则"处理（docs/21 §14.2）:

    ① 进入（降档）仅由高精度（12bit）状态发起 —— 低档禁链式自降;
    ② 离开（升档）用失败类信号 —— FCS/同步失败在低精度下依然可信;
    ③ TTL 复查 = 无条件回顶 —— 低档为限时租约; 回顶后由常规评估重新降档。

接口（RTL 镜像: rtl/rx/top/adc_gear_ctrl.sv, bit-true）:
    g = AdcGear(th8=18, th4=20, k_fail=3, ttl=200)
    gear = g.on_frame(snr_valid, snr_est, fcs_ok)
事件约定: 每帧边界调用一次 on_frame;
    snr_valid — 该帧携带高精度评估（仅 gear==12 时上游应给出）;
    snr_est   — 评估值（无符号 dB, 0..255）;
    fcs_ok    — 帧结果（失败类信号无条件可信）。
输出 gear ∈ {12, 8, 4}（请求值; ADC 切换由上层执行）; 复位值 12。
复杂度: 每帧数个比较/加一 —— 无乘除。
"""


class AdcGear:
    GEAR_12, GEAR_8, GEAR_4 = 12, 8, 4

    def __init__(self, th8=18, th4=20, k_fail=3, ttl=200):
        self.th8, self.th4, self.k_fail, self.ttl = th8, th4, k_fail, ttl
        self.reset()

    def reset(self):
        self.gear = self.GEAR_12
        self.fail_cnt = 0
        self.ttl_cnt = 0

    def on_frame(self, snr_valid=False, snr_est=0, fcs_ok=True, force=False):
        """帧边界事件。返回请求档位。与 RTL 逐位一致（含计数器更新时序）。

        force: 外部强制（电平, 帧边界采样）——**覆盖自动判断**：无条件回全态（12）
               并清计数（即便基带自行评估认为可维持低档）。
        """
        if force:
            self.gear = self.GEAR_12
            self.fail_cnt = 0
            self.ttl_cnt = 0
            return self.gear

        # 失败计数无条件更新（失败类信号任何时候可信）
        self.fail_cnt = 0 if fcs_ok else self.fail_cnt + 1

        if self.gear == self.GEAR_12:
            self.ttl_cnt = 0                       # 高精度发起降档（禁链式: 仅 12 档评估）
            if snr_valid and snr_est > self.th4:
                self.gear = self.GEAR_4
            elif snr_valid and snr_est > self.th8:
                self.gear = self.GEAR_8
        else:
            if self.fail_cnt >= self.k_fail:       # 离开①: 失败触发（阶梯升档）
                self.gear = self.GEAR_8 if self.gear == self.GEAR_4 else self.GEAR_12
                self.fail_cnt = 0
                self.ttl_cnt = 0
            elif self.ttl_cnt + 1 >= self.ttl:     # 离开②: TTL 到期 → 无条件回顶
                self.gear = self.GEAR_12
                self.ttl_cnt = 0
            else:
                self.ttl_cnt += 1
        return self.gear
