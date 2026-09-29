// mc_cfo_tb.sv —— 蒙特卡洛全链 (CFO 版): MF → cfo_rot → sync → despread → deframer
// ---------------------------------------------------------------------------
// 与 mc_tb.sv 的差异: 主链插入 CFO 消旋 (cfo_rot), cfo_est 挂在 MF 输出上做
// 前导联合估计 (对齐点 + 频偏)。
//
// 触发 ("上帝视角", 仅用于验证消旋架构本身能不能修 CFO 检测):
//   +FRAMES=<frames.txt>  每行一个帧起点采样索引 (hex), 由 mc_gen.py 导出
//   +NFRAMES=<n>          帧数
//   在 k == frame_start-1 拍: est_start (采集下个 2048 = 前导) + rot_load (相位归零)
//   est_done 时: phase_inc_reg <= phase_inc (供后续帧消旋; 首帧无估计 → 未消旋)
//
// 触发机制 (能量检测/自举) 是后续工程问题; 本 TB 先钉死"消旋后检测能否恢复"。
//
// plusargs: +MEM= +NSMP= +OUT= +CKS= +FRAMES= +NFRAMES=
// 事件 dump: SYM/FST/BYT/FRM/DET (同 mc_tb) + EST <k> <phase_inc> <p_hat>
`timescale 1ns/1ps
module mc_cfo_tb #(
    parameter integer MAX_SMP = 1 << 25,
    parameter integer MAX_FRAMES = 4096
) ();
    // —— 时钟 (16 MHz, 自生成) ——
    reg clk = 0;
    initial forever #31.25 clk = ~clk;

    // —— plusargs ——
    string       mem_path = "mem.bin", out_path = "events.txt", fr_path = "frames.txt";
    int unsigned nsmp = 0, nframes = 0;
    reg  [47:0]  ph_th = 48'd200_000_000_000;
    reg  [47:0]  sfd_th = 48'd30_000_000_000_000;
    longint unsigned ph_arg = 0, sfd_arg = 0;
    reg  [31:0]  cks_arg = 32'h0;
    int          cks_seen = 0;
    reg  signed [23:0] fix_inc = 24'sd0;   // +FIXINC=<v>: 跳过估计直接用给定值 (二分诊断)
    int          fix_seen = 0;
    int unsigned dump_start = 0, dump_len = 0;   // +DUMPSTART/+DUMPLEN: dump 消旋后采样

    // —— 激励数组 ——
    reg [31:0] smp_mem [0:MAX_SMP-1];
    reg [31:0] fr_mem  [0:MAX_FRAMES-1];
    int fd_mem = 0, fd_out = 0, fd_fr = 0;
    int got_bytes = 0;

    reg              rst_n = 0;
    reg signed [11:0] i_in = 0, q_in = 0;
    reg              dv_in = 0;

    // —— DUT: MF ——
    wire signed [20:0] mf_i, mf_q;
    wire               mf_dv;
    rx_matched_filter u_mf (
        .clk(clk), .rst_n(rst_n),
        .i_in(i_in), .q_in(q_in), .dv_in(dv_in),
        .i_out(mf_i), .q_out(mf_q), .dv_out(mf_dv)
    );

    // —— DUT: CFO 消旋 (主链) ——
    reg  signed [23:0] phase_inc_reg = 24'sd0;
    reg  signed [23:0] phase_off_reg = 24'sd0;   // 帧起点相位 (est_done 时锁存)
    reg                rot_load = 0;
    wire signed [20:0] rot_i, rot_q;
    wire               rot_dv;

    cfo_rot u_rot (
        .clk(clk), .rst_n(rst_n),
        .i_in(mf_i), .q_in(mf_q), .dv_in(mf_dv),
        .load(rot_load), .phase_inc(phase_inc_reg), .phase_off(est_done ? phase_off : phase_off_reg),
        .i_out(rot_i), .q_out(rot_q), .dv_out(rot_dv)
    );

    // —— DUT: CFO 估计 (挂 MF 输出) ——
    reg                est_start = 0;
    wire               est_done;
    wire [2:0]         p_hat;
    wire signed [23:0] phase_inc;
    wire signed [23:0] phase_off;

    cfo_est #(.W(21)) u_est (
        .clk(clk), .rst_n(rst_n),
        .chip_off(8'd0), .skip_t3(1'b1),   // 本 TB 为外部触发: 触发点在帧起点前
        .i_in(mf_i), .q_in(mf_q), .dv_in(mf_dv),
        .start(est_start),
        .done(est_done), .p_hat(p_hat), .phase_inc(phase_inc), .phase_off(phase_off)
    );

    // —— DUT: 同步 / 解扩 / 解帧 (接消旋后信号) ——
    wire [7:0] data_out, psdu_len;
    wire       data_valid, fcs_ok, frame_done;
    wire signed [20:0] chip_i, chip_q;
    wire               chip_dv, frame_start;
    wire               detect;
    wire [3:0]         locked_phase;

    preamble_sync #(.W(21)) u_sync (
        .clk(clk), .rst_n(rst_n),
        .i_in(rot_i), .q_in(rot_q), .dv_in(rot_dv),
        .ph_thresh(ph_th), .sfd_thresh(sfd_th),
        .frame_done(frame_done),
        .chip_i(chip_i), .chip_q(chip_q), .chip_dv(chip_dv),
        .detect(detect), .frame_start(frame_start), .locked_phase(locked_phase)
    );

    wire [3:0] sym;
    wire       sym_dv;
    despreader #(.W(12)) u_desp (
        .clk(clk), .rst_n(rst_n),
        .chip_i(chip_i[18:7]), .chip_q(chip_q[18:7]), .chip_dv(chip_dv),
        .frame_start(frame_start),
        .sym(sym), .sym_dv(sym_dv)
    );

    rx_deframer u_defr (
        .clk(clk), .rst_n(rst_n),
        .sym(sym), .sym_dv(sym_dv), .frame_start(frame_start),
        .data_out(data_out), .data_valid(data_valid),
        .psdu_len(psdu_len), .fcs_ok(fcs_ok),
        .frame_done(frame_done), .busy()
    );

    // —— 播放计数与触发 ——
    int unsigned k = 0;
    int unsigned f_idx = 0;
    wire at_frame_start = (f_idx < nframes) && (k == fr_mem[f_idx] - 32'd1);

    always @(posedge clk) begin
        if (!rst_n) begin
            est_start <= 1'b0;
            rot_load  <= 1'b0;
            f_idx     <= 0;
            phase_inc_reg <= 24'sd0;
        end else begin
            est_start <= at_frame_start;
            rot_load  <= at_frame_start;
            if (at_frame_start && dv_in) f_idx <= f_idx + 1;
            if (fix_seen) begin
                phase_inc_reg <= fix_inc;                 // 诊断模式: 固定值
            end else if (est_done) begin
                phase_inc_reg <= phase_inc;               // 正常: 用估计值
                phase_off_reg <= phase_off;               // 帧起点相位 (下一帧 load 时加载)
            end
        end
    end

    // —— 事件 dump ——
    reg det_d = 0;
    always @(posedge clk) begin
        if (fd_out != 0 && dv_in) begin
`ifndef NO_DUMP
            if (sym_dv)          $fwrite(fd_out, "SYM %0d %0d\n", k, sym);
            if (frame_start)     $fwrite(fd_out, "FST %0d\n", k);
            if (data_valid)      $fwrite(fd_out, "BYT %0d %0d\n", k, data_out);
            if (frame_done)      $fwrite(fd_out, "FRM %0d %0d %0d\n", k, psdu_len, fcs_ok);
            if (detect && !det_d) $fwrite(fd_out, "DET %0d %0d\n", k, locked_phase);
            if (est_done)        $fwrite(fd_out, "EST %0d %0d %0d %0d\n", k, phase_inc, p_hat, phase_off);
            if (rot_load)        $fwrite(fd_out, "LOAD %0d %0d %0d\n", k, phase_inc_reg, phase_off_reg);
`endif
            if (dump_len > 0 && k >= dump_start && k < dump_start + dump_len)
                $fwrite(fd_out, "RSMP %0d %0d %0d\n", k, rot_i, rot_q);
            if (dump_len > 0 && k >= dump_start && k < dump_start + dump_len)
                $fwrite(fd_out, "SFDW %0d %0d %0d %0d\n", k, u_sync.sfd_n, u_sync.sfd_E[47:0], u_sync.sfd_found);
            if (dump_len > 0 && k >= dump_start && k < dump_start + dump_len)
                $fwrite(fd_out, "PACC %0d %0d\n", k, u_rot.pacc);
            if (dump_len > 0 && k >= dump_start && k < dump_start + dump_len)
                $fwrite(fd_out, "CHIP %0d %0d %0d %0d %0d\n", k, chip_i, chip_q, chip_dv, u_sync.lphase);
        end
        det_d <= detect;
    end

    // —— 主流程 ——
    localparam integer DRAIN = 600;

    initial begin
        if (!$value$plusargs("MEM=%s", mem_path)) begin
            $display("[mc_cfo_tb] FATAL missing +MEM"); $finish;
        end
        if (!$value$plusargs("NSMP=%d", nsmp) || nsmp == 0) begin
            $display("[mc_cfo_tb] FATAL missing/invalid +NSMP"); $finish;
        end
        if (!$value$plusargs("OUT=%s", out_path)) begin
            $display("[mc_cfo_tb] FATAL missing +OUT"); $finish;
        end
        if (!$value$plusargs("FRAMES=%s", fr_path)) begin
            $display("[mc_cfo_tb] FATAL missing +FRAMES"); $finish;
        end
        if (!$value$plusargs("NFRAMES=%d", nframes) || nframes == 0) begin
            $display("[mc_cfo_tb] FATAL missing/invalid +NFRAMES"); $finish;
        end
        if ($value$plusargs("PH=%d", ph_arg))  ph_th  = ph_arg[47:0];
        if ($value$plusargs("SFD=%d", sfd_arg)) sfd_th = sfd_arg[47:0];
        cks_seen = $value$plusargs("CKS=%h", cks_arg);
        fix_seen = $value$plusargs("FIXINC=%d", fix_inc);
        void'($value$plusargs("DUMPSTART=%d", dump_start));
        void'($value$plusargs("DUMPLEN=%d", dump_len));
        if (nframes > MAX_FRAMES) begin
            $display("[mc_cfo_tb] FATAL NFRAMES=%0d exceeds MAX_FRAMES", nframes);
            $finish;
        end
        if (nsmp >= MAX_SMP) begin
            $display("[mc_cfo_tb] FATAL NSMP exceeds MAX_SMP"); $finish;
        end

        fd_mem = $fopen(mem_path, "rb");
        if (fd_mem == 0) begin $display("[mc_cfo_tb] FATAL cannot open %s", mem_path); $finish; end
        got_bytes = $fread(smp_mem, fd_mem);
        $fclose(fd_mem);
        if (got_bytes != nsmp * 4) begin
            $display("[mc_cfo_tb] FATAL mem size %0d != NSMP*4", got_bytes); $finish;
        end
        $readmemh(fr_path, fr_mem);

        if (cks_seen) begin
            reg [31:0] cks = 32'h0;
            int unsigned ci;
            for (ci = 0; ci < 1024 && ci < nsmp; ci = ci + 1) cks ^= smp_mem[ci];
            for (ci = (nsmp > 1024 ? nsmp - 1024 : 0); ci < nsmp; ci = ci + 1)
                cks ^= smp_mem[ci];
            if (cks !== cks_arg) begin
                $display("[mc_cfo_tb] FATAL mem checksum %h != %h", cks, cks_arg);
                $finish;
            end
        end

        // 复位
        rst_n = 0;
        repeat (16) @(posedge clk);
        rst_n = 1;
        @(negedge clk);

        fd_out = $fopen(out_path, "w");
        if (fd_out == 0) begin $display("[mc_cfo_tb] FATAL cannot open out"); $finish; end

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
        $display("[mc_cfo_tb] done: %0d samples, %0d frames triggered", nsmp, f_idx);
        $finish;
    end
endmodule
