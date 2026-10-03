// adc_quant_if.sv —— 量化动态化接口（档位请求 + 握手 + 权限闸门）
// 证据/决策解耦架构的"执行侧"（docs/21 §15）: 无论档位决策从哪来（基带自动 / 外部代理）,
// 切换都必须经过此闸门——权限矩阵:
//   · 升档（→更高精度）: 恒允许（安全方向）
//   · 降档（→更低精度）: 需 set_low_ok（机会主义许可, 来自决策层）且不越 GEAR_MIN 下限
//   · set_force_full（电平, 外部代理）: 无条件强制全态（12b）, 期间拒绝降档
// 握手: set_* → 权限检查 → adc_gear 切换 + adc_req 置位 → adc_ack → adc_req 清零。
//       adc_req 忙时新请求忽略（简单化; 决策层约定不忙时再发）。
// 档位编码: 2'b00=12bit, 2'b01=8bit, 2'b10=4bit（数值越大 = 精度越低）。
`timescale 1ns/1ps
module adc_quant_if #(
    parameter [1:0] GEAR_MIN = 2'd2    // 允许到达的最低档（下限边界; 2=4bit）
) (
    input  wire       clk,
    input  wire       rst_n,
    // —— 决策/代理侧（设置请求）——
    input  wire [1:0] set_gear,
    input  wire       set_req,         // 单拍
    input  wire       set_low_ok,      // permit: 允许降档（机会主义许可）
    input  wire       set_force_full,  // 外部代理: 电平, 强制全态 + 拒降
    // —— 面向 ADC/RF ——
    output reg  [1:0] adc_gear,        // 当前档位（生效）
    output reg        adc_req,         // 切换请求（保持至 ack）
    input  wire       adc_ack,
    // —— 观测 ——
    output reg        denied           // 单拍: 一次被权限拒绝
);
    wire lower     = (set_gear > adc_gear);     // 目标精度更低
    wire higher    = (set_gear < adc_gear);
    wire below_min = (set_gear > GEAR_MIN);     // 越过允许下限
    wire force_go  = set_force_full && (adc_gear != 2'd0) && !adc_req;
    wire allow     = (higher | (lower & set_low_ok & !below_min))
                     && !(set_force_full && lower);   // force 期间禁降

    always @(posedge clk) begin
        if (!rst_n) begin
            adc_gear <= 2'd0;
            adc_req  <= 1'b0;
            denied   <= 1'b0;
        end else begin
            denied <= 1'b0;
            if (adc_req && adc_ack) begin
                adc_req <= 1'b0;                     // 握手完成
            end else if (force_go) begin
                adc_gear <= 2'd0;                    // 外部代理: 强制全态
                adc_req  <= 1'b1;
            end else if (set_req && !adc_req && (set_gear != adc_gear)) begin
                if (allow) begin
                    adc_gear <= set_gear;
                    adc_req  <= 1'b1;
                end else begin
                    denied <= 1'b1;                  // 权限拒绝（观测）
                end
            end
        end
    end
endmodule
