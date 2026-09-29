# -*- coding: utf-8 -*-
"""
visualize —— 可视化层: 消费 measure 结果画图 (matplotlib 薄封装)

与 measure 分离: 本层只做「把已测得的参数画出来」, 不做任何信号处理。
中文字体由调用方配置 (见 run_visual / 各实验脚本)。
"""

import numpy as np
import matplotlib.pyplot as plt


def plot_constellation(soft, ax=None, title=None, s=16, alpha=0.75, **kw):
    """星座散点图。颜色可用 c= 或 color= 传入 (不传默认 tab:blue)。"""
    ax = ax or plt.gca()
    v = np.asarray(soft)
    if "c" not in kw and "color" not in kw:
        kw["color"] = "tab:blue"
    ax.scatter(v.real, v.imag, s=s, alpha=alpha, **kw)
    ax.axhline(0, color="gray", lw=0.4)
    ax.axvline(0, color="gray", lw=0.4)
    ax.set_aspect("equal")
    ax.grid(alpha=0.3)
    if title:
        ax.set_title(title)
    return ax


def plot_spectrum(freqs, mag, ax=None, title=None, **kw):
    """幅度谱折线图。"""
    ax = ax or plt.gca()
    ax.plot(freqs, mag, **kw)
    ax.set_xlabel("frequency")
    ax.set_ylabel("amplitude")
    if title:
        ax.set_title(title)
    return ax


def plot_eye(eye, ax=None, title=None, **kw):
    """眼图: 输入 (n_traces, sps) 折叠矩阵, 每列画一条迹线。"""
    ax = ax or plt.gca()
    e = np.asarray(eye)
    ax.plot(np.arange(e.shape[1]), e.T, **kw)
    if title:
        ax.set_title(title)
    return ax


def plot_waveform(x, ax=None, title=None, real_only=False, **kw):
    """波形图: 复数默认画 I/Q 两路, real_only 只画实部。"""
    ax = ax or plt.gca()
    x = np.asarray(x)
    if np.iscomplexobj(x) and not real_only:
        ax.plot(x.real, label="I", **kw)
        ax.plot(x.imag, label="Q", **kw)
        ax.legend(fontsize=8)
    else:
        ax.plot(x.real if np.iscomplexobj(x) else x, **kw)
    if title:
        ax.set_title(title)
    return ax


def plot_ber_curve(snr_list, ber_list, ax=None, title=None, label=None,
                   no_error_marker=True, floor=None, color=None, **kw):
    """BER vs SNR 曲线 (对数纵轴)。

    零误码点不画成固定的下限线 —— 那会被误读成 error floor (链路压不下去的
    征兆), 而真相是「统计分辨率不够, 没测到误码」。默认把零误码点画成
    向下三角 + 虚线, 表示「真实 BER 低于此点」的上界语义。

    no_error_marker=False 回退到把零误码点固定画在 floor (默认 1e-9) 的旧行为。
    """
    ax = ax or plt.gca()
    x = np.asarray(snr_list, dtype=float)
    y = np.asarray(ber_list, dtype=float)
    zero = y <= 0
    if floor is None:
        pos = y[y > 0]
        floor = float(pos.min() * 0.3) if pos.size else 1e-9
    if zero.any() and not no_error_marker:
        y = np.where(zero, floor, y)
        zero = np.zeros_like(zero)
    elif zero.any():
        ax.semilogy(x[zero], np.full(zero.sum(), floor), "v", ms=7, mfc="none",
                    color=color, label="no errors detected (< this level)")
    ax.semilogy(x, np.where(zero, floor, y), "-" if zero.any() else "-o",
                color=color, label=label, **kw)
    ax.set_xlabel("SNR (dB)")
    ax.set_ylabel("BER")
    ax.set_ylim(bottom=floor * 0.3)
    ax.grid(True, which="both", alpha=0.3)
    if title:
        ax.set_title(title)
    return ax


# ---------------------------------------------------------------------------
# 中文字体 (幂等配置; 未配置时中文会显示为方框)
# ---------------------------------------------------------------------------

_CJK_CANDIDATES = ("Noto Sans CJK SC", "Source Han Sans SC", "WenQuanYi Zen Hei",
                   "Microsoft YaHei", "SimHei", "Droid Sans Fallback")
_CJK_CACHE = [None]


def use_cjk_font(verbose=False):
    """为 matplotlib 配置中文字体, 幂等。返回选中的字体名 (找不到则 None)。"""
    if _CJK_CACHE[0] is not None:
        return _CJK_CACHE[0]
    from matplotlib import font_manager, rcParams
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in _CJK_CANDIDATES:
        if name in available:
            rcParams["font.sans-serif"] = [name] + [f for f in rcParams["font.sans-serif"]
                                                   if f != name]
            rcParams["axes.unicode_minus"] = False
            _CJK_CACHE[0] = name
            return name
    if verbose:
        print("warning: 未找到中文字体, 图中中文将显示为方框")
    return None


# ---------------------------------------------------------------------------
# 链路观测点的常用画法 (配合 baseband.link.Signal)
# ---------------------------------------------------------------------------

def plot_constellation_grid(cases, ncols=3, subplot_size=(3.4, 3.4), suptitle=None,
                            s=14, alpha=0.6):
    """多情形星座图网格, 用于一页对比「理想 / 各损伤 / 校正后」。

    cases: [(标题, 星座软值), ...] —— 软值取 ``Signal.rx_soft``。
    返回 (fig, axes); 每个子图等比例且带零轴, 便于看旋转/平移/收缩。
    """
    n = len(cases)
    ncols = min(ncols, n)
    nrows = (n + ncols - 1) // ncols
    fig, axes = plt.subplots(nrows, ncols, figsize=(subplot_size[0] * ncols,
                                                    subplot_size[1] * nrows),
                             squeeze=False)
    for ax in axes.ravel()[n:]:
        ax.axis("off")
    lim = 0.0
    for _, soft in cases:
        v = np.asarray(soft)
        if v.size:
            lim = max(lim, float(np.abs(v).max()))
    lim = lim * 1.15 or 1.0
    for ax, (name, soft) in zip(axes.ravel(), cases):
        plot_constellation(soft, ax=ax, title=name, s=s, alpha=alpha)
        ax.set_xlim(-lim, lim)
        ax.set_ylim(-lim, lim)
    if suptitle:
        fig.suptitle(suptitle)
    fig.tight_layout()
    return fig, axes


def plot_corr(corr, ax=None, title=None, peak=None, label="correlation", **kw):
    """相关器输出 (同步扫描曲线): 横轴对齐点, 纵轴相关幅值。

    peak 不为 None 时标出选中的对齐点。
    """
    ax = ax or plt.gca()
    c = np.asarray(corr)
    ax.plot(np.arange(len(c)), c, label=label, **kw)
    if peak is not None:
        ax.axvline(peak, color="tab:red", ls="--", lw=1.0)
        ax.annotate(f"sync = {int(peak)}", xy=(peak, c[peak] if peak < len(c) else 0),
                    xytext=(8, -12), textcoords="offset points", fontsize=8,
                    color="tab:red")
    ax.set_xlabel("alignment index")
    ax.set_ylabel("|correlation|")
    ax.grid(alpha=0.3)
    if title:
        ax.set_title(title)
    return ax
