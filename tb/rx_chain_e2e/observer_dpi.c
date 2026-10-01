/* observer_dpi.c —— 链路观测器的 DPI-C 实现（"固件在环"最小演示）
 *
 * 与 model/upper/observer.py 同结构：质量加权证据累积（泄漏积分）+
 * 捕获状态机（SEARCH -> VERIFY -> LOCK）。由 tb 在每次 est_done 调用，
 * 返回建议的 phase_inc，经外部参数通道（rx_top.ext_inc）作用于消旋器。
 *
 * 与 Python ref 的差异（刻意简化，作为最小演示）：
 *   - 单候选（不做多状态聚类）—— 场景是单设备；
 *   - 无 EMA 跟踪（LOCK 后直接替换）—— 演示性质。
 *
 * 返回值编码：低 24 位 = 建议 phase_inc（定点，24bit 补码截断）；
 *             高 8 位  = 状态（0=SEARCH, 1=VERIFY, 2=LOCK）。
 */

static double g_ev     = 0.0;   /* 累积证据（等效满分帧数） */
static int    g_state  = 0;     /* 0=SEARCH 1=VERIFY 2=LOCK */
static int    g_streak = 0;     /* 连续命中帧数 */
static int    g_inc    = 0;     /* LOCK 后的建议 phase_inc */

int observer_dpi_reset(void)
{
    g_ev = 0.0;
    g_state = 0;
    g_streak = 0;
    g_inc = 0;
    return 0;
}

/* 一步观测：inc_fx = 本帧估计的 phase_inc；num/den = 质量 cf 的分子/分母。 */
int observer_dpi_step(int inc_fx, int num, int den)
{
    const double q_floor = 0.25;
    const double acq_th  = 1.5;
    const double decay   = 0.92;

    double q = (den > 0) ? (double)num / (double)den : 0.0;
    double w = (q > q_floor) ? (q - q_floor) / (1.0 - q_floor) : 0.0;

    g_ev *= decay;                       /* 近期优先（泄漏积分） */
    if (w > 0.0) {
        g_ev += w;
        g_streak++;
        if (g_state == 0 && g_ev >= acq_th) {
            g_state = 1;                 /* SEARCH -> VERIFY */
        } else if (g_state == 1 && g_streak >= 2) {
            g_state = 2;                 /* VERIFY -> LOCK */
        }
        if (g_state == 2) {
            g_inc = inc_fx;              /* LOCK 后跟随本帧估计 */
        }
    } else {
        g_streak = 0;                    /* 连续命中链断 */
    }
    return (g_inc & 0x00FFFFFF) | (g_state << 24);
}
