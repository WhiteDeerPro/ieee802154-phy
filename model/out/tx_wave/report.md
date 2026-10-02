# TX 发射波形检查（数字域）

- 数据: tb/tx_framer 的 tx_chain bit-true 测试导出（RTL 采样, 已与黄金逐位匹配）
- PSDU: `0180a55a00ff42c31199`; 符号数 36; I/Q 对齐偏移 12/12
- FCS 符号(末 4): ['0xe', '0xd', '0x0', '0xe']
- 带宽: **-3dB 1.27 MHz / -20dB 5.10 MHz**（基带, 采样率 16 MHz）

## 图
- `tx_wave_check.png`: ①时域 ②包络 ③频谱 ④I-Q 轨迹

## 说明
- 该波形即 RTL 发射链（tx_framer+oqpsk_modulator）数字输出；bit-true 已对拍黄金模型,
  本检查提供直观印证与谱宽测量；**模拟域质量（EVM/杂散/掩模）需在 analog 侧验证**。
