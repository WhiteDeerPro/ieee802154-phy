// dual_mc_tb.sv —— rx_dual 的蒙特卡洛 testbench（文件向量驱动, 不依赖 cocotb）
// ---------------------------------------------------------------------------
// 与 tb/rx_chain_e2e/mc_tb.sv 同形态（$fread 播放 + 事件 dump 文本 + Python 后处理），
// 但 DUT = rx_dual：一份 ADC 流喂**两个旋性假设**的通道, 各出码流。
//
// 场景: 多设备交替发帧（mc_gen 的 cfo_list, 每帧一个 CFO = 一台设备的晶振偏差）,
// A/B 两通道各带一组固定参数 —— 统计"单通道覆盖"与"双通道选优"的差异。
//
// 数据流:
//   Python (mc_gen.py): 多帧定点波形（可为每帧指定不同 CFO）→ ADC 12bit → mem.bin
//   VCS (本文件): 播放 → rx_dual → 事件 dump
//   Python (run_dual_mc.py): 读 dump + GT → 按设备分组统计 FCS 成功率
//
// 事件 dump 格式（每行以采样索引 k 开头, 通道后缀 A/B）:
//   SYMA <k> <sym>             通道 A 解扩符号
//   FSTA <k>                   通道 A 定界（sfd_detect.frame_start）
//   BYTA <k> <data>            通道 A deframer 输出字节
//   FRMA <k> <psdu_len> <fcs>  通道 A 帧结束（frame_done）
//   （同组 SYMB/FSTB/BYTB/FRMB）
//   DET  <k> <phase_out>       共享前端锁定指示 + 相位
//
// plusargs:
//   +MEM=<path>  +NSMP=<n>  +OUT=<path>             (必须)
//   +CKS=<hex>   激励抽检（mc_gen 的 mem_cks）
//   +PH=<dec>    +SFD=<dec>                         (门限, 默认同 mc_tb)
//   +NORMT=<dec>  SFD 归一化门限 Q8（ρ_th×256）; 0/缺省 = 绝对门限 sfd_th
//        判据: sfd_E ≥ (norm_th·W)>>2, W = 64 片窗能量（幅度自适应）
//   +INCA=<dec>  +INCB=<dec>  两通道**码片级**相位增量（满量程 2π = 2^24）
//   +PHTAB=<file>  相位表注入（需编译时 EXTPH=1）:
//        每行 "<frame_start_dec> <phase_dec>"; 到达每帧起点前 16 拍把
//        ext_lock_phase 置为该帧相位（真相位标定: (frame_start+8)&15）。
//        用于对比实验: 扫描相位 vs 上帝视角正确相位。
//   -pvalue+dual_mc_tb.EXTPH=1   编译为 1 则 SYNC_DIRECT=1（不例化扫描器）
// ---------------------------------------------------------------------------
`timescale 1ns/1ps
module dual_mc_tb #(
    parameter integer MAX_SMP = 1 << 25,
    parameter integer EXTPH   = 0,     // 1: 相位全外置（SYNC_DIRECT=1 + PHTAB）
    parameter integer RSTEN   = 0      // 1: 帧到达 → 扫描重启（RST_EN=1）
) ();
    // —— 时钟 (16 MHz, 自生成) ——
    reg clk = 0;
    initial forever #31.25 clk = ~clk;

    // —— plusargs 配置 ——
    string       mem_path = "mem.bin";
    string       out_path = "events.txt";
    int unsigned nsmp = 0;
    reg  [47:0]  ph_th  = 48'd200_000_000_000;
    reg  [47:0]  sfd_th = 48'd30_000_000_000_000;
    longint unsigned ph_arg = 0, sfd_arg = 0, inc_arg = 0, normt_arg = 0;
    reg  [31:0]  cks_arg = 32'h0;
    int          cks_seen = 0;
    reg signed [23:0] inc_a = 24'sd0, inc_b = 24'sd0;
    reg  [15:0]  norm_th = 16'd0;

    // —— 激励数组 ——
    reg [31:0] smp_mem [0:MAX_SMP-1];
    int fd_mem = 0, fd_out = 0;
    int got_bytes = 0;

    // —— DUT 输入 ——
    reg              rst_n = 0;
    reg signed [11:0] i_in = 0, q_in = 0;
    reg              dv_in = 0;

    // —— 相位注入（+PHTAB）——
    reg          ext_en = 0;
    reg  [3:0]   ext_ph = 0;
    localparam integer PH_MAX = 8192;
    longint unsigned ph_k [0:PH_MAX-1];
    int              ph_v [0:PH_MAX-1];
    int              ph_n = 0, ph_ptr = 0;
    int              ph_adv = 16;   // +PHADV: 相位切换提前量(拍). 16=标称(帧前16拍); 0=帧起点; 负=帧内迟切(边界扫描后门)
    // —— 多段替换表（+SWAPTAB，上位机灵活决策）——
    localparam integer ST_MAX = 256;
    longint unsigned st_k [0:ST_MAX-1];
    int              st_a [0:ST_MAX-1];
    int              st_b [0:ST_MAX-1];
    int              st_n = 0, st_ptr = 0;
    string           st_path = "";
    int              fd_st = 0;
    string           ph_path = "";
    int              fd_ph = 0;

    // —— DUT: rx_dual（共享前端 + 两通道执行段）——
    wire [7:0] data_a, data_b, len_a, len_b;
    wire       dvld_a, dvld_b, fcs_a, fcs_b, fd_a, fd_b;
    wire [3:0] phase_out;
    wire       detect;
    wire signed [20:0] rot_a_i, rot_a_q, rot_b_i, rot_b_q;
    wire       rot_a_dv, rot_b_dv;
    wire signed [20:0] pbuf_i, pbuf_q;
    wire               pbuf_done;
    reg  [11:0]        pbuf_addr = 12'd0;
    reg                pbuf_clr  = 1'b0;

    rx_dual #(.W(16), .PW(24), .SYNC_DIRECT(EXTPH != 0), .RST_EN(RSTEN != 0)) dut (
        .clk(clk), .rst_n(rst_n),
        .adc_i(i_in), .adc_q(q_in), .adc_dv(dv_in),
        .ph_thresh(ph_th), .sfd_thresh(sfd_th),
        .sfd_norm_th(norm_th),
        .ext_lock_en(ext_en), .ext_lock_phase(ext_ph), .rot_load(1'b0),
        .phase_inc_chip_a(inc_a), .phase_off_a(24'd0),
        .phase_inc_chip_b(inc_b), .phase_off_b(24'd0),
        .data_a(data_a), .data_valid_a(dvld_a), .psdu_len_a(len_a),
        .fcs_ok_a(fcs_a), .frame_done_a(fd_a),
        .data_b(data_b), .data_valid_b(dvld_b), .psdu_len_b(len_b),
        .fcs_ok_b(fcs_b), .frame_done_b(fd_b),
        .phase_out(phase_out), .detect(detect),
        .rot_a_i(rot_a_i), .rot_a_q(rot_a_q),
        .rot_b_i(rot_b_i), .rot_b_q(rot_b_q),
        .rot_a_dv(rot_a_dv), .rot_b_dv(rot_b_dv),
        .pbuf_addr(pbuf_addr), .pbuf_clr(pbuf_clr),
        .pbuf_i(pbuf_i), .pbuf_q(pbuf_q), .pbuf_done(pbuf_done),
        .any_fcs_ok()
    );

    // —— 播放计数: 当前被 DUT 采样的激励索引 ——
    int unsigned k = 0;

    // —— 事件 dump (posedge 采样沿记录) ——
    reg det_d = 0;
    reg fcs_a_d = 0, fcs_b_d = 0;
    always @(posedge clk) begin
        if (fd_out != 0 && dv_in) begin
            if (dvld_a)          $fwrite(fd_out, "BYTA %0d %0d\n", k, data_a);
            if (fd_a)            $fwrite(fd_out, "FRMA %0d %0d %0d\n", k, len_a, fcs_a);
            if (dvld_b)          $fwrite(fd_out, "BYTB %0d %0d\n", k, data_b);
            if (fd_b)            $fwrite(fd_out, "FRMB %0d %0d %0d\n", k, len_b, fcs_b);
            // fcs_ok 保持到下一帧, 用上升沿作为“本帧校验通过”的一次性事件
            if (fcs_a && !fcs_a_d) $fwrite(fd_out, "FCSA %0d\n", k);
            if (fcs_b && !fcs_b_d) $fwrite(fd_out, "FCSB %0d\n", k);
            if (detect && !det_d) $fwrite(fd_out, "DET %0d %0d\n", k, phase_out);
        end
        det_d   <= detect;
        fcs_a_d <= fcs_a;
        fcs_b_d <= fcs_b;
    end

    // —— 符号/定界事件用层次探针（rx_dual 未引出这两个观测点）——
    wire [3:0] sym_a = dut.u_be_a.u_desp.sym;
    wire       sym_a_dv = dut.u_be_a.u_desp.sym_dv;
    wire       fs_a = dut.u_be_a.fs_ch;
    wire [3:0] sym_b = dut.u_be_b.u_desp.sym;
    wire       sym_b_dv = dut.u_be_b.u_desp.sym_dv;
    wire       fs_b = dut.u_be_b.fs_ch;
    always @(posedge clk) begin
        if (fd_out != 0 && dv_in) begin
            if (sym_a_dv) $fwrite(fd_out, "SYMA %0d %0d\n", k, sym_a);
            if (sym_b_dv) $fwrite(fd_out, "SYMB %0d %0d\n", k, sym_b);
            if (fs_a)     $fwrite(fd_out, "FSTA %0d\n", k);
            if (fs_b)     $fwrite(fd_out, "FSTB %0d\n", k);
        end
    end

    // —— 定界器内部状态追踪（调试: found 的锁死/误触发）——
    reg fnd_a_d = 0, fnd_b_d = 0;
    always @(posedge clk) begin
        if (fd_out != 0 && dv_in) begin
            if (dut.u_be_a.u_sfd.found != fnd_a_d)
                $fwrite(fd_out, "FNDA %0d %0d\n", k, dut.u_be_a.u_sfd.found);
            if (dut.u_be_b.u_sfd.found != fnd_b_d)
                $fwrite(fd_out, "FNDB %0d %0d\n", k, dut.u_be_b.u_sfd.found);
        end
        fnd_a_d <= dut.u_be_a.u_sfd.found;
        fnd_b_d <= dut.u_be_b.u_sfd.found;
    end

    // —— 诊断: phase_out 的变化历史（latch 是否在帧间被改写）——
    reg [3:0] ph_d = 4'h0;
    always @(posedge clk) begin
        if (fd_out != 0 && dv_in && phase_out !== ph_d)
            $fwrite(fd_out, "PHCH %0d %0d\n", k, phase_out);
        ph_d <= phase_out;
    end

    // —— 诊断: 注入相位的变化历史（EXTK, 便于核对 PHTAB 生效时刻）——
    reg [3:0] ext_ph_d = 4'h0;
    always @(posedge clk) begin
        if (fd_out != 0 && dv_in && ext_en && ext_ph !== ext_ph_d)
            $fwrite(fd_out, "EXTK %0d %0d\n", k, ext_ph);
        ext_ph_d <= ext_ph;
    end

    // —— 诊断: 指定区间的 sfd_E 全序列（+EDA/+EDB 给出窗口）——
    longint unsigned eda = 0, edb = 0;
    initial begin
        $value$plusargs("EDA=%d", eda);
        $value$plusargs("EDB=%d", edb);
    end
    always @(posedge clk) begin
        if (fd_out != 0 && dv_in && edb > eda && k >= eda && k <= edb
            && dut.u_be_a.u_sfd.chip_dv)
            $fwrite(fd_out, "SFW %0d %0d\n", k, dut.u_be_a.u_sfd.sfd_E);
    end

    // —— 前导缓冲 dump: +PBUF=<path>（I-13; done 上升沿后立即扫 PRE+POST=1024 地址）——
    // 缓冲在 done 后冻结写入, 故读出内容在扫描期间恒有效。
    string pbuf_path = "";
    int    fd_pbuf = 0;
    initial begin
        if ($value$plusargs("PBUF=%s", pbuf_path)) fd_pbuf = $fopen(pbuf_path, "w");
    end
    reg        pb_run  = 1'b0;
    reg [12:0] pb_cnt  = 13'd0;
    reg        pb_dn_d = 1'b0;
    always @(posedge clk) begin
        pb_dn_d <= pbuf_done;
        if (fd_pbuf != 0) begin
            if (pbuf_done && !pb_dn_d && !pb_run) begin
                pb_run <= 1'b1;
                pb_cnt <= 13'd0;
                $display("[dual_mc_tb] PBUF capture done @k=%0d, dumping...", k);
`ifdef PBUF_DBG
                $display("[pbuf-dbg] bmax0=%0d bmax1=%0d phase=%0d fill=%0d blk=%0d",
                         dut.u_fe.g_scan.u_pbuf.bmax0, dut.u_fe.g_scan.u_pbuf.bmax1,
                         dut.u_fe.g_scan.u_pbuf.phase, dut.u_fe.g_scan.u_pbuf.fill,
                         dut.u_fe.g_scan.u_pbuf.blk_no);
                $display("[pbuf-dbg] total=%0d wr=%0d trig=%0d",
                         dut.u_fe.g_scan.u_pbuf.total_in, dut.u_fe.g_scan.u_pbuf.wr_stream,
                         dut.u_fe.g_scan.u_pbuf.trig_stream);
                $display("[pbuf-dbg] exp_new=%p", dut.u_fe.g_scan.u_pbuf.exp_new);
                $display("[pbuf-dbg] exp_old=%p", dut.u_fe.g_scan.u_pbuf.exp_old);
`endif
            end else if (pb_run) begin
                pbuf_addr <= pb_cnt[11:0];
                if (pb_cnt > 0)
`ifdef PBUF_RAWCOL
                    // 5 列版（+define+PBUF_RAWCOL）: 附 raw 尾数 + 块指数——仅新版 pbuf 可用
                    $fwrite(fd_pbuf, "%0d %0d %0d %0d %0d\n", pb_cnt - 1, pbuf_i, pbuf_q,
                             dut.u_fe.g_scan.u_pbuf.mem_i[dut.u_fe.g_scan.u_pbuf.ra],
                             dut.u_fe.g_scan.u_pbuf.rexp);
`else
                    $fwrite(fd_pbuf, "%0d %0d %0d\n", pb_cnt - 1, pbuf_i, pbuf_q);
`endif
                if (pb_cnt == 13'd1024) begin
                    pb_run <= 1'b0;
                    $display("[dual_mc_tb] PBUF dumped: 1024 samples");
                    $fclose(fd_pbuf);
                    fd_pbuf = 0;
                end else begin
                    pb_cnt <= pb_cnt + 1'b1;
                end
            end
        end
    end

`ifdef PBUF_DBG
    // —— pbuf 探针（调试：样本 368 的收集→回写→写入链）——
    always @(posedge clk) begin
        if (dut.u_fe.g_scan.u_pbuf.accept &&
            dut.u_fe.g_scan.u_pbuf.total_in == 24'd368)
            $display("[p368] collect#368 i_in=%0d q_in=%0d",
                     dut.u_fe.g_scan.u_pbuf.i_in, dut.u_fe.g_scan.u_pbuf.q_in);
        if (dut.u_fe.g_scan.u_pbuf.wb_en &&
            dut.u_fe.g_scan.u_pbuf.blk_no == 4'd5 &&
            dut.u_fe.g_scan.u_pbuf.c_cnt == 6'd48)
            $display("[p368] wb blk5/off48 Bq=%0d s_i=%0d t_i=%0d wr=%0d",
                     dut.u_fe.g_scan.u_pbuf.Bq, dut.u_fe.g_scan.u_pbuf.s_i,
                     dut.u_fe.g_scan.u_pbuf.t_i, dut.u_fe.g_scan.u_pbuf.wr_stream);
    end

`endif
    // —— 强制 restart 注入（测试）: +INJRST=<k> 在采样 k 注入一拍 scan_restart ——
    // 用于构造"锁定被 restart 打掉"的边界场景（force/release 同步器输入, 不影响 RTL）。
    generate
    if (EXTPH == 0) begin : g_inj        // scan_restart 只在扫描分支存在
        longint unsigned inj_k = 0;
        initial void'($value$plusargs("INJRST=%d", inj_k));
        always @(posedge clk) begin
            if (inj_k != 0 && k == inj_k)
                force dut.u_fe.g_scan.u_sync.scan_restart = 1'b1;
            else if (inj_k != 0 && k == inj_k + 1)
                release dut.u_fe.g_scan.u_sync.scan_restart;
        end
    end
    endgenerate

    // —— 眼图 dump: +EYEDUMP=<path> +EYES=<start> +EYEE=<end>（采样级 MF 输出）——
    // 采样级不加消旋（RTL 的消旋在码片级），Python 侧用与各通道同参数的
    // 理想旋转做“矫正后”对照 —— 与 chip 级消旋数学等价（复乘与抽取可交换）。
    string eye_path = "";
    int    fd_eye = 0;
    longint unsigned eye_s = 0, eye_e = 0;
    initial begin
        if ($value$plusargs("EYEDUMP=%s", eye_path)) fd_eye = $fopen(eye_path, "w");
        void'($value$plusargs("EYES=%d", eye_s));
        void'($value$plusargs("EYEE=%d", eye_e));
    end
    always @(posedge clk) begin
        if (fd_eye != 0 && dv_in && k >= eye_s && k <= eye_e)
            $fwrite(fd_eye, "%0d %0d %0d\n", k, dut.u_fe.mf_i, dut.u_fe.mf_q);
    end

    // —— VCD 波形 dump: +VCD=<path> [VCDT0=.. VCDT1=..]（dump 同步器内部）——
    // 仅 EXTPH=0（扫描路径存在）时有效; 给定 VCDT0/T1 时只 dump 该采样窗口。
    string vcd_path = "";
    longint unsigned vcd_t0 = 0, vcd_t1 = 0;
    reg vcd_en = 1'b0;
    initial begin
        if (EXTPH == 0 && $value$plusargs("VCD=%s", vcd_path)) begin
            void'($value$plusargs("VCDT0=%d", vcd_t0));
            void'($value$plusargs("VCDT1=%d", vcd_t1));
            $dumpfile(vcd_path);
            $dumpvars(0, dut.u_fe);   // 注: 用共有父层（g_scan 仅扫描分支存在, EXTPH=1 编不过）
            vcd_en <= 1'b1;
            if (vcd_t1 != 0) $dumpoff;        // 窗口模式: 先关, 到点再开
        end
    end
    always @(posedge clk) begin
        if (vcd_en) begin
            if (vcd_t1 != 0 && k == vcd_t0) $dumpon;
            if (vcd_t1 != 0 && k == vcd_t1) $dumpoff;
        end
    end

    // —— 码片级消旋 dump: +ROTDUMP=<path>（每码片一行: A/B k rot_i rot_q）——
    // 供 Python 侧做解扩星座/符号判决（每 32 片一符号, 与 despreader 同口径）。
    string rot_path = "";
    int    fd_rot = 0;
    initial begin
        if ($value$plusargs("ROTDUMP=%s", rot_path)) fd_rot = $fopen(rot_path, "w");
    end
    always @(posedge clk) begin
        if (fd_rot != 0 && dv_in) begin
            if (rot_a_dv) $fwrite(fd_rot, "A %0d %0d %0d\n", k, rot_a_i, rot_a_q);
            if (rot_b_dv) $fwrite(fd_rot, "B %0d %0d %0d\n", k, rot_b_i, rot_b_q);
        end
    end

    // —— 诊断: 扫描重启脉冲 PDR + 扫描状态翻转 SYSC（仅 EXTPH=0 时存在）——
    generate
    if (EXTPH == 0) begin : g_pdr
        wire pd_rst_dbg = dut.u_fe.g_scan.u_pd.det_pulse;
        wire [1:0] st_dbg = dut.u_fe.g_scan.u_sync.state;
        reg [1:0] st_d = 2'd0;
        always @(posedge clk) begin
            if (fd_out != 0 && dv_in) begin
                if (pd_rst_dbg)          $fwrite(fd_out, "PDR %0d\n", k);
                if (st_dbg !== st_d)     $fwrite(fd_out, "SYSC %0d %0d\n", k, st_dbg);
            end
            st_d <= st_dbg;
        end
    end
    endgenerate

    // —— 通道参数运行时替换: +SWAPK=<k> +SWAPB=<chip_inc>（帧间生效; I-10 协议雏形）——
    longint unsigned swapk_arg = 0, swapb_arg = 0;
    initial begin
        void'($value$plusargs("SWAPK=%d", swapk_arg));
        void'($value$plusargs("SWAPB=%d", swapb_arg));
    end

    // —— 主流程 ——
    localparam integer DRAIN = 600;

    initial begin
        if (!$value$plusargs("MEM=%s", mem_path)) begin
            $display("[dual_mc_tb] FATAL missing +MEM"); $finish;
        end
        if (!$value$plusargs("NSMP=%d", nsmp) || nsmp == 0) begin
            $display("[dual_mc_tb] FATAL missing/invalid +NSMP"); $finish;
        end
        if (!$value$plusargs("OUT=%s", out_path)) begin
            $display("[dual_mc_tb] FATAL missing +OUT"); $finish;
        end
        if ($value$plusargs("PH=%d", ph_arg))  ph_th  = ph_arg[47:0];
        if ($value$plusargs("SFD=%d", sfd_arg)) sfd_th = sfd_arg[47:0];
        if ($value$plusargs("NORMT=%d", normt_arg)) norm_th = normt_arg[15:0];
        if ($value$plusargs("INCA=%d", inc_arg)) inc_a = inc_arg[23:0];
        if ($value$plusargs("INCB=%d", inc_arg)) inc_b = inc_arg[23:0];
        cks_seen = $value$plusargs("CKS=%h", cks_arg);
        if (nsmp >= MAX_SMP) begin
            $display("[dual_mc_tb] FATAL NSMP=%0d exceeds MAX_SMP=%0d", nsmp, MAX_SMP);
            $finish;
        end

        fd_mem = $fopen(mem_path, "rb");
        if (fd_mem == 0) begin
            $display("[dual_mc_tb] FATAL cannot open %s", mem_path); $finish;
        end
        got_bytes = $fread(smp_mem, fd_mem);
        $fclose(fd_mem);
        if (got_bytes != nsmp * 4) begin
            $display("[dual_mc_tb] FATAL mem size %0d != NSMP*4 (%0d)",
                     got_bytes, nsmp * 4);
            $finish;
        end

        if (cks_seen) begin
            reg [31:0] cks = 32'h0;
            int unsigned ci;
            for (ci = 0; ci < 1024 && ci < nsmp; ci = ci + 1) cks ^= smp_mem[ci];
            for (ci = (nsmp > 1024 ? nsmp - 1024 : 0); ci < nsmp; ci = ci + 1)
                cks ^= smp_mem[ci];
            if (cks !== cks_arg) begin
                $display("[dual_mc_tb] FATAL mem checksum %h != %h", cks, cks_arg);
                $finish;
            end
            $display("[dual_mc_tb] mem checksum OK (%h)", cks);
        end

        rst_n = 0;
        repeat (16) @(posedge clk);
        rst_n = 1;
        @(negedge clk);

        void'($value$plusargs("PHADV=%d", ph_adv));
        if ($value$plusargs("PHTAB=%s", ph_path)) begin
            fd_ph = $fopen(ph_path, "r");
            if (fd_ph == 0) begin
                $display("[dual_mc_tb] FATAL cannot open PHTAB %s", ph_path);
                $finish;
            end
            while (ph_n < PH_MAX &&
                   $fscanf(fd_ph, "%d %d\n", ph_k[ph_n], ph_v[ph_n]) == 2)
                ph_n = ph_n + 1;
            $fclose(fd_ph);
            ext_en = 1;
            $display("[dual_mc_tb] PHTAB loaded: %0d entries (EXTPH=%0d)",
                     ph_n, EXTPH);
        end
        if ($value$plusargs("SWAPTAB=%s", st_path)) begin
            fd_st = $fopen(st_path, "r");
            if (fd_st == 0) begin
                $display("[dual_mc_tb] FATAL cannot open SWAPTAB %s", st_path);
                $finish;
            end
            while (st_n < ST_MAX &&
                   $fscanf(fd_st, "%d %d %d\n", st_k[st_n], st_a[st_n], st_b[st_n]) == 3)
                st_n = st_n + 1;
            $fclose(fd_st);
            $display("[dual_mc_tb] SWAPTAB loaded: %0d entries", st_n);
        end

        fd_out = $fopen(out_path, "w");
        if (fd_out == 0) begin
            $display("[dual_mc_tb] FATAL cannot open out %s", out_path); $finish;
        end

        for (k = 0; k < nsmp; k = k + 1) begin
            // 运行时替换通道 B 参数（帧间时刻触发）
            if (swapb_arg != 0 && k == swapk_arg) inc_b = swapb_arg[23:0];
            // 多段替换表: +SWAPTAB（每行 k inc_a inc_b; 0=该通道不改）——上位机轮换/决策
            while (st_ptr < st_n && longint'(k) >= longint'(st_k[st_ptr])) begin
                if (st_a[st_ptr][23:0] != 0) inc_a = st_a[st_ptr][23:0];
                if (st_b[st_ptr][23:0] != 0) inc_b = st_b[st_ptr][23:0];
                st_ptr = st_ptr + 1;
            end
            // 到达下一帧起点前 16 拍: 切换注入相位（帧间隙处, 不影响前帧）
            // PHADV 可调: 正=提前量(标称16); 0=帧起点切换; 负=帧内第|PHADV|拍才切(迟切边界)
            while (ph_ptr < ph_n && longint'(k) + ph_adv >= longint'(ph_k[ph_ptr])) begin
                ext_ph = ph_v[ph_ptr][3:0];
                ph_ptr = ph_ptr + 1;
            end
            i_in = smp_mem[k][11:0];
            q_in = smp_mem[k][23:12];
            dv_in = 1;
            @(negedge clk);
        end
        dv_in = 0;
        i_in = 0;
        q_in = 0;

        repeat (DRAIN) @(posedge clk);

        // （前导缓冲的读出已移至 done 上升沿的并行进程, 详见上方 pbuf dump 段）

        $fclose(fd_out);
        if (fd_rot != 0) $fclose(fd_rot);
        if (fd_eye != 0) $fclose(fd_eye);

        $display("[dual_mc_tb] done: %0d samples played, inc_a=%0d inc_b=%0d, sim %0t",
                 nsmp, inc_a, inc_b, $time);
        $display("[dual_mc_tb] A: defr.state=%0d byte_idx=%0d psdu_len=%0d crc_busy=%b fcs_ok=%b",
                 dut.u_be_a.u_defr.state, dut.u_be_a.u_defr.byte_idx,
                 dut.u_be_a.u_defr.psdu_len, dut.u_be_a.u_defr.crc_busy,
                 dut.u_be_a.u_defr.fcs_ok);
        $display("[dual_mc_tb] B: defr.state=%0d byte_idx=%0d psdu_len=%0d crc_busy=%b fcs_ok=%b",
                 dut.u_be_b.u_defr.state, dut.u_be_b.u_defr.byte_idx,
                 dut.u_be_b.u_defr.psdu_len, dut.u_be_b.u_defr.crc_busy,
                 dut.u_be_b.u_defr.fcs_ok);
        $finish;
    end
endmodule
