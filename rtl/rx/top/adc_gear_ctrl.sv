// adc_gear_ctrl.sv —— ADC 档位控制器（gear shifting; ref: model/algo/adc_gear.py, bit-true）
// 不对称原则（docs/21 §14.2）:
//   ① 降档仅由 12bit（高精度）状态发起 —— 低档禁链式自降;
//   ② 升档用失败信号（无条件可信）—— 连续 K_FAIL 帧失败 → 阶梯升档;
//   ③ TTL 复查 = 无条件回顶 —— 低档为限时租约; 回顶后由常规评估重新降档。
// 事件约定（与 ref 一致）: 每帧边界一个 frame_ev 脉冲，同拍携带 snr_valid/snr_est/fcs_ok。
// gear_req: 2'b00=12bit, 2'b01=8bit, 2'b10=4bit（请求值, 切换由 ADC/上层执行）。
`timescale 1ns/1ps
module adc_gear_ctrl #(
    parameter [7:0]  TH_DOWN_8 = 8'd18,   // 12→8 降档阈值（dB）
    parameter [7:0]  TH_DOWN_4 = 8'd20,   // 12→4 直达阈值（dB）
    parameter [15:0] K_FAIL    = 16'd3,   // 连续失败 K 帧 → 升档
    parameter [15:0] TTL       = 16'd200  // 低档许可期（帧）
) (
    input  wire       clk,
    input  wire       rst_n,
    input  wire       frame_ev,      // 帧边界（单拍）
    input  wire       snr_valid,     // 高精度评估有效（仅 gear==12 时上游应给出）
    input  wire [7:0] snr_est,       // 评估值（无符号 dB）
    input  wire       fcs_ok,        // 帧结果（失败类信号）
    output reg  [1:0] gear_req
);
    localparam [1:0] G12 = 2'd0, G8 = 2'd1, G4 = 2'd2;

    reg [15:0] fail_cnt;
    reg [15:0] ttl_cnt;

    // 组合"下一值"——与 ref 的"更新后判定"时序对齐
    wire [15:0] fail_next = fcs_ok ? 16'd0 : fail_cnt + 16'd1;
    wire [15:0] ttl_next  = ttl_cnt + 16'd1;

    always @(posedge clk) begin
        if (!rst_n) begin
            gear_req <= G12;
            fail_cnt <= 16'd0;
            ttl_cnt  <= 16'd0;
        end else if (frame_ev) begin
            fail_cnt <= fail_next;                    // 失败计数无条件更新

            if (gear_req == G12) begin
                // 高精度发起降档（禁链式: 仅 12 档评估）
                ttl_cnt <= 16'd0;
                if (snr_valid && (snr_est > TH_DOWN_4))      gear_req <= G4;
                else if (snr_valid && (snr_est > TH_DOWN_8)) gear_req <= G8;
            end else begin
                if (fail_next >= K_FAIL) begin        // 离开①: 失败触发（阶梯升档）
                    gear_req <= (gear_req == G4) ? G8 : G12;
                    fail_cnt <= 16'd0;
                    ttl_cnt  <= 16'd0;
                end else if (ttl_next >= TTL) begin   // 离开②: TTL 到期 → 无条件回顶
                    gear_req <= G12;
                    ttl_cnt  <= 16'd0;
                end else begin
                    ttl_cnt <= ttl_next;
                end
            end
        end
    end
endmodule
