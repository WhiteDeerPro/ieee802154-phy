// half_sine_fir.sv —— 8 抽头半正弦成形 FIR, 系数 Q2.6
// h = [12,36,53,63,63,53,36,12] (= round(64·sin(π(n+0.5)/8)))
// TX 成形与 RX 匹配滤波共用 (h 对称, 卷积即匹配)。
// 定点约定见 docs/05: y = acc 截低 Y_W bit。TX (x∈{±1,0}) 无截断损失;
// RX 用 Y_W=ACC_W 全精度。
// 与 model/phy_802154.py fixed_half_sine / modulate_oqpsk_fixed bit-true。
`timescale 1ns/1ps
module half_sine_fir #(
    parameter X_W = 12,              // 输入位宽
    parameter Y_W = 12               // 输出位宽 (<= ACC_W; ACC_W = X_W+9)
) (
    input  wire                      clk,
    input  wire                      rst_n,
    input  wire signed [X_W-1:0]     x,
    input  wire                      dv_in,
    output reg  signed [Y_W-1:0]     y,
    output reg                       dv_out
);
    localparam ACC_W = X_W + 9;
    localparam signed [7:0] C0 = 8'd12;
    localparam signed [7:0] C1 = 8'd36;
    localparam signed [7:0] C2 = 8'd53;
    localparam signed [7:0] C3 = 8'd63;
    // 系数对称: C4=C3, C5=C2, C6=C1, C7=C0

    reg signed [X_W-1:0] pipe [0:7];
    reg signed [ACC_W-1:0] acc;
    integer k;

    always @(posedge clk) begin
        if (!rst_n) begin
            for (k = 0; k < 8; k = k + 1) pipe[k] <= {X_W{1'b0}};
            y      <= {Y_W{1'b0}};
            dv_out <= 1'b0;
            acc    <= {ACC_W{1'b0}};
        end else begin
            for (k = 7; k > 0; k = k - 1) pipe[k] <= pipe[k-1];
            pipe[0] <= dv_in ? x : {X_W{1'b0}};
            acc = (pipe[0] * C0) + (pipe[1] * C1) + (pipe[2] * C2)
                + (pipe[3] * C3) + (pipe[4] * C3) + (pipe[5] * C2)
                + (pipe[6] * C1) + (pipe[7] * C0);
            y      <= acc[Y_W-1:0];
            dv_out <= dv_in;
        end
    end
endmodule
