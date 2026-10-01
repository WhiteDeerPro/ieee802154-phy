// mc_top_tb.sv —— 蒙特卡洛全链 (rx_top 集成版)
// ---------------------------------------------------------------------------
// DUT = rtl/rx/rx_top.sv: ADC(12bit) → MF → cfo_rot → sync → despread → deframer
// 与 mc_cfo_tb 的差异: **无上帝视角** —— CFO 估计为 rx_top 内部 free-running,
// 不需要 frames.txt 触发; +FRAMES/+NFRAMES 为接口兼容保留但未使用。
//
// plusargs: +MEM= +NSMP= +OUT= +CKS= [+FRAMES= +NFRAMES=] [+NOCFO=1 旁路消旋]
// 事件 dump: SYM/FST/BYT/FRM/DET (同 mc_tb 口径) + EST <k> <phase_inc> (诊断)
`timescale 1ns/1ps
module mc_top_tb #(
    parameter integer MAX_SMP = 1 << 25,
    parameter integer MAX_FRAMES = 4096,
    // CFO 估计配置 (仅影响**自主触发**路径 —— 外部触发走 EST_CHIP_OFF/SKIP_T3)
    // 默认值 = 自主触发的正确值 (detect 在帧起点后 538 = 8*67+3)。
    parameter integer CHIP_OFF_P = 3,
    parameter integer NSMP_P     = 2048,
    parameter integer SKIP_T3_P  = 0
) ();
    // —— 时钟 (16 MHz) ——
    reg clk = 0;
    initial forever #31.25 clk = ~clk;

    // —— plusargs ——
    string       mem_path = "mem.bin", out_path = "events.txt", fr_path = "frames.txt";
    int unsigned nsmp = 0, nframes = 0;
    reg  [47:0]  ph_th  = 48'd200_000_000_000;
    reg  [47:0]  sfd_th = 48'd30_000_000_000_000;
    longint unsigned ph_arg = 0, sfd_arg = 0;
    reg  [31:0]  cks_arg = 32'h0;
    int          cks_seen = 0;
    reg          cfo_en = 1'b1;
    int          nocfo_seen = 0;
    reg          trig_ext_r = 1'b1;      // 默认自主 (detect 触发); +TRIGEXT=1 切外部 (需配 EST_CHIP_OFF=0)
    int          trigext_seen = 0;
    int          fdelay = 0;            // +FDELAY=<d>: 触发点相对帧起点的额外偏移 (扫描容限)
    int          fixinc_v = 0;          // +FIXINC=<inc>: 强制 inc_reg (诊断: 旁路估计)
    int          fixinc_seen = 0;
    int unsigned dump_start = 0, dump_len = 0;   // +DUMPSTART/+DUMPLEN: dump 检测器内部量

    // —— 激励 ——
    reg [31:0] smp_mem [0:MAX_SMP-1];
    reg [31:0] fr_mem  [0:MAX_FRAMES-1];      // 帧起点 (触发用)
    int fd_mem = 0, fd_out = 0;
    int got_bytes = 0;

    reg               rst_n = 0;
    reg signed [11:0] i_in = 0, q_in = 0;
    reg               dv_in = 0;

    // —— CFO 触发 (验证期由帧起点信息驱动; 真实系统需前导能量检测, 见 docs/08 I-6) ——
    int unsigned k = 0;
    reg          est_start_r = 0, rot_load_r = 0;
    int unsigned f_idx = 0;
    wire at_frame_start = (f_idx < nframes) && (k == fr_mem[f_idx] - 32'd1 + fdelay[15:0]);

    always @(posedge clk) begin
        if (!rst_n) begin
            est_start_r <= 1'b0;
            rot_load_r  <= 1'b0;
            f_idx       <= 0;
        end else begin
            est_start_r <= at_frame_start;
            rot_load_r  <= at_frame_start;
            if (at_frame_start && dv_in) f_idx <= f_idx + 1;
        end
    end

    // —— DUT: rx_top ——
    wire        detect, frame_start, busy;
    wire [3:0]  locked_phase;
    wire [7:0]  data_out, psdu_len;
    wire        data_valid, fcs_ok, frame_done;

    // 外部参数通道（决策层解耦）: 默认关闭走内部估计; +EXTINC=<值> 可强开
    integer           ext_inc_tmp;
    reg               EXT_INC_EN = 1'b0;
    reg signed [23:0] EXT_INC_V  = 24'sd0;
    initial begin
        if ($value$plusargs("EXTINC=%d", ext_inc_tmp)) begin
            EXT_INC_EN = 1'b1;
            EXT_INC_V  = ext_inc_tmp[23:0];
        end
    end

    // ---- 外部定时通道（"全估计外置"）: +EXTLCK=<相位 0..15> 启用 ----
    integer        ext_lck_tmp;
    reg            EXT_LCK_EN = 1'b0;
    reg [3:0]      EXT_LCK_PH = 4'd0;
    initial begin
        if ($value$plusargs("EXTLCK=%d", ext_lck_tmp)) begin
            EXT_LCK_EN = 1'b1;
            EXT_LCK_PH = ext_lck_tmp[3:0];
        end
    end

    // ---- DPI 观测器（"固件在环"最小演示）: +DPI=1 启用 ----
    // C 侧 observer_dpi.c 与 model/upper/observer.py 同结构；本 tb 在每次
    // est_done 把 (估计值, 质量分子/分母) 喂给它，LOCK 后经外部参数通道接管消旋。
    import "DPI-C" function int observer_dpi_step(input int inc_fx, input int num, input int den);
    integer dpi_en    = 0;
    integer dpi_ret   = 0;
    integer dpi_state = 0;
    initial if ($value$plusargs("DPI=%d", dpi_en)) ;

    rx_top #(.W(21), .EST_CHIP_OFF_AUTO(CHIP_OFF_P[7:0]), .EST_NSMP(NSMP_P),
             .EST_SKIP_T3_AUTO(SKIP_T3_P != 0)) u_top (
        .clk(clk), .rst_n(rst_n),
        .adc_i(i_in), .adc_q(q_in), .adc_dv(dv_in),
        .cfo_en(cfo_en), .est_start(est_start_r), .rot_load(rot_load_r),
        .trig_ext(trig_ext_r),
        .ext_inc_en(EXT_INC_EN), .ext_inc(EXT_INC_V), .ext_phase_off(24'd0),
        .ext_lock_en(EXT_LCK_EN), .ext_lock_phase(EXT_LCK_PH),
        .ph_thresh(ph_th), .sfd_thresh(sfd_th),
        .detect(detect), .locked_phase(locked_phase),
        .frame_start(frame_start), .busy(busy),
        .data_out(data_out), .data_valid(data_valid),
        .psdu_len(psdu_len), .fcs_ok(fcs_ok), .frame_done(frame_done)
    );

    // —— 诊断: 强制 inc_reg (检验"采纳了错误估计"的假设) ——
    always @(posedge clk) if (fixinc_seen) u_top.inc_reg <= fixinc_v[23:0];

    // —— 事件 dump ——
    reg det_d = 0;
    always @(posedge clk) begin
        if (fd_out != 0 && dv_in) begin
            if (u_top.sym_dv)     $fwrite(fd_out, "SYM %0d %0d\n", k, u_top.sym);
            if (frame_start)      $fwrite(fd_out, "FST %0d\n", k);
            if (data_valid)       $fwrite(fd_out, "BYT %0d %0d\n", k, data_out);
            if (frame_done)       $fwrite(fd_out, "FRM %0d %0d %0d\n", k, psdu_len, fcs_ok);
            if (detect && !det_d) $fwrite(fd_out, "DET %0d %0d\n", k, locked_phase);
            if (u_top.est_done) begin
                $fwrite(fd_out, "EST %0d %0d %0d %0d %0d %0d\n", k, u_top.phase_inc,
                        u_top.est_ok, u_top.u_est.pbest,
                        u_top.u_est.aa_i + u_top.u_est.aa_q,
                        u_top.u_est.am[u_top.u_est.pbest]);
                if (dpi_en) begin
                    dpi_ret   = observer_dpi_step(u_top.phase_inc,
                                                  u_top.u_est.aa_i + u_top.u_est.aa_q,
                                                  u_top.u_est.am[u_top.u_est.pbest]);
                    dpi_state = (dpi_ret >> 24) & 8'hFF;
                    if (dpi_state == 2) begin        // LOCK: 接管消旋参数
                        EXT_INC_V  = dpi_ret[23:0];
                        EXT_INC_EN = 1'b1;
                    end
                    $fwrite(fd_out, "DPI %0d %0d %0d\n", k, dpi_state, dpi_ret[23:0]);
                end
            end
            if (u_top.pd_det && u_top.u_pdet.sample_en)
                $fwrite(fd_out, "PDT %0d\n", k);   // 仅在采样拍记录 (det_pulse 保持到下次采样)
            if (dump_len > 0 && k >= dump_start && k < dump_start + dump_len && u_top.u_est.k0)
                $fwrite(fd_out, "ESD %0d %0d %0d %0d %0d %0d %0d\n", k,
                        u_top.u_est.t, u_top.u_est.ce, u_top.u_est.co,
                        u_top.u_est.mc_e[3], u_top.u_est.mc_o[7], u_top.u_est.me_m);
        end
        det_d <= detect;
    end

    // —— 主流程 ——
    localparam integer DRAIN = 600;

    initial begin
        if (!$value$plusargs("MEM=%s", mem_path)) begin
            $display("[mc_top_tb] FATAL missing +MEM"); $finish;
        end
        if (!$value$plusargs("NSMP=%d", nsmp) || nsmp == 0) begin
            $display("[mc_top_tb] FATAL missing/invalid +NSMP"); $finish;
        end
        if (!$value$plusargs("OUT=%s", out_path)) begin
            $display("[mc_top_tb] FATAL missing +OUT"); $finish;
        end
        if ($value$plusargs("FRAMES=%s", fr_path)) $readmemh(fr_path, fr_mem);
        void'($value$plusargs("NFRAMES=%d", nframes));
        if ($value$plusargs("PH=%d", ph_arg))   ph_th  = ph_arg[47:0];
        if ($value$plusargs("SFD=%d", sfd_arg)) sfd_th = sfd_arg[47:0];
        cks_seen = $value$plusargs("CKS=%h", cks_arg);
        nocfo_seen = $value$plusargs("NOCFO=%d", cfo_en);
        trigext_seen = $value$plusargs("TRIGEXT=%d", trig_ext_r);
        void'($value$plusargs("FDELAY=%d", fdelay));
        fixinc_seen = $value$plusargs("FIXINC=%d", fixinc_v);
        void'($value$plusargs("DUMPSTART=%d", dump_start));
        void'($value$plusargs("DUMPLEN=%d", dump_len));
        if (nsmp >= MAX_SMP) begin
            $display("[mc_top_tb] FATAL NSMP exceeds MAX_SMP"); $finish;
        end

        fd_mem = $fopen(mem_path, "rb");
        if (fd_mem == 0) begin $display("[mc_top_tb] FATAL cannot open %s", mem_path); $finish; end
        got_bytes = $fread(smp_mem, fd_mem);
        $fclose(fd_mem);
        if (got_bytes != nsmp * 4) begin
            $display("[mc_top_tb] FATAL mem size %0d != NSMP*4", got_bytes); $finish;
        end

        if (cks_seen) begin
            reg [31:0] cks = 32'h0;
            int unsigned ci;
            for (ci = 0; ci < 1024 && ci < nsmp; ci = ci + 1) cks ^= smp_mem[ci];
            for (ci = (nsmp > 1024 ? nsmp - 1024 : 0); ci < nsmp; ci = ci + 1)
                cks ^= smp_mem[ci];
            if (cks !== cks_arg) begin
                $display("[mc_top_tb] FATAL mem checksum %h != %h", cks, cks_arg);
                $finish;
            end
        end

        rst_n = 0;
        repeat (16) @(posedge clk);
        rst_n = 1;
        @(negedge clk);

        fd_out = $fopen(out_path, "w");
        if (fd_out == 0) begin $display("[mc_top_tb] FATAL cannot open out"); $finish; end

        for (k = 0; k < nsmp; k = k + 1) begin
            i_in = smp_mem[k][11:0];
            q_in = smp_mem[k][23:12];
            dv_in = 1;
            @(negedge clk);
        end
        dv_in = 0;
        i_in = 0;
        q_in = 0;

        repeat (DRAIN) @(posedge clk);
        $fclose(fd_out);
        $display("[mc_top_tb] done: %0d samples", nsmp);
        $finish;
    end
endmodule
