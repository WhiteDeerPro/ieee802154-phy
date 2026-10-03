// stress_tb.sv —— despreader_oct8 压力台（文件回放, 纯 SV, 无 cocotb）。
// ---------------------------------------------------------------------------
// 用法: simv_stress +MEM=<mem.bin> +NSMP=<n> +GT=<gt.hex>
//   mem.bin: >u4 大端, 32bit/采样: i[11:0] | q[23:12]（与 dual_mc 场景同约定）
//   gt.hex : $readmemh 文本, 每符号一行十六进制（4bit）
// 节奏: 每片 8 拍（16MHz / 2MHz）; frame_start 每 32 片。
// 统计: ref/oct 各自对 GT 的错误数 + 两者判决分歧数（全部在 RTL 级完成,
//       判决拍 = ref/oct sym_dv 同拍）。
// ---------------------------------------------------------------------------
`timescale 1ns/1ps
module stress_tb;
    parameter integer MAX_SMP = 8000000;
    parameter integer MAX_SYM = 250000;

    reg clk = 0;
    always #31.25 clk = ~clk;      // 16 MHz

    reg rst_n = 0;

    reg [31:0] smp [0:MAX_SMP-1];
    reg [3:0]  gt  [0:MAX_SYM-1];

    reg signed [11:0] chip_i, chip_q;
    reg               chip_dv, frame_start;

    wire [3:0] sym_ref,  sym_oct;
    wire       sym_dv_ref, sym_dv_oct;

    despreader u_ref (
        .clk(clk), .rst_n(rst_n),
        .chip_i(chip_i), .chip_q(chip_q), .chip_dv(chip_dv),
        .frame_start(frame_start),
        .sym(sym_ref), .sym_dv(sym_dv_ref)
    );
    despreader_oct8 u_oct (
        .clk(clk), .rst_n(rst_n),
        .chip_i(chip_i), .chip_q(chip_q), .chip_dv(chip_dv),
        .frame_start(frame_start),
        .sym(sym_oct), .sym_dv(sym_dv_oct)
    );

    integer n_smp, n_sym;
    integer fd, rc;
    string mem_path, gt_path;

    // —— 片时序（每片 8 拍）——
    integer t, ci;
    always @(posedge clk) begin
        if (!rst_n) begin
            t  <= 0;
            ci <= 0;
        end else if (ci < n_smp) begin
            if (t == 7) begin t <= 0; ci <= ci + 1; end
            else              t <= t + 1;
        end
    end

    always @(*) begin
        if (ci < n_smp) begin
            chip_i = smp[ci][11:0];
            chip_q = smp[ci][23:12];
        end else begin
            chip_i = 12'sd0;
            chip_q = 12'sd0;
        end
    end

    assign chip_dv     = rst_n && (ci < n_smp) && (t == 0);
    assign frame_start = chip_dv && (ci % 32 == 0);

    // —— 统计（判决拍比对）——
    integer j = 0, ref_err = 0, oct_err = 0, diff_cnt = 0;
    integer guard = 0;
    integer ref_dv_cnt = 0, oct_dv_cnt = 0;
    reg ref_dv_d = 0, oct_dv_d = 0;
    integer dv_total = 0, hit_ref = 0, fs_total = 0;
    integer dv_first_ci = -1, dv_last_ci = -1;
    integer hit_first_ci = -1, hit_last_ci = -1;

    initial begin
        if (!$value$plusargs("MEM=%s", mem_path))  begin $display("ERR: +MEM missing");  $finish; end
        if (!$value$plusargs("GT=%s",  gt_path))   begin $display("ERR: +GT missing");   $finish; end
        if (!$value$plusargs("NSMP=%d", n_smp))    begin $display("ERR: +NSMP missing"); $finish; end
        n_sym = n_smp / 32;

        fd = $fopen(mem_path, "rb");
        if (fd == 0) begin $display("ERR: cannot open %s", mem_path); $finish; end
        rc = $fread(smp, fd);
        $fclose(fd);
        $readmemh(gt_path, gt);
        $display("[stress] mem=%s smp=%0d gt=%s sym=%0d", mem_path, n_smp, gt_path, n_sym);
        $display("[stress] smp[0]=%h smp[1]=%h smp[2]=%h smp[3]=%h",
                 smp[0], smp[1], smp[2], smp[3]);

        repeat (16) @(posedge clk);
        @(negedge clk);          // 沿间释放复位: 避免片 0 的 dv 窗被"半周期错过"
        rst_n = 1;
    end

    always @(posedge clk) begin
        if (rst_n) begin
            guard = guard + 1;
            if (guard > n_smp * 8 + 200000) begin
                $display("ERR: timeout j=%0d/%0d ci=%0d t=%0d cc_ref=%0d cc_oct=%0d dvr=%b dvo=%b",
                         j, n_sym, ci, t, u_ref.chip_cnt, u_oct.chip_cnt,
                         sym_dv_ref, sym_dv_oct);
                $display("ERR: dv 边沿数 ref=%0d oct=%0d | dv_total=%0d hit_ref=%0d fs_total=%0d",
                         ref_dv_cnt, oct_dv_cnt, dv_total, hit_ref, fs_total);
                $display("ERR: dv ci 首/末=%0d/%0d | hit ci 首/末=%0d/%0d",
                         dv_first_ci, dv_last_ci, hit_first_ci, hit_last_ci);
                $finish;
            end
            if (chip_dv) begin
                dv_total = dv_total + 1;
                if (dv_first_ci < 0) dv_first_ci = ci;
                dv_last_ci = ci;
                if (u_ref.chip_cnt == 6'd31) begin
                    hit_ref = hit_ref + 1;
                    if (hit_first_ci < 0) hit_first_ci = ci;
                    hit_last_ci = ci;
                end
            end
            if (frame_start) fs_total = fs_total + 1;
            if (sym_dv_ref && !ref_dv_d) ref_dv_cnt = ref_dv_cnt + 1;
            if (sym_dv_oct && !oct_dv_d) oct_dv_cnt = oct_dv_cnt + 1;
            ref_dv_d = sym_dv_ref;
            oct_dv_d = sym_dv_oct;
            if (sym_dv_ref && sym_dv_oct && j < n_sym) begin
                if (sym_ref !== gt[j]) ref_err = ref_err + 1;
                if (sym_oct !== gt[j]) oct_err = oct_err + 1;
                if (sym_oct !== sym_ref) diff_cnt = diff_cnt + 1;
                j = j + 1;
                if (j % 25000 == 0)
                    $display("[stress] %0d/%0d: ref_err=%0d oct_err=%0d diff=%0d",
                             j, n_sym, ref_err, oct_err, diff_cnt);
                if (j == n_sym) begin
                    $display("=== stress done: N=%0d ref_err=%0d oct_err=%0d diff=%0d ===",
                             n_sym, ref_err, oct_err, diff_cnt);
                    $finish;
                end
            end
        end
    end
endmodule
