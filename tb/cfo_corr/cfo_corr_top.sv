// cfo_corr_top.sv —— cfo_est + cfo_rot 集成顶层 (cocotb 测试用)
// ---------------------------------------------------------------------------
// est 与 rot 内部串联: est 估出的 phase_inc 直接送进 rot; 同时也引到端口上,
// 让测试可以核对估计值, 并用它和 Python 模型比对。
//
// 使用顺序 (测试侧):
//   1) est_start 打一拍 → 接下来 2048 个有效采样被当作前导收进 cfo_est
//   2) 等 est_done → 读 p_hat / phase_inc
//   3) rot_load 打一拍 (相位累加器归零) → 之后喂整帧, i_out/q_out 即消旋结果
`timescale 1ns/1ps
module cfo_corr_top (
    input  wire                clk,
    input  wire                rst_n,
    input  wire signed [20:0]  i_in,
    input  wire signed [20:0]  q_in,
    input  wire                dv_in,
    input  wire                est_start,
    input  wire                rot_load,
    input  wire signed [23:0]  phase_off,      // 帧起点相位注入 (0 = 旧行为, 不加载)
    output wire                est_done,
    output wire [2:0]          p_hat,
    output wire signed [23:0]  phase_inc,
    output wire signed [23:0]  phase_off_est,  // est 估计的 φ0 (供测试核对)
    output wire signed [20:0]  i_out,
    output wire signed [20:0]  q_out,
    output wire                dv_out
);

    cfo_est #(.W(21), .PW(24)) u_est (
        .clk(clk), .rst_n(rst_n),
        .chip_off(8'd0), .skip_t3(1'b1),   // 本 TB 为外部触发: 触发点在帧起点前
        .i_in(i_in), .q_in(q_in), .dv_in(dv_in), .start(est_start),
        .done(est_done), .p_hat(p_hat), .phase_inc(phase_inc),
        .phase_off(phase_off_est)
    );

    cfo_rot #(.W(21)) u_rot (
        .clk(clk), .rst_n(rst_n),
        .i_in(i_in), .q_in(q_in), .dv_in(dv_in),
        .load(rot_load), .phase_inc(phase_inc), .phase_off(phase_off),
        .i_out(i_out), .q_out(q_out), .dv_out(dv_out)
    );

    // ---- 波形观测 (可选): +define+DUMP_VCD 时生成 VCD 供 GTKWave 查看 ----
    // 用 level 1 (仅本层端口, ~13 个信号), 不去 dump 子模块内部——全层次会到几百 MB。
`ifdef DUMP_VCD
    initial begin
        $dumpfile("cfo_corr.vcd");
        $dumpvars(1, cfo_corr_top);
    end
`endif

endmodule
