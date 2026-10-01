// mc_tb.sv —— 蒙特卡洛全链 testbench (文件向量驱动, 不依赖 cocotb)
// ---------------------------------------------------------------------------
// 为什么不用 cocotb: cocotb 逐采样驱动是 Python↔VPI 交互, 实测 ~300 µs/采样,
// 蒙特卡洛要 1e6 比特量级 (= 数千万采样) → 小时级; 本 TB 让 VCS 自己播放
// 预生成的激励 mem, 纯仿真速度 (实测 ~4M 采样/s, 快 ~1000x)。判据是统计量
// (BER 曲线), 不是逐拍比对, 所以"文件向量 + 后处理"是合适的形态。
//
// 数据流:
//   Python (mc_gen.py): 多帧定点波形 + AWGN → ADC 12bit 量化 → mem.bin
//                       每采样 32bit: [11:0]=I, [23:12]=Q (signed 12bit)
//   VCS (本文件): $fread 读入数组 → negedge 播放 → 事件 dump 文本
//   Python (run_mc.py): 读 dump + GT → 统计 BER/SER/FER
//
// 事件 dump 格式 (每行以事件发生时的采样索引 k 开头):
//   SYM <k> <sym>              解扩输出符号 (sym_dv)
//   FST <k>                   帧起始 (frame_start, 与首个 PHR 码片同拍)
//   BYT <k> <data>             deframer 输出字节 (data_valid)
//   FRM <k> <psdu_len> <fcs>  帧结束 (frame_done)
//   DET <k> <phase>            前导检测 (detect 上升沿)
//
// plusargs:
//   +MEM=<path>  激励 mem 文件 (二进制, 必须)
//   +NSMP=<n>    采样数 (必须)
//   +OUT=<path>  事件 dump 输出 (必须)
//   +PH=<dec>    前导检测门限 (默认 2e11)
//   +SFD=<dec>   SFD 检测门限 (默认 3e13)
//   +CKS=<hex>   激励抽检 checksum (mc_gen 头尾各 1024 元素 XOR), 不符则 FATAL
//                —— 防止 $fread 字节序/偏移类错误静默地把激励读乱
`timescale 1ns/1ps
module mc_tb #(
    parameter integer MAX_SMP = 1 << 25     // 采样上限 (32M, 每点 2000 帧留余量)
) ();
    // —— 时钟 (16 MHz, 自生成) ——
    reg clk = 0;
    initial forever #31.25 clk = ~clk;

    // —— plusargs 配置 ——
    string       mem_path = "mem.bin";
    string       out_path = "events.txt";
    int unsigned nsmp = 0;
    reg  [47:0]  ph_th = 48'd200_000_000_000;
    reg  [47:0]  sfd_th = 48'd30_000_000_000_000;
    longint unsigned ph_arg = 0, sfd_arg = 0;
    reg  [31:0]  cks_arg = 32'h0;
    int          cks_seen = 0;

    // —— 激励数组 ——
    reg [31:0] smp_mem [0:MAX_SMP-1];
    int fd_mem = 0, fd_out = 0;
    int got_bytes = 0;

    // —— DUT 输入 (negedge 驱动, 上升沿被 DUT 采样) ——
    reg              rst_n = 0;
    reg signed [11:0] i_in = 0, q_in = 0;
    reg              dv_in = 0;

    // —— DUT: e2e 链 (与 tb/rx_chain_e2e/e2e_top.sv 相同接线 + 符号探针) ——
    wire signed [20:0] mf_i, mf_q;
    wire               mf_dv;
    rx_matched_filter u_mf (
        .clk(clk), .rst_n(rst_n),
        .i_in(i_in), .q_in(q_in), .dv_in(dv_in),
        .i_out(mf_i), .q_out(mf_q), .dv_out(mf_dv)
    );

    wire signed [20:0] chip_i, chip_q;
    wire               chip_dv, frame_start;
    wire               detect;
    wire [3:0]             locked_phase;

    preamble_sync #(.W(21)) u_sync (
        .clk(clk), .rst_n(rst_n),
        .i_in(mf_i), .q_in(mf_q), .dv_in(mf_dv),
        .ph_thresh(ph_th), .sfd_thresh(sfd_th),
        .frame_done(frame_done),
        .ext_lock_en(1'b0), .ext_lock_phase(4'd0),
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

    wire [7:0] data_out, psdu_len;
    wire       data_valid, fcs_ok, frame_done;

    rx_deframer u_defr (
        .clk(clk), .rst_n(rst_n),
        .sym(sym), .sym_dv(sym_dv), .frame_start(frame_start),
        .data_out(data_out), .data_valid(data_valid),
        .psdu_len(psdu_len), .fcs_ok(fcs_ok),
        .frame_done(frame_done), .busy()
    );

    // —— 播放计数: 当前被 DUT 采样的激励索引 ——
    int unsigned k = 0;

    // —— 事件 dump (posedge 采样沿记录) ——
    // `ifdef NO_DUMP 用于性能剖析 (只跑播放+链, 不写事件文件)
    reg det_d = 0;
    always @(posedge clk) begin
        if (fd_out != 0 && dv_in) begin
`ifndef NO_DUMP
            if (sym_dv)          $fwrite(fd_out, "SYM %0d %0d\n", k, sym);
            if (frame_start)     $fwrite(fd_out, "FST %0d\n", k);
            if (data_valid)      $fwrite(fd_out, "BYT %0d %0d\n", k, data_out);
            if (frame_done)      $fwrite(fd_out, "FRM %0d %0d %0d\n", k, psdu_len, fcs_ok);
            if (detect && !det_d) $fwrite(fd_out, "DET %0d %0d\n", k, locked_phase);
`endif
        end
        det_d <= detect;
    end

    // —— 主流程: 读激励 → 复位 → 播放 → 排空 → 收尾 ——
    localparam integer DRAIN = 600;      // 帧尾排空 (≥ 一个 PSDU 符号的管流深度)

    initial begin
        if (!$value$plusargs("MEM=%s", mem_path)) begin
            $display("[mc_tb] FATAL missing +MEM"); $finish;
        end
        if (!$value$plusargs("NSMP=%d", nsmp) || nsmp == 0) begin
            $display("[mc_tb] FATAL missing/invalid +NSMP"); $finish;
        end
        if (!$value$plusargs("OUT=%s", out_path)) begin
            $display("[mc_tb] FATAL missing +OUT"); $finish;
        end
        if ($value$plusargs("PH=%d", ph_arg))  ph_th  = ph_arg[47:0];
        if ($value$plusargs("SFD=%d", sfd_arg)) sfd_th = sfd_arg[47:0];
        cks_seen = $value$plusargs("CKS=%h", cks_arg);
        if (nsmp >= MAX_SMP) begin
            $display("[mc_tb] FATAL NSMP=%0d exceeds MAX_SMP=%0d", nsmp, MAX_SMP);
            $finish;
        end

        fd_mem = $fopen(mem_path, "rb");
        if (fd_mem == 0) begin
            $display("[mc_tb] FATAL cannot open %s", mem_path); $finish;
        end
        got_bytes = $fread(smp_mem, fd_mem);
        $fclose(fd_mem);
        if (got_bytes != nsmp * 4) begin
            $display("[mc_tb] FATAL mem size %0d bytes != NSMP*4 (%0d)",
                     got_bytes, nsmp * 4);
            $finish;
        end

        // 激励完整性抽检: 头尾各 1024 元素的 XOR 必须与 Python 侧一致
        if (cks_seen) begin
            reg [31:0] cks = 32'h0;
            int unsigned ci;
            for (ci = 0; ci < 1024 && ci < nsmp; ci = ci + 1) cks ^= smp_mem[ci];
            for (ci = (nsmp > 1024 ? nsmp - 1024 : 0); ci < nsmp; ci = ci + 1)
                cks ^= smp_mem[ci];
            if (cks !== cks_arg) begin
                $display("[mc_tb] FATAL mem checksum %h != expected %h (字节序/激励读入错误)",
                         cks, cks_arg);
                $finish;
            end
            $display("[mc_tb] mem checksum OK (%h)", cks);
        end

        // 复位序列
        rst_n = 0;
        repeat (16) @(posedge clk);
        rst_n = 1;
        @(negedge clk);

        fd_out = $fopen(out_path, "w");
        if (fd_out == 0) begin
            $display("[mc_tb] FATAL cannot open out %s", out_path); $finish;
        end

        // 播放: k 之后的 negedge 更新值, 下一 posedge 被 DUT 采样
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

        $display("[mc_tb] done: %0d samples played, sim time %0t", nsmp, $time);
        $finish;
    end
endmodule
