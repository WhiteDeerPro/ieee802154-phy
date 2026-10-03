# -*- coding: utf-8 -*-
"""
algo.adc_gear —— ADC 档位调节（gear shifting）参考实现
======================================================

为什么需要: SAR ADC 功耗随分辨率近线性/显著增长（docs/21 §9/§14; TI slyt605）——
按环境动态选档可省而不伤性能。核心难点"鸡生蛋"（选档要 SNR 估计、估计要 ADC
数据）按"不对称原则"处理（docs/21 §14.2）:

    ① 进入（降档）仅由最高精度（16）状态发起 —— 低档禁链式自降;
    ② 离开（升档）用失败类信号 —— FCS/同步失败在低精度下依然可信;
    ③ TTL 复查 = 无条件回顶 —— 低档为限时租约; 回顶后由常规评估重新降档。

档位集（用户口径修正 2026-10-03）: **{16, 12, 8, 4}** ——
    16 = 全精度档（链 W=16 "满精度可改回 21" 的参考配置）;
    12 = ADC 原生数据精度;
    8/4 = 省电降档。复位值 = 16。

接口（RTL 镜像: rtl/rx/top/adc_gear_ctrl.sv, bit-true）:
    g = AdcGear(th12=0, th8=18, th4=20, k_fail=3, ttl=200)
    gear = g.on_frame(snr_valid, snr_est, fcs_ok, force)
事件约定: 每帧边界调用一次 on_frame;
    snr_valid — 该帧携带高精度评估（仅 gear==16 时上游应给出）;
    snr_est   — 评估值（无符号 dB, 0..255）;
    fcs_ok    — 帧结果（失败类信号无条件可信）;
    force     — 外部强制（电平, 帧边界采样）: 覆盖自动判断, 无条件回 16 并清计数。
复杂度: 每帧数个比较/加一 —— 无乘除。
"""


class AdcGear:
    GEAR_16, GEAR_12, GEAR_8, GEAR_4 = 16, 12, 8, 4

    def __init__(self, th12=0, th8=18, th4=20, k_fail=3, ttl=200):
        self.th12, self.th8, self.th4 = th12, th8, th4
        self.k_fail, self.ttl = k_fail, ttl
        self.reset()

    def reset(self):
        self.gear = self.GEAR_16
        self.fail_cnt = 0
        self.ttl_cnt = 0

    def _step_up(self, gear):
        """阶梯升档: 4→8→12→16。"""
        return {self.GEAR_4: self.GEAR_8, self.GEAR_8: self.GEAR_12,
                self.GEAR_12: self.GEAR_16}[gear]

    def on_frame(self, snr_valid=False, snr_est=0, fcs_ok=True, force=False):
        """帧边界事件。返回请求档位。与 RTL 逐位一致（含计数器更新时序）。"""
        if force:
            self.gear = self.GEAR_16
            self.fail_cnt = 0
            self.ttl_cnt = 0
            return self.gear

        # 失败计数无条件更新（失败类信号任何时候可信）
        self.fail_cnt = 0 if fcs_ok else self.fail_cnt + 1

        if self.gear == self.GEAR_16:
            # 最高档评估 → 机会主义降档（禁链式: 仅最高档评估）
            self.ttl_cnt = 0
            if snr_valid and snr_est > self.th4:
                self.gear = self.GEAR_4
            elif snr_valid and snr_est > self.th8:
                self.gear = self.GEAR_8
            elif snr_valid and snr_est > self.th12:
                self.gear = self.GEAR_12
        else:
            if self.fail_cnt >= self.k_fail:       # 离开①: 失败触发（阶梯升档）
                self.gear = self._step_up(self.gear)
                self.fail_cnt = 0
                self.ttl_cnt = 0
            elif self.ttl_cnt + 1 >= self.ttl:     # 离开②: TTL 到期 → 无条件回顶
                self.gear = self.GEAR_16
                self.ttl_cnt = 0
            else:
                self.ttl_cnt += 1
        return self.gear
