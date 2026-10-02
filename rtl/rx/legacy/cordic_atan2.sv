// [状态] 历史/参考实现（2026-10-02 标注）
//   用途：CORDIC atan2（cfo_est 的子模块）
//   现行主链路为 rx_dual 链（rx_dual/rx_frontend/rx_chip_backend/…），本文件不在其上；
//   仍被 tb/cfo_corr 等历史测试引用，勿删；重构对照请以 rtl/rx/ 现行模块为准。
// cordic_atan2.sv —— 向量模式 CORDIC, 16 级迭代求 atan2(y, x)
// ---------------------------------------------------------------------------
// 用途: cfo_est 从"差分相关累加量 acc = Σ d"里取出相位 arg(acc), 再 /16 得到
//       每采样的相位增量 (差分跨 2 个码片 = 16 采样, 故相位增量 = arg/16)。
//
// 为什么用 CORDIC 而不是查表/近似:
//   需要的相位范围到 ±pi (450 kHz 时 arg = 2.83 rad), 小角度近似 Im/Re 不成立;
//   查表要覆盖 2pi 且精度受限。CORDIC 只用移位和加减, 16 级给 ~0.006 度分辨率,
//   面积小且不需要乘法器 —— 符合"低复杂度基带"的项目约束。
//
// 迭代 (向量模式, 把 (x,y) 旋转到 x 轴):
//   y>0: x' = x + (y>>i); y' = y - (x>>i); z += ATAN[i]
//   y<=0: x' = x - (y>>i); y' = y + (x>>i); z -= ATAN[i]
//   注意 x' y' 必须用**旧值**同时算 (非阻塞赋值)。
//
// 输出相位满量程 2*pi, 位宽 PW 有符号: phase = arg * 2^PW / 2pi。
// 只做 atan2, 不输出模长 (本设计用 acc_i^2+acc_q^2 当质量指标, 免开方)。
`timescale 1ns/1ps
module cordic_atan2 #(
    parameter W  = 24,          // 输入位宽 (有符号)
    parameter PW = 24,          // 相位位宽 (满量程 2*pi)
    parameter G  = 12           // 内部增长余量 (防迭代溢出): K=1.65 增长 + 各级中间值
                                //   —— G 太小时 x 会回绕, y 的符号判据随之失效, z 一路乱加
) (
    input  wire                    clk,
    input  wire                    rst_n,
    input  wire                    start,        // 单拍脉冲, 锁存输入并启动
    input  wire signed [W-1:0]     x_in,
    input  wire signed [W-1:0]     y_in,
    output reg                     done,         // 单拍脉冲
    output reg  signed [PW-1:0]    phase        // atan2(y_in, x_in)
);
    localparam IW = W + G;
    localparam NST = 16;

    // atan(2^-i) 定点值 (满量程 2*pi)
    localparam signed [PW-1:0] PH0  = 24'sd2097152;
    localparam signed [PW-1:0] PH1  = 24'sd1238021;
    localparam signed [PW-1:0] PH2  = 24'sd654136;
    localparam signed [PW-1:0] PH3  = 24'sd332050;
    localparam signed [PW-1:0] PH4  = 24'sd166669;
    localparam signed [PW-1:0] PH5  = 24'sd83416;
    localparam signed [PW-1:0] PH6  = 24'sd41718;
    localparam signed [PW-1:0] PH7  = 24'sd20860;
    localparam signed [PW-1:0] PH8  = 24'sd10430;
    localparam signed [PW-1:0] PH9  = 24'sd5215;
    localparam signed [PW-1:0] PH10 = 24'sd2608;
    localparam signed [PW-1:0] PH11 = 24'sd1304;
    localparam signed [PW-1:0] PH12 = 24'sd652;
    localparam signed [PW-1:0] PH13 = 24'sd326;
    localparam signed [PW-1:0] PH14 = 24'sd163;
    localparam signed [PW-1:0] PH15 = 24'sd81;

    function signed [PW-1:0] atan_tab(input integer i);
        case (i)
            0: atan_tab = PH0;   1: atan_tab = PH1;   2: atan_tab = PH2;
            3: atan_tab = PH3;   4: atan_tab = PH4;   5: atan_tab = PH5;
            6: atan_tab = PH6;   7: atan_tab = PH7;   8: atan_tab = PH8;
            9: atan_tab = PH9;  10: atan_tab = PH10; 11: atan_tab = PH11;
           12: atan_tab = PH12; 13: atan_tab = PH13; 14: atan_tab = PH14;
            default: atan_tab = PH15;
        endcase
    endfunction

    reg              busy;
    reg [4:0]        it;
    reg signed [IW-1:0]  x, y;
    reg signed [PW+2:0]  z;

    // 象限预处理: CORDIC 每级只能转 sum(atan(2^-i)) ~ 99.9 度, 直接喂第二/三象限的
    // 向量会转不过去而误加到别处。先把 x<0 的点翻到右半平面, 180 度的偏移记在 z 初值里 ——
    // 预处理后待求角度落在 [-90, +90], 处于 CORDIC 收敛域内。
    // (做此修正前 300 kHz CFO 会被折叠成 277 kHz, 那正是 99.9 度的极限)
    localparam signed [PW+2:0] HALF_TURN = 1 << (PW - 1);   // 180 度
    wire x_neg = x_in[W-1];
    wire signed [IW-1:0] x0 = x_neg ? (-{{(IW-W){x_in[W-1]}}, x_in})
                                    :   {{(IW-W){x_in[W-1]}}, x_in};
    wire signed [IW-1:0] y0 = x_neg ? (-{{(IW-W){y_in[W-1]}}, y_in})
                                    :   {{(IW-W){y_in[W-1]}}, y_in};
    wire signed [PW+2:0] z0 = x_neg ? HALF_TURN : {(PW+3){1'b0}};

    wire signed [IW-1:0] xs = x >>> it[3:0];
    wire signed [IW-1:0] ys = y >>> it[3:0];
    wire y_nonneg = ~y[IW-1];
    wire signed [IW-1:0]   x_nxt = y_nonneg ? (x + ys) : (x - ys);
    wire signed [IW-1:0]   y_nxt = y_nonneg ? (y - xs) : (y + xs);
    wire signed [PW-1:0]   phi = atan_tab(it[3:0]);
    wire signed [PW+2:0]   phi_e = {{3{phi[PW-1]}}, phi};
    wire signed [PW+2:0]   z_nxt = y_nonneg ? (z + phi_e) : (z - phi_e);

    always @(posedge clk) begin
        if (!rst_n) begin
            busy  <= 1'b0;
            it    <= 5'd0;
            x     <= {IW{1'b0}};
            y     <= {IW{1'b0}};
            z     <= {(PW+3){1'b0}};
            done  <= 1'b0;
            phase <= {PW{1'b0}};
        end else begin
            done <= 1'b0;
            if (start) begin
                // 输入符号扩展到内部位宽 + 象限预处理
                x    <= x0;
                y    <= y0;
                z    <= z0;
                it   <= 5'd0;
                busy <= 1'b1;
            end else if (busy) begin
                x <= x_nxt;
                y <= y_nxt;
                z <= z_nxt;
                if (it == NST - 1) begin
                    busy  <= 1'b0;
                    done  <= 1'b1;
                    phase <= z_nxt[PW-1:0];
                end else begin
                    it <= it + 5'd1;
                end
            end
        end
    end
endmodule
