// adc_gear_ctrl.sv v3 —— ADC 档位决策（ref: model/algo/adc_gear.py; 决策/执行解耦）
// 输出为 set_* 序列（接 adc_quant_if）, **当前档位由 cur_gear 反馈**（来自执行侧）——
// 决策层不直连执行器; set_low_ok 即 permit（b 方案可将 set_req 移交消费侧）。
// 不对称原则（docs/21 §14.2）:
//   ① 降档仅由最高精度（16）状态发起——低档禁链式自降;
//   ② 升档用失败信号（无条件可信）——连续 K_FAIL 帧失败 → 阶梯升档; TTL 到期回顶;
//   ③ 监督事件（sup_force 电平, 与执行侧 force_full 同源）: 清内部计数（与 ref 一致）,
//      档位由执行侧直接强制——监督层独立于本决策层。
// 档位编码（v1, 3bit）: 0=16b（全精度, 复位值）, 1=12b（ADC 原生）, 2=8b, 3=4b
//   （数值↑=精度↓; 阶梯升档 = 编码-1）。
// 事件约定: 每帧边界 frame_ev 单拍, 同拍携带 snr_valid/snr_est/fcs_ok/sup_force。
`timescale 1ns/1ps
module adc_gear_ctrl #(
    parameter [2:0]  G12       = 3'd1,    // 12bit 档编码（ADC 原生）
    parameter [2:0]  G8        = 3'd2,
    parameter [2:0]  G4        = 3'd3,
    parameter [7:0]  TH_DOWN_12= 8'd0,    // 16→12 降档阈值（0=恒允许: ADC 数据原生精度）
    parameter [7:0]  TH_DOWN_8 = 8'd18,
    parameter [7:0]  TH_DOWN_4 = 8'd20,
    parameter [15:0] K_FAIL    = 16'd3,
    parameter [15:0] TTL       = 16'd200
) (
    input  wire       clk,
    input  wire       rst_n,
    input  wire       frame_ev,      // 帧边界（单拍）
    input  wire       snr_valid,     // 高精度评估有效（仅 cur_gear==16b 时上游应给出）
    input  wire [7:0] snr_est,       // 评估值（无符号 dB）
    input  wire       fcs_ok,        // 帧结果（失败类信号）
    input  wire       sup_force,     // 监督广播（与执行侧 force_full 同源; 电平）
    input  wire [2:0] cur_gear,      // 生效档反馈（来自 adc_quant_if.adc_gear）
    // —— set 序列（→ adc_quant_if）——
    output reg  [2:0] set_gear,
    output reg        set_req,       // 单拍
    output reg        set_low_ok     // permit（降档许可, 与请求同拍; b 方案可独立消费）
);
    localparam [2:0] G16 = 3'd0;

    reg [15:0] fail_cnt;
    reg [15:0] ttl_cnt;
    wire [15:0] fail_next = fcs_ok ? 16'd0 : fail_cnt + 16'd1;
    wire [15:0] ttl_next  = ttl_cnt + 16'd1;

    always @(posedge clk) begin
        if (!rst_n) begin
            set_gear  <= G16;
            set_req   <= 1'b0;
            set_low_ok<= 1'b0;
            fail_cnt  <= 16'd0;
            ttl_cnt   <= 16'd0;
        end else begin
            set_req <= 1'b0;                       // 单拍脉冲
            if (frame_ev) begin
                fail_cnt <= fail_next;             // 失败计数无条件更新
                if (sup_force) begin
                    // 监督事件: 清计数（档位由执行侧强制; 与 ref 的 force 分支一致）
                    fail_cnt <= 16'd0;
                    ttl_cnt  <= 16'd0;
                end else if (cur_gear == G16) begin
                    // 最高档评估 → 机会主义降档（带 permit）
                    ttl_cnt <= 16'd0;
                    if (snr_valid && (snr_est > TH_DOWN_4)) begin
                        set_gear <= G4; set_req <= 1'b1; set_low_ok <= 1'b1;
                    end else if (snr_valid && (snr_est > TH_DOWN_8)) begin
                        set_gear <= G8; set_req <= 1'b1; set_low_ok <= 1'b1;
                    end else if (snr_valid && (snr_est > TH_DOWN_12)) begin
                        set_gear <= G12; set_req <= 1'b1; set_low_ok <= 1'b1;
                    end else set_low_ok <= 1'b0;
                end else begin
                    if (fail_next >= K_FAIL) begin         // 离开①: 失败触发（阶梯升档=编码-1）
                        set_gear <= cur_gear - 3'd1;
                        set_req <= 1'b1; set_low_ok <= 1'b0;   // 升档无需 permit
                        fail_cnt <= 16'd0;
                        ttl_cnt  <= 16'd0;
                    end else if (ttl_next >= TTL) begin    // 离开②: TTL 回顶
                        set_gear <= G16;
                        set_req <= 1'b1; set_low_ok <= 1'b0;
                        ttl_cnt  <= 16'd0;
                    end else begin
                        ttl_cnt <= ttl_next;
                    end
                end
            end
        end
    end
endmodule
