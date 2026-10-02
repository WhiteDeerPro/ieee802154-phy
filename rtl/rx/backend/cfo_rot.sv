// cfo_rot.sv —— 整帧消旋 (CFO 修正的数据通路)
// ---------------------------------------------------------------------------
// 输入 cfo_est 给出的每采样相位增量 phase_inc, 对 MF 输出流做逐采样复旋转:
//     out[n] = in[n] · exp(-j·n·phase_inc)
// 相位零点在 load 之后的第一个有效采样 (n=0, 相位 0), 与模型侧
// model/experiments/run_cfo_fix.py 的 derotate() 约定一致 —— 那里是
// mf * exp(-2j*pi*f_hat*n/FS), n 从 0 起算, 所以这里也必须从 0 起算。
//
// 实现选择: 相位累加器 + cos/sin 查找表, 而不是递归复数旋转。
//   递归法 w[n] = w[n-1]·exp(-j·inc) 每采样要两次复数乘 (8 个实数乘);
//   查表法只有一次复数乘 (4 个实数乘) 加一个加法器。
//   累加器保持 24 位精度 (累加误差不累积到查表值里), 只用高 8 位索引 LUT,
//   角度量化 2*pi/256 = 1.4 度 —— 对非相干 |·| 检测完全无影响, 也远小于
//   系统要修的 96 kHz 量级偏差。
`timescale 1ns/1ps
module cfo_rot #(
    parameter W = 21            // 与 rx_matched_filter 输出同宽
) (
    input  wire                    clk,
    input  wire                    rst_n,
    input  wire signed [W-1:0]     i_in,
    input  wire signed [W-1:0]     q_in,
    input  wire                    dv_in,
    input  wire                    load,        // 单拍脉冲: 相位累加器归零 (帧起点)
    input  wire signed [23:0]      phase_inc,   // 每采样相位增量 (满量程 2*pi)
    input  wire signed [23:0]      phase_off,   // 帧起点相位 φ0: load 时加载, 使消旋后前导落回实轴
    output reg  signed [W-1:0]     i_out,
    output reg  signed [W-1:0]     q_out,
    output reg                     dv_out
);
    `include "rot_lut.svh"

    reg [23:0] pacc;                            // 无符号相位累加器 (自然回绕)

    wire [7:0] idx = pacc[23:16];
    wire signed [15:0] cs = COS_LUT[idx];
    wire signed [15:0] sn = SIN_LUT[idx];

    // 复乘: (i + jq)·(cos - j sin)
    wire signed [W+16:0] ro_i = i_in * cs + q_in * sn;
    wire signed [W+16:0] ro_q = q_in * cs - i_in * sn;

    always @(posedge clk) begin
        if (!rst_n) begin
            pacc   <= 24'd0;
            i_out  <= {W{1'b0}};
            q_out  <= {W{1'b0}};
            dv_out <= 1'b0;
        end else begin
            dv_out <= dv_in;
            if (dv_in) begin
                // load 与数据同拍时, 本拍数据用 pacc=0 旋转 (即 n=0), 不吞采样
                i_out <= (ro_i >>> 15);
                q_out <= (ro_q >>> 15);
                // LUT 存的已是 exp(-j*phi) 的 (cos, -sin), 所以累加器正向加,
                // 复乘即得 exp(-j*n*phase_inc), 与模型 derotate() 同号。
                // load 时加载 -φ0 (而不是 0): 补偿"消旋相位参考点"处的信号相位,
                // 使消旋后前导码片落回实轴 (无共轭积判据 r=Σ(I·I'-Q·Q') 需要这个)。
                pacc  <= load ? (24'd0 - phase_off[23:0]) : (pacc + phase_inc[23:0]);
            end

            // load 单独打拍 (无数据时) 也清零, 便于测试在数据前预置相位零点
            if (load && !dv_in) pacc <= 24'd0;
        end
    end
endmodule
