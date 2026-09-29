// 工具链冒烟测试：8 位计数器
`timescale 1ns/1ps
module smoke_tb (
    input  logic       clk,
    input  logic       rst_n,
    output logic [7:0] count
);
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) count <= 8'd0;
    else count <= count + 8'd1;
  end
endmodule
