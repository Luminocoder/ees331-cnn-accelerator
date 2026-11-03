`timescale 1ns / 1ps

// ============================================================================
// 模块: controller (controller_fsm) — 任务 B5 交付物
// 版本: v1.0
//
// 主控制器 FSM，按冻结的状态转换图调度六个层：
//   S_IDLE → S_CONV1 → S_POOL1 → S_CONV2 → S_POOL2 → S_FC1 → S_FC2 → S_DONE
//
// 握手约定（冻结于 accelerator_ports.md v2 §4）：
//   - {eng}_start : 单周期脉冲，进入对应状态时产生
//   - {eng}_done  : 电平，engine 完成后拉高并保持
//   - accel_done  : 保持高电平直到下一次 accel_start（冻结约定）
//
// 状态编码与 B 的状态转换图 v1.0 一致（S_CONV1=0001 ... S_DONE=0111）
// ============================================================================

module controller #(
    // ADR-0002 决定 1/3：中间层写回右移位数（A 标定值，2026-08-24 签字）
    parameter [3:0] SHIFT_CONV1 = 4'd9,
    parameter [3:0] SHIFT_CONV2 = 4'd9,
    parameter [3:0] SHIFT_FC1   = 4'd10
) (
    input  wire        clk,
    input  wire        rst_n,

    // ---- 与 axi_if 的握手 ----
    input  wire        accel_start,      // 单周期启动脉冲
    input  wire        accel_soft_rst,   // 软复位
    output wire        accel_busy,
    output wire        accel_done,
    output wire        accel_error,

    // ---- 与 engine 的握手 ----
    output reg         conv_start,
    output reg         pool_start,
    output reg         fc_start,
    input  wire        conv_done,
    input  wire        pool_done,
    input  wire        fc_done,

    // ---- 层配置 / 调试 ----
    output wire [31:0] layer_cfg,        // 低 3 位 = 层 ID（0=Conv1 ... 5=FC2）
    output wire [31:0] layer_status,     // 当前状态编号（调试用）

    output wire        irq_done
);

    // 状态编码（与状态转换图一致）
    localparam [3:0] S_IDLE  = 4'b0000,
                     S_CONV1 = 4'b0001,
                     S_POOL1 = 4'b0010,
                     S_CONV2 = 4'b0011,
                     S_POOL2 = 4'b0100,
                     S_FC1   = 4'b0101,
                     S_FC2   = 4'b0110,
                     S_DONE  = 4'b0111;

    reg [3:0] state, next_state;

    always @(posedge clk) begin
        if (!rst_n || accel_soft_rst)
            state <= S_IDLE;
        else
            state <= next_state;
    end

    // done 上升沿检测：引擎 done 是保持电平（直到下一次 start），
    // 若用电平判断完成，同一引擎连续两层（Conv1/Conv2、FC1/FC2）时，
    // 上一层残留的高电平会让本层被跳过。故只认 done 的 0→1 边沿。
    reg conv_done_d1, pool_done_d1, fc_done_d1;
    always @(posedge clk) begin
        if (!rst_n || accel_soft_rst) begin
            conv_done_d1 <= 1'b0;
            pool_done_d1 <= 1'b0;
            fc_done_d1   <= 1'b0;
        end else begin
            conv_done_d1 <= conv_done;
            pool_done_d1 <= pool_done;
            fc_done_d1   <= fc_done;
        end
    end
    wire conv_rise = conv_done && !conv_done_d1;
    wire pool_rise = pool_done && !pool_done_d1;
    wire fc_rise   = fc_done   && !fc_done_d1;

    // 状态转移
    always @(*) begin
        next_state = state;
        case (state)
            S_IDLE:  if (accel_start) next_state = S_CONV1;
            S_CONV1: if (conv_rise)   next_state = S_POOL1;
            S_POOL1: if (pool_rise)   next_state = S_CONV2;
            S_CONV2: if (conv_rise)   next_state = S_POOL2;
            S_POOL2: if (pool_rise)   next_state = S_FC1;
            S_FC1:   if (fc_rise)     next_state = S_FC2;
            S_FC2:   if (fc_rise)     next_state = S_DONE;
            S_DONE:                   next_state = S_IDLE;
            default:                  next_state = S_IDLE;
        endcase
    end

    // engine 启动脉冲：仅在"进入"对应状态的那一个周期为高
    // （状态自保持时 next_state==state，必须排除，否则脉冲持续拉高）
    always @(posedge clk) begin
        if (!rst_n || accel_soft_rst) begin
            conv_start <= 1'b0;
            pool_start <= 1'b0;
            fc_start   <= 1'b0;
        end else begin
            conv_start <= ((next_state == S_CONV1) && (state != S_CONV1)) ||
                          ((next_state == S_CONV2) && (state != S_CONV2));
            pool_start <= ((next_state == S_POOL1) && (state != S_POOL1)) ||
                          ((next_state == S_POOL2) && (state != S_POOL2));
            fc_start   <= ((next_state == S_FC1)   && (state != S_FC1))   ||
                          ((next_state == S_FC2)   && (state != S_FC2));
        end
    end

    // DONE 保持：拉高后直到下一次 START（只在空闲时接受新 START）
    reg done_hold;
    always @(posedge clk) begin
        if (!rst_n || accel_soft_rst)
            done_hold <= 1'b0;
        else if (accel_start && state == S_IDLE)
            done_hold <= 1'b0;
        else if (state == S_DONE)
            done_hold <= 1'b1;
    end

    assign accel_done  = done_hold;
    assign irq_done    = done_hold;
    assign accel_busy  = (state != S_IDLE) && (state != S_DONE);
    assign accel_error = 1'b0;   // 保留：错误源（非法配置/超时）Phase 3 补

    // 层 ID
    reg [2:0] layer_id;
    always @(*) begin
        case (state)
            S_CONV1: layer_id = 3'd0;
            S_POOL1: layer_id = 3'd1;
            S_CONV2: layer_id = 3'd2;
            S_POOL2: layer_id = 3'd3;
            S_FC1:   layer_id = 3'd4;
            S_FC2:   layer_id = 3'd5;
            default: layer_id = 3'd0;
        endcase
    end

    // 写回右移位数（ADR-0002）：pool 层与 FC2 不移位
    reg [3:0] layer_shift;
    always @(*) begin
        case (state)
            S_CONV1: layer_shift = SHIFT_CONV1;
            S_CONV2: layer_shift = SHIFT_CONV2;
            S_FC1:   layer_shift = SHIFT_FC1;
            default: layer_shift = 4'd0;
        endcase
    end

    // layer_cfg 位段（冻结，见 accelerator_ports.md）：
    //   [2:0]  layer_id
    //   [6:3]  layer_shift (>>>S)
    assign layer_cfg    = {24'b0, layer_shift, layer_id};
    assign layer_status = {28'b0, state};

endmodule
