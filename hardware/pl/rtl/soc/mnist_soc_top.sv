`timescale 1ns / 1ps

// ============================================================================
// mnist_soc_top — FPGA 顶层封装（Phase 4，方案 A）
//
// 集成：cnn_accel_top（权重烘焙 INIT_EN=1）+ axi_if（控制 + 图片写入）
// 对外：单个 AXI-Lite 从机 + clk/rst + irq_done
//
// 数据流：
//   ARM --AXI-Lite--> axi_if --控制信号--> cnn_accel_top
//                             --图片写入(fm_a_*)--> cnn_accel_top
//   权重在综合时通过 $readmemh 烘焙进 weight_bram（无需 ARM 运行时写）
//
// 注意：权重文件路径需在综合时指向实际文件（可 override 参数，或把
//       ref_model/weights_int8/ 拷贝到 Vivado 工程目录）。
// ============================================================================

module mnist_soc_top #(
    // 权重烘焙文件路径（综合时 $readmemh 用）
    // 用绝对路径，避免 Vivado 相对路径解析歧义（相对工程目录 vs 源文件目录）
    parameter [8*255:0] W_CONV1_FILE = "ref_model/weights_int8/Conv1_int8.txt",
    parameter [8*255:0] W_CONV2_FILE = "ref_model/weights_int8/Conv2_int8.txt",
    parameter [8*255:0] W_FC1_FILE   = "ref_model/weights_int8/FC1_int8.txt",
    parameter [8*255:0] W_FC2_FILE   = "ref_model/weights_int8/FC2_int8.txt",
    parameter [8*255:0] W_BIAS_FILE  = "ref_model/weights_int8/bias_layout.txt"
) (
    // ========== 时钟与复位 ==========
    input  wire        clk,
    input  wire        rst_n,

    // ========== AXI4-Lite 从机（接 AXI Interconnect / M_AXI_GP0）==========
    input  wire [31:0] s_axi_awaddr,
    input  wire        s_axi_awvalid,
    output wire        s_axi_awready,
    input  wire [31:0] s_axi_wdata,
    input  wire [3:0]  s_axi_wstrb,
    input  wire        s_axi_wvalid,
    output wire        s_axi_wready,
    output wire [1:0]  s_axi_bresp,
    output wire        s_axi_bvalid,
    input  wire        s_axi_bready,
    input  wire [31:0] s_axi_araddr,
    input  wire        s_axi_arvalid,
    output wire        s_axi_arready,
    output wire [31:0] s_axi_rdata,
    output wire [1:0]  s_axi_rresp,
    output wire        s_axi_rvalid,
    input  wire        s_axi_rready,

    // ========== 中断（可选，接 GIC）==========
    output wire        irq_done
);

    // ---- axi_if <-> cnn_accel_top 互连 ----
    wire        accel_start, accel_soft_rst;
    wire        accel_busy, accel_done, accel_error;
    wire [31:0] result_0, result_1, result_2, result_3, result_4;
    wire [31:0] result_5, result_6, result_7, result_8, result_9;
    wire [31:0] layer_status;
    wire        fm_a_we;
    wire [13:0] fm_a_addr;
    wire [7:0]  fm_a_wdata;

    // 未用信号（权重 BRAM 端口 A 烘焙后不需写；fm_a_rdata 不读回）
    wire [7:0]  fm_a_rdata;
    wire        w_a_we   = 1'b0;
    wire [15:0] w_a_addr = 16'h0;
    wire [7:0]  w_a_wdata = 8'h0;
    wire [7:0]  w_a_rdata;

    // ---- axi_if：控制 + 图片写入 ----
    axi_if_v2 u_axi_if (
        .clk(clk), .rst_n(rst_n),
        .s_axi_awaddr(s_axi_awaddr), .s_axi_awvalid(s_axi_awvalid), .s_axi_awready(s_axi_awready),
        .s_axi_wdata(s_axi_wdata),   .s_axi_wstrb(s_axi_wstrb),     .s_axi_wvalid(s_axi_wvalid),  .s_axi_wready(s_axi_wready),
        .s_axi_bresp(s_axi_bresp),   .s_axi_bvalid(s_axi_bvalid),    .s_axi_bready(s_axi_bready),
        .s_axi_araddr(s_axi_araddr), .s_axi_arvalid(s_axi_arvalid),  .s_axi_arready(s_axi_arready),
        .s_axi_rdata(s_axi_rdata),   .s_axi_rresp(s_axi_rresp),      .s_axi_rvalid(s_axi_rvalid),  .s_axi_rready(s_axi_rready),
        .accel_start(accel_start),   .accel_soft_rst(accel_soft_rst),
        .accel_busy(accel_busy),     .accel_done(accel_done),        .accel_error(accel_error),
        .result_0(result_0), .result_1(result_1), .result_2(result_2), .result_3(result_3), .result_4(result_4),
        .result_5(result_5), .result_6(result_6), .result_7(result_7), .result_8(result_8), .result_9(result_9),
        .layer_status(layer_status),
        .fm_a_we(fm_a_we), .fm_a_addr(fm_a_addr), .fm_a_wdata(fm_a_wdata)
    );

    // ---- cnn_accel_top：权重烘焙（INIT_EN=1）----
    cnn_accel_top #(
        .INIT_EN(1),
        .W_CONV1_FILE(W_CONV1_FILE), .W_CONV2_FILE(W_CONV2_FILE),
        .W_FC1_FILE(W_FC1_FILE), .W_FC2_FILE(W_FC2_FILE),
        .W_BIAS_FILE(W_BIAS_FILE)
    ) u_accel (
        .clk(clk), .rst_n(rst_n),
        .accel_start(accel_start), .accel_soft_rst(accel_soft_rst),
        .accel_busy(accel_busy), .accel_done(accel_done), .accel_error(accel_error),
        .result_0(result_0), .result_1(result_1), .result_2(result_2),
        .result_3(result_3), .result_4(result_4), .result_5(result_5),
        .result_6(result_6), .result_7(result_7), .result_8(result_8),
        .result_9(result_9),
        .layer_status(layer_status), .irq_done(irq_done),
        .fm_a_we(fm_a_we), .fm_a_addr(fm_a_addr), .fm_a_wdata(fm_a_wdata), .fm_a_rdata(fm_a_rdata),
        .w_a_we(w_a_we), .w_a_addr(w_a_addr), .w_a_wdata(w_a_wdata), .w_a_rdata(w_a_rdata)
    );

endmodule
