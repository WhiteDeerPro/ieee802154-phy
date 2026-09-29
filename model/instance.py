# -*- coding: utf-8 -*-
"""
instance —— 通信实验实例: 一次实验 = 一个自包含的产出目录
============================================================

规范: 每个通信实验是一个**实例**, 产出到 ``out/<实例名>/``, 内含该实验的全套观测 ——
读者不必重跑就能看懂「这条链路发生了什么」:

    out/<实例名>/
      ├─ report.md          分析结论与关键数值 (close() 时自动汇总)
      ├─ bits.txt           基带码片段 (过长自动截断并标注)
      ├─ symbols.csv        符号序列
      ├─ waveform.png       时域: 发射 / 信道 / 接收 各阶段波形
      ├─ spectrum.png       频谱 (幅度)
      ├─ energy.png         能量谱 / PSD (dB/Hz)
      ├─ filters.png        滤波器: 成形脉冲与匹配滤波的频率响应
      ├─ noise.png          加噪对照: 全带白噪声 vs 带限(带内)噪声
      ├─ constellation.png  星座图
      └─ eye.png            眼图 (可选)

调用顺序自由; **未调用的产出不会出现在目录里** —— 短实验不必凑满全套。
所有图件的信号处理都复用 ``measure`` / ``visualize``, 本单位只负责组织与落盘。

用法::

    inst = Instance("qpsk_link", fs=16e6)
    inst.bits(tx_bits)
    inst.symbols(syms)
    inst.waveform({"① 发射": tx, "② 信道": rx})
    inst.spectrum(rx); inst.energy(rx)
    inst.filters({"成形 (RC)": pulse})
    inst.noise(tx, full, band)
    inst.constellation(soft)
    inst.eye(rx, period=10)
    inst.note("判决 BER = 0")
    inst.close()
"""

from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import measure
import visualize

OUT_ROOT = Path(__file__).resolve().parent / "out"


class Instance:
    """一次通信实验的实例: 自管目录 + 标准产出。"""

    def __init__(self, name, root=None, fs=None, title=None):
        self.name = name
        self.dir = (Path(root) / name) if root else (OUT_ROOT / name)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.fs = fs
        self.title = title or name
        self.notes = []
        self.tables = []
        self.produced = []
        visualize.use_cjk_font()

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _save(self, fig, name, dpi=130):
        p = self.dir / name
        fig.tight_layout()
        fig.savefig(p, dpi=dpi)
        plt.close(fig)
        self.produced.append(name)
        return p

    def add_figure(self, fig, name):
        """把已画好的 Figure 存进实例目录 —— 自定义图件用 (标准产出之外)。"""
        return self._save(fig, name)

    def _freq_mhz(self, x, fs):
        return fs if fs else self.fs

    # ------------------------------------------------------------------
    # 基带码 / 符号
    # ------------------------------------------------------------------
    def bits(self, bits, limit=256, per_line=64, label="基带码"):
        """基带码片段 → ``bits.txt`` (过长自动截断并标注总长)。"""
        b = np.asarray(bits).reshape(-1).astype(int)
        lines = [f"# {label} —— 共 {b.size} bit, 下列显示前 {min(limit, b.size)} bit",
                 f"# 每行 {per_line} bit", ""]
        for i in range(0, min(limit, b.size), per_line):
            lines.append("".join(str(v) for v in b[i:i + per_line]))
        if b.size > limit:
            lines += ["...", f"# (其余 {b.size - limit} bit 省略)"]
        (self.dir / "bits.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.produced.append("bits.txt")
        return self.dir / "bits.txt"

    def symbols(self, symbols, limit=256, label="符号"):
        """符号序列 → ``symbols.csv`` (index, re, im; 列名用 ASCII 以兼容各类工具)。"""
        s = np.asarray(symbols).reshape(-1)
        n = min(limit, s.size)
        head = "index,re,im\n"
        rows = "".join(f"{i},{s[i].real:.6g},{s[i].imag:.6g}\n" for i in range(n))
        tail = (f"# {label}: 共 {s.size} 个, 列出前 {n} 个\n" if s.size > limit
                else f"# {label}: 共 {s.size} 个\n")
        (self.dir / "symbols.csv").write_text(tail + head + rows, encoding="utf-8")
        self.produced.append("symbols.csv")
        return self.dir / "symbols.csv"

    # ------------------------------------------------------------------
    # 波形 / 频谱 / 能量
    # ------------------------------------------------------------------
    def waveform(self, signals, n_show=400, symbol_period=None, title=None):
        """多阶段时域波形: 每个信号一个子图 (复数画 I/Q 两路)。

        symbol_period 不为 None 时按该周期画符号边界竖线 —— 便于把波形与符号
        序列对应起来 (看每个符号在波形上占哪一段)。
        """
        items = list(signals.items()) if isinstance(signals, dict) else list(signals)
        fig, axs = plt.subplots(len(items), 1, figsize=(11, 1.9 * len(items)),
                                squeeze=False)
        for ax, (name, sig) in zip(axs[:, 0], items):
            visualize.plot_waveform(np.asarray(sig).ravel()[:n_show], ax=ax, title=name)
            if symbol_period:
                for k in range(0, n_show, symbol_period):
                    ax.axvline(k, color="k", lw=0.4, alpha=0.22)
            ax.set_xlabel("sample" + (f"  (竖线 = 符号边界, 每 {symbol_period} 点)"
                                      if symbol_period else ""))
        fig.suptitle(title or f"{self.title} —— 时域波形 (前 {n_show} 采样)", fontsize=12)
        return self._save(fig, "waveform.png")

    def spectrum(self, x, fs=None, n_fft=None, envelope=True, title=None):
        """幅度谱 (复数经 fftshift, 横轴 MHz)。

        envelope=True 时叠加**频谱包络** (幅度谱的滑动平均) 并报告:
          · 包络均值 = 对频率取平均后的平均幅度密度 (把谱形状抹平成单一数字,
            适合快速比较不同实验的整体电平; 看滚降/旁瓣形状仍要看谱本身);
          · 99% 占用带宽 = 包含 99% 总功率的最小频宽 (工程惯例) —— 它是
            「带内/带外」的客观判据, 比人为指定带宽可靠。
        """
        fs = self._freq_mhz(x, fs)
        v = np.asarray(x).ravel()
        freqs, mag = measure.spectrum(v, fs, n_fft)
        f_s, m_s = np.fft.fftshift(freqs), np.fft.fftshift(mag)
        fig, ax = plt.subplots(figsize=(11, 3.9))
        visualize.plot_spectrum(f_s / 1e6, m_s, ax=ax, lw=0.7, label="|X|")
        if envelope:
            env, env_mean = measure.spectrum_envelope(f_s, m_s)
            ax.plot(f_s / 1e6, env, lw=1.6, color="tab:orange",
                    label=f"包络 (均值 {env_mean:.3g})")
            bw, flo, fhi = measure.occupied_bandwidth(v, fs)
            ax.axvspan(flo / 1e6, fhi / 1e6, color="tab:green", alpha=0.12,
                       label=f"99% 占用带宽 {bw/1e6:.3f} MHz")
            ax.legend(fontsize=8)
            self.table("频谱包络", [
                f"包络均值 (平均幅度密度) = {env_mean:.4g}",
                f"99% 占用带宽 = {bw/1e6:.4f} MHz  [{flo/1e6:.3f}, {fhi/1e6:.3f}] MHz",
                "(占用带宽按**当前信号**的含噪功率算 —— 噪声是宽带的, 会把带宽撑大;"
                " 要反映调制/成形本身, 请对**发送信号**测)",
            ])
        ax.set_xlabel("frequency (MHz)")
        ax.set_ylabel("|X|")
        ax.set_title(title or f"{self.title} —— 幅度谱")
        return self._save(fig, "spectrum.png")

    def energy(self, x, fs=None, n_fft=None, title=None):
        """能量谱 / PSD (dB/Hz, fftshift 后)。"""
        fs = self._freq_mhz(x, fs)
        v = np.asarray(x).ravel()
        n_fft = n_fft or len(v)
        psd = np.abs(np.fft.fft(v, n_fft)) ** 2 / (len(v) * fs)
        f = np.fft.fftfreq(n_fft, 1 / fs)
        fig, ax = plt.subplots(figsize=(11, 3.6))
        ax.plot(np.fft.fftshift(f) / 1e6, np.fft.fftshift(10 * np.log10(psd + 1e-30)),
                lw=0.8, color="tab:purple")
        ax.set_xlabel("frequency (MHz)")
        ax.set_ylabel("PSD (dB/Hz)")
        ax.grid(alpha=0.3)
        ax.set_title(title or f"{self.title} —— 能量谱 (PSD)")
        return self._save(fig, "energy.png")

    # ------------------------------------------------------------------
    # 滤波器 / 噪声
    # ------------------------------------------------------------------
    def filters(self, pulses, fs=None, n_fft=4096, title=None):
        """滤波器频率响应: 每个脉冲给出幅频 (dB) 与群延迟。

        pulses: {标签: 脉冲系数} —— 成形脉冲与其匹配滤波器 (同系数, 对称) 都可列。
        """
        fs = self._freq_mhz(None, fs)
        items = list(pulses.items()) if isinstance(pulses, dict) else list(pulses)
        fig, axs = plt.subplots(2, len(items), figsize=(5.2 * len(items), 6),
                                squeeze=False)
        for j, (name, h) in enumerate(items):
            h = np.asarray(h).ravel()
            H = np.fft.fftshift(np.fft.fft(h, n_fft))
            f = np.fft.fftshift(np.fft.fftfreq(n_fft)) * fs / 1e6      # MHz
            axs[0, j].plot(f, 20 * np.log10(np.abs(H) / np.max(np.abs(H)) + 1e-12),
                           lw=0.9)
            axs[0, j].set_title(f"{name} —— 幅频", fontsize=10)
            axs[0, j].set_ylabel("|H| (dB)")
            axs[0, j].grid(alpha=0.3)
            axs[1, j].stem(np.arange(len(h)), h, basefmt=" ")
            axs[1, j].set_title("脉冲系数", fontsize=9)
            axs[1, j].set_xlabel("tap")
            axs[1, j].grid(alpha=0.3)
        fig.suptitle(title or f"{self.title} —— 滤波器响应", fontsize=12)
        return self._save(fig, "filters.png")

    def noise(self, clean, full, band, fs=None, n_show=400, title=None):
        """加噪对照: 干净 / 全带白噪声 / 带限(带内)噪声 —— 时域 + 频谱双排。"""
        fs = self._freq_mhz(clean, fs)
        cases = [("干净", clean), ("+全带白噪声", full), ("+带限(带内)噪声", band)]
        fig, axs = plt.subplots(2, 3, figsize=(16, 6.2))
        for j, (name, sig) in enumerate(cases):
            sig = np.asarray(sig).ravel()
            visualize.plot_waveform(sig[:n_show], ax=axs[0, j], title=name, lw=0.8)
            freqs, mag = measure.spectrum(sig, fs)
            visualize.plot_spectrum(np.fft.fftshift(freqs) / 1e6,
                                    np.fft.fftshift(mag), ax=axs[1, j], lw=0.6)
            axs[1, j].set_xlabel("frequency (MHz)")
        axs[0, 0].set_xlabel("sample")
        axs[1, 0].set_ylabel("|X|")
        fig.suptitle(title or f"{self.title} —— 加噪对照 (上: 时域, 下: 频谱)", fontsize=12)
        return self._save(fig, "noise.png")

    # ------------------------------------------------------------------
    # 星座 / 眼图
    # ------------------------------------------------------------------
    def constellation(self, soft, ref=None, title=None, s=18):
        """星座图; ref 不为 None 时叠加参考点。"""
        soft = np.asarray(soft).ravel()
        fig, ax = plt.subplots(figsize=(5.6, 5.6))
        visualize.plot_constellation(soft, ax=ax, s=s, alpha=0.7)
        if ref is not None:
            ax.scatter(np.asarray(ref).real, np.asarray(ref).imag,
                       marker="x", s=70, c="tab:red", lw=1.4, label="理想点")
            ax.legend(fontsize=8)
        lim = max(float(np.abs(soft).max()), 1e-9) * 1.2
        ax.set_xlim(-lim, lim); ax.set_ylim(-lim, lim)
        ax.set_xlabel("I"); ax.set_ylabel("Q")
        ax.set_title(title or f"{self.title} —— 星座图")
        return self._save(fig, "constellation.png")

    def eye(self, x, period, n_traces=40, first_peak=None, ideal=None,
            component="I", title=None):
        """眼图 (逐分量) + 量化 —— 按行业惯例给出**眼高**与**判决点 SNR**。

        component: ``"I"`` 实部 | ``"Q"`` 虚部 | ``"envelope"`` 包络 √(I²+Q²)

        为何必须逐分量: 复数波形无法折叠成一张二维图 —— 眼图折叠的前提是拉出一个
        **实的**一维序列。I/Q 也不能相加 (那是两个正交分量、承载不同信息, 加起来
        信息就毁了); 正确的“合成”是复表示 ``I+jQ``, 而星座图本来就是这么画的。
        包络眼图只对**非恒模**调制 (ASK / O-QPSK) 有信息, 恒模 PSK 下它是一条直线。

        ideal: 各符号的理想采样值 (仅 component="I"/"Q" 时用于量化)。
        """
        x = np.asarray(x).ravel()
        if component == "I":
            v, ylab = x.real, "I amplitude"
        elif component == "Q":
            v, ylab = x.imag, "Q amplitude"
        elif component == "envelope":
            v, ylab, ideal = np.abs(x), "|x| envelope", None
        else:
            raise ValueError("component 应为 I / Q / envelope")
        first_peak = first_peak if first_peak is not None else period // 2
        start = first_peak - period // 2
        # start 可能为负 (峰值靠近波形起点, 如短脉冲的正弦成形) —— 跳过越界窗口
        segs = [v[s:s + period] for s in (start + k * period for k in range(n_traces))
                if s >= 0 and s + period <= len(v)]
        eye = np.array(segs)
        fig, ax = plt.subplots(figsize=(7, 4.4))
        visualize.plot_eye(eye, ax=ax, lw=0.6, alpha=0.45)
        ax.axvline(period // 2, color="r", ls="--", lw=1.0, alpha=0.8)
        ax.set_xlabel("符号周期内采样 (红虚线 = 最佳采样点)")
        ax.set_ylabel(ylab)
        ax.grid(alpha=0.3)
        ax.set_title(title or f"{self.title} —— 眼图 ({component})")
        name = "eye.png" if component == "I" else f"eye_{component.lower()}.png"
        path = self._save(fig, name)

        if ideal is not None:
            ref = np.asarray(ideal).ravel()[:len(eye)]
            samp = eye[:, period // 2]
            lv = np.unique(np.round(ref, 6))
            gaps = np.diff(np.unique(np.r_[lv, -lv]))
            err = samp - ref
            self.eye_height = float(gaps.min()) if gaps.size else float("nan")
            self.eye_snr_db = float(10 * np.log10(np.mean(ref ** 2) /
                                                   max(np.mean(err ** 2), 1e-30)))
            self.table("眼图量化", [
                f"眼高 (最内层眼, 最小电平间距) = {self.eye_height:.3f}",
                f"判决点 SNR = {self.eye_snr_db:.1f} dB",
                f"(眼宽/定时余量需扫 jitter 才能给出, 非单张眼图所能)",
            ])
        return path

    def code_autocorr(self, code, title=None, two_sided=True):
        """扩频码自相关 —— 验证 PN 序列的尖锐主峰与低旁瓣 (扩频同步的基础)。

        code: ±1 码序列 (单周期或多周期)。主峰应 = 序列长度, 旁瓣应 ≪ 主峰。
        """
        c = np.asarray(code).ravel()
        r = np.abs(measure.xcorr(c, c))
        n = len(c)
        lag = np.arange(len(r)) - (n - 1)
        fig, ax = plt.subplots(figsize=(10, 3.8))
        ax.stem(lag, r, basefmt=" ", markerfmt=" ", linefmt="C0-")
        ax.set_xlabel("lag (码片)")
        ax.set_ylabel("|R(k)|")
        ax.grid(alpha=0.3)
        peak = float(r.max())
        side = float(np.max(np.delete(r, int(np.argmax(r)))))
        ax.set_title(title or f"{self.title} —— 扩频码自相关 (主峰 {peak:.0f}, "
                              f"最大旁瓣 {side:.0f}, 旁瓣/主峰 {side/peak:.1%})")
        path = self._save(fig, "autocorr.png")
        self.table("扩频码自相关", [
            f"主峰 = {peak:.0f} (= 序列长度 {n})",
            f"最大旁瓣 = {side:.0f}  (旁瓣/主峰 = {side/peak:.1%})",
        ])
        return path

    # ------------------------------------------------------------------
    # 分析与汇总
    # ------------------------------------------------------------------
    def note(self, text):
        """追加一条分析结论 (进 report.md)。"""
        self.notes.append(text)

    def table(self, title, rows):
        """追加一张表 (进 report.md; rows 为可迭代的行序列)。"""
        self.tables.append((title, list(rows)))

    def close(self, report="report.md"):
        """汇总 ``report.md``: 实例信息 + 关键数值表 + 分析结论 + 产出清单。"""
        L = [f"# 实验实例: {self.title}", ""]
        if self.fs:
            L += [f"- 采样率: {self.fs/1e6:g} MHz", ""]
        for title, rows in self.tables:
            L += [f"## {title}", ""]
            for r in rows:
                L.append("- " + (r if isinstance(r, str) else " | ".join(map(str, r))))
            L.append("")
        if self.notes:
            L += ["## 分析结论", ""] + [f"- {n}" for n in self.notes] + [""]
        L += ["## 产出", ""] + [f"- `{p}`" for p in self.produced] + [""]
        p = self.dir / report
        p.write_text("\n".join(L), encoding="utf-8")
        self.produced.append(report)
        return p
