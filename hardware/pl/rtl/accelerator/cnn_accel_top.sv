`timescale 1ns / 1ps

// ============================================================================
// cnn_accel_top — 加速器顶层（B7，接口修正版）
//
// 接口依据 accelerator_ports.md v2：简单信号，AXI-Lite 解析在外部 axi_if（任务 C）。
// 内部接线（层参数 MUX / 引擎↔BRAM 复用 MUX / FC2 结果锁存）沿用 B 的交付，已审。
//
// 修正点（R6）：
//   1. 顶层端口由 s_axi_* 改为 v2 简单信号（accel_start/busy/done/result_0~9/...）
//   2. 删除内部 axi_slave_if 占位
//   3. BRAM 端口 A 提升为顶层端口，供 SoC 层桥（C）写图片/权重
//   4. weight_bram 增加 INIT 参数透传，供集成仿真直接 $readmemh 加载真实权重
// ============================================================================

module cnn_accel_top #(
    parameter IMG_WIDTH    = 28,
    parameter IMG_HEIGHT   = 28,
    parameter NUM_CLASSES  = 10,
    parameter DATA_WIDTH   = 8,      // INT8
    parameter ACCUM_WIDTH  = 32,     // INT32 累加
    // weight_bram 初始化参数透传（集成仿真用，综合时 INIT_EN=0）
    parameter INIT_EN      = 0,
    parameter [8*255:0] W_CONV1_FILE = "",
    parameter [8*255:0] W_CONV2_FILE = "",
    parameter [8*255:0] W_FC1_FILE   = "",
    parameter [8*255:0] W_FC2_FILE   = "",
    parameter [8*255:0] W_BIAS_FILE  = ""
) (
    // ========== 时钟与复位 ==========
    input  wire                     clk,
    input  wire                     rst_n,

    // ========== 与 axi_if 的握手（控制） ==========
    input  wire                     accel_start,      // 启动一次推理（边沿脉冲）
    input  wire                     accel_soft_rst,   // 软复位（不清 BRAM）
    output wire                     accel_busy,
    output wire                     accel_done,       // 保持到下次 START
    output wire                     accel_error,

    // ========== 推理结果（去 axi_if 的 RESULT 寄存器） ==========
    output wire [ACCUM_WIDTH-1:0]   result_0,
    output wire [ACCUM_WIDTH-1:0]   result_1,
    output wire [ACCUM_WIDTH-1:0]   result_2,
    output wire [ACCUM_WIDTH-1:0]   result_3,
    output wire [ACCUM_WIDTH-1:0]   result_4,
    output wire [ACCUM_WIDTH-1:0]   result_5,
    output wire [ACCUM_WIDTH-1:0]   result_6,
    output wire [ACCUM_WIDTH-1:0]   result_7,
    output wire [ACCUM_WIDTH-1:0]   result_8,
    output wire [ACCUM_WIDTH-1:0]   result_9,

    // ========== 调试 ==========
    output wire [31:0]              layer_status,

    // ========== 中断（Phase 4 用） ==========
    output wire                     irq_done,

    // ========== ARM 写 BRAM 通路（端口 A，SoC 层桥驱动） ==========
    input  wire                     fm_a_we,
    input  wire [13:0]              fm_a_addr,
    input  wire [7:0]               fm_a_wdata,
    output wire [7:0]               fm_a_rdata,
    input  wire                     w_a_we,
    input  wire [15:0]              w_a_addr,
    input  wire [7:0]               w_a_wdata,
    output wire [7:0]               w_a_rdata
);

    // ========== 内部信号 ==========
    logic [31:0] layer_status_i;

    logic        conv_start, pool_start, fc_start;
    logic        conv_done, pool_done, fc_done;
    logic [31:0] layer_cfg;
    logic [2:0]  layer_id;
    assign layer_id = layer_cfg[2:0];

    // 层参数（由 layer_id MUX 产生）
    logic [7:0]  c_ifm_size, c_ofm_size;
    logic [4:0]  c_ifm_ch, c_ofm_ch;   // 5 位以容纳 16 通道
    logic [13:0] c_ifm_base, c_ofm_base;
    logic [15:0] c_w_base, c_bias_base;
    logic [3:0]  c_S_layer;
    logic        c_is_fc2;
    logic [15:0] c_ifm_len, c_ofm_len;

    // 引擎到 BRAM 的端口
    logic        conv_fm_we, pool_fm_we, fc_fm_we;
    logic [13:0] conv_fm_addr, pool_fm_addr, fc_fm_addr;
    logic [7:0]  conv_fm_wdata, pool_fm_wdata, fc_fm_wdata;
    logic [15:0] conv_w_addr, fc_w_addr;
    logic        fc_out_we;
    logic [3:0]  fc_out_addr;
    logic [31:0] fc_out_wdata;

    // BRAM 端口 B（引擎共用，按 layer_id MUX）
    logic        fm_b_we;
    logic [13:0] fm_b_addr;
    logic [7:0]  fm_b_wdata, fm_b_rdata;
    logic [15:0] w_b_addr;
    logic [7:0]  w_b_rdata;

    // FC2 结果寄存器
    logic [31:0] result_regs [0:9];

    // ========== 层参数 MUX（照 R5 参数表） ==========
    always_comb begin
        case (layer_id)
            3'd0: begin // Conv1
                c_ifm_size = 28; c_ofm_size = 28;
                c_ifm_ch = 1;   c_ofm_ch = 8;
                c_ifm_base = 14'h0000; c_ofm_base = 14'h2000;
                c_w_base = 0;    c_bias_base = 52040;
                c_S_layer = 9;   c_is_fc2 = 0;
                c_ifm_len = 0;   c_ofm_len = 0;
            end
            3'd1: begin // Pool1
                c_ifm_size = 28; c_ofm_size = 14;
                c_ifm_ch = 8;    c_ofm_ch = 8;
                c_ifm_base = 14'h2000; c_ofm_base = 14'h0000;
                c_w_base = 0;    c_bias_base = 0;
                c_S_layer = 0;   c_is_fc2 = 0;
                c_ifm_len = 0;   c_ofm_len = 0;
            end
            3'd2: begin // Conv2
                c_ifm_size = 14; c_ofm_size = 14;
                c_ifm_ch = 8;    c_ofm_ch = 16;
                c_ifm_base = 14'h0000; c_ofm_base = 14'h2000;
                c_w_base = 72;   c_bias_base = 52072;
                c_S_layer = 9;   c_is_fc2 = 0;
                c_ifm_len = 0;   c_ofm_len = 0;
            end
            3'd3: begin // Pool2
                c_ifm_size = 14; c_ofm_size = 7;
                c_ifm_ch = 16;   c_ofm_ch = 16;
                c_ifm_base = 14'h2000; c_ofm_base = 14'h0000;
                c_w_base = 0;    c_bias_base = 0;
                c_S_layer = 0;   c_is_fc2 = 0;
                c_ifm_len = 0;   c_ofm_len = 0;
            end
            3'd4: begin // FC1
                c_ifm_size = 0; c_ofm_size = 0;
                c_ifm_ch = 0;   c_ofm_ch = 0;
                c_ifm_base = 14'h0000; c_ofm_base = 14'h2000;
                c_w_base = 1224; c_bias_base = 52136;
                c_S_layer = 10; c_is_fc2 = 0;
                c_ifm_len = 784; c_ofm_len = 64;
            end
            3'd5: begin // FC2
                c_ifm_size = 0; c_ofm_size = 0;
                c_ifm_ch = 0;   c_ofm_ch = 0;
                c_ifm_base = 14'h2000; c_ofm_base = 14'h0000;
                c_w_base = 51400; c_bias_base = 52392;
                c_S_layer = 0;   c_is_fc2 = 1;
                c_ifm_len = 64;  c_ofm_len = 10;
            end
            default: begin
                c_ifm_size = 0; c_ofm_size = 0;
                c_ifm_ch = 0;   c_ofm_ch = 0;
                c_ifm_base = 0; c_ofm_base = 0;
                c_w_base = 0;   c_bias_base = 0;
                c_S_layer = 0;  c_is_fc2 = 0;
                c_ifm_len = 0;  c_ofm_len = 0;
            end
        endcase
    end

    // ========== 引擎 → BRAM 端口 MUX ==========
    always_comb begin
        if (layer_id == 3'd0 || layer_id == 3'd2) begin // Conv
            fm_b_we    = conv_fm_we;
            fm_b_addr  = conv_fm_addr;
            fm_b_wdata = conv_fm_wdata;
            w_b_addr   = conv_w_addr;
        end else if (layer_id == 3'd1 || layer_id == 3'd3) begin // Pool
            fm_b_we    = pool_fm_we;
            fm_b_addr  = pool_fm_addr;
            fm_b_wdata = pool_fm_wdata;
            w_b_addr   = 16'h0;
        end else begin // FC
            fm_b_we    = fc_fm_we;
            fm_b_addr  = fc_fm_addr;
            fm_b_wdata = fc_fm_wdata;
            w_b_addr   = fc_w_addr;
        end
    end

    // ========== FC2 结果锁存 ==========
    always_ff @(posedge clk) begin
        if (!rst_n || accel_soft_rst) begin
            for (int i = 0; i < 10; i++) result_regs[i] <= 0;
        end else if (fc_out_we) begin
            result_regs[fc_out_addr] <= fc_out_wdata;
        end
    end

    // 结果输出
    assign result_0 = result_regs[0];
    assign result_1 = result_regs[1];
    assign result_2 = result_regs[2];
    assign result_3 = result_regs[3];
    assign result_4 = result_regs[4];
    assign result_5 = result_regs[5];
    assign result_6 = result_regs[6];
    assign result_7 = result_regs[7];
    assign result_8 = result_regs[8];
    assign result_9 = result_regs[9];

    // ========== 子模块例化 ==========

    // 1. Controller FSM
    controller u_ctrl (
        .clk            (clk),
        .rst_n          (rst_n),
        .accel_start    (accel_start),
        .accel_soft_rst (accel_soft_rst),
        .accel_busy     (accel_busy),
        .accel_done     (accel_done),
        .accel_error    (accel_error),
        .conv_start     (conv_start),
        .pool_start     (pool_start),
        .fc_start       (fc_start),
        .conv_done      (conv_done),
        .pool_done      (pool_done),
        .fc_done        (fc_done),
        .layer_cfg      (layer_cfg),
        .layer_status   (layer_status_i),
        .irq_done       (irq_done)
    );
    assign layer_status = layer_status_i;

    // 2. Conv Engine
    conv_engine u_conv (
        .clk            (clk),
        .rst_n          (rst_n),
        .conv_start     (conv_start),
        .conv_done      (conv_done),
        .fm_b_we        (conv_fm_we),
        .fm_b_addr      (conv_fm_addr),
        .fm_b_wdata     (conv_fm_wdata),
        .fm_b_rdata     (fm_b_rdata),
        .w_b_addr       (conv_w_addr),
        .w_b_rdata      (w_b_rdata),
        .ifm_size       (c_ifm_size),
        .ifm_ch         (c_ifm_ch),
        .ofm_ch         (c_ofm_ch),
        .ifm_base_addr  (c_ifm_base),
        .ofm_base_addr  (c_ofm_base),
        .w_base_addr    (c_w_base),
        .bias_base_addr (c_bias_base),
        .S_layer        (c_S_layer)
    );

    // 3. Pool Engine
    pool_engine u_pool (
        .clk            (clk),
        .rst_n          (rst_n),
        .pool_start     (pool_start),
        .pool_done      (pool_done),
        .fm_b_we        (pool_fm_we),
        .fm_b_addr      (pool_fm_addr),
        .fm_b_wdata     (pool_fm_wdata),
        .fm_b_rdata     (fm_b_rdata),
        .ifm_size       (c_ifm_size),
        .ifm_ch         (c_ifm_ch),
        .ifm_base_addr  (c_ifm_base),
        .ofm_base_addr  (c_ofm_base)
    );

    // 4. FC Engine
    fc_engine u_fc (
        .clk            (clk),
        .rst_n          (rst_n),
        .fc_start       (fc_start),
        .fc_done        (fc_done),
        .fm_b_we        (fc_fm_we),
        .fm_b_addr      (fc_fm_addr),
        .fm_b_wdata     (fc_fm_wdata),
        .fm_b_rdata     (fm_b_rdata),
        .w_b_addr       (fc_w_addr),
        .w_b_rdata      (w_b_rdata),
        .out_we         (fc_out_we),
        .out_addr       (fc_out_addr),
        .out_wdata      (fc_out_wdata),
        .ifm_len        (c_ifm_len),
        .ofm_len        (c_ofm_len),
        .ifm_base_addr  (c_ifm_base),
        .ofm_base_addr  (c_ofm_base),
        .w_base_addr    (c_w_base),
        .bias_base_addr (c_bias_base),
        .S_layer        (c_S_layer),
        .is_fc2         (c_is_fc2)
    );

    // 5. Feature Map BRAM (ping-pong)
    fm_bram #( .ADDR_WIDTH(14) ) u_fm_bram (
        .clk     (clk),
        .a_we    (fm_a_we),
        .a_addr  (fm_a_addr),
        .a_wdata (fm_a_wdata),
        .a_rdata (fm_a_rdata),
        .b_we    (fm_b_we),
        .b_addr  (fm_b_addr),
        .b_wdata (fm_b_wdata),
        .b_rdata (fm_b_rdata)
    );

    // 6. Weight BRAM（含权重/bias 初始化透传）
    weight_bram #(
        .ADDR_WIDTH (16),
        .INIT_EN    (INIT_EN),
        .CONV1_FILE (W_CONV1_FILE),
        .CONV2_FILE (W_CONV2_FILE),
        .FC1_FILE   (W_FC1_FILE),
        .FC2_FILE   (W_FC2_FILE),
        .BIAS_FILE  (W_BIAS_FILE)
    ) u_w_bram (
        .clk     (clk),
        .a_we    (w_a_we),
        .a_addr  (w_a_addr),
        .a_wdata (w_a_wdata),
        .a_rdata (w_a_rdata),
        .b_addr  (w_b_addr),
        .b_rdata (w_b_rdata)
    );

endmodule
