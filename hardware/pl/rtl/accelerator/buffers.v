`timescale 1ns / 1ps

// ============================================================================
// buffers.v — 任务 B6 交付物：Weight BRAM + Feature Map BRAM
// 版本: v1.0
//
// 两个真双口 BRAM 模块，读写均为同步（1 周期延迟）。
//
// 1. weight_bram — 权重存储（64 KB）
//    - 按 docs/design/weight-format.md 冻结顺序用 $readmemh 初始化：
//        Conv1 @ 0      (72 B)
//        Conv2 @ 72     (1152 B)
//        FC1   @ 1224   (50176 B)
//        FC2   @ 51400  (640 B)     共 52040 B
//    - 端口 A：ARM 配置侧（写权重 / 读回校验）
//    - 端口 B：引擎只读侧
//    - INIT_EN=0（默认）时不加载文件，综合安全
//
// 2. fm_bram — 特征图存储（16 KB，ping-pong 两个 8 KB bank）
//    - bank0: 0x0000-0x1FFF   bank1: 0x2000-0x3FFF
//    - 两个端口均可读写（engine 读一个 bank、写另一个 bank）
//    - 同周期同地址双写时端口 B 优先（文档化行为）
// ============================================================================


// ----------------------------------------------------------------------------
// weight_bram — 64 KB 权重存储
// ----------------------------------------------------------------------------
module weight_bram #(
    parameter ADDR_WIDTH = 16,
    parameter DATA_WIDTH = 8,
    parameter INIT_EN    = 0,          // 1 = 仿真时 $readmemh 加载权重文件
    parameter [8*255:0] CONV1_FILE = "",
    parameter [8*255:0] CONV2_FILE = "",
    parameter [8*255:0] FC1_FILE   = "",
    parameter [8*255:0] FC2_FILE   = "",
    // ADR-0002 决定 4：bias INT32 大端，追加于权重区尾部
    parameter [8*255:0] BIAS_FILE  = "",
    parameter BIAS_BASE = 52040,
    parameter BIAS_LEN  = 392,    // (8+16+64+10) 个 INT32 × 4 字节
    // 加载偏移（冻结于 docs/design/weight-format.md，勿改）
    parameter CONV1_BASE = 0,
    parameter CONV2_BASE = 72,
    parameter FC1_BASE   = 1224,
    parameter FC2_BASE   = 51400,
    parameter CONV1_LEN  = 72,
    parameter CONV2_LEN  = 1152,
    parameter FC1_LEN    = 50176,
    parameter FC2_LEN    = 640
) (
    input  wire                    clk,

    // ---- 端口 A：ARM 配置侧（读写） ----
    input  wire                    a_we,
    input  wire [ADDR_WIDTH-1:0]   a_addr,
    input  wire [DATA_WIDTH-1:0]   a_wdata,
    output reg  [DATA_WIDTH-1:0]   a_rdata,

    // ---- 端口 B：引擎只读侧 ----
    input  wire [ADDR_WIDTH-1:0]   b_addr,
    output reg  [DATA_WIDTH-1:0]   b_rdata
);

    localparam DEPTH = 1 << ADDR_WIDTH;
    reg [DATA_WIDTH-1:0] mem [0:DEPTH-1];

    // 仿真初始化：按冻结偏移加载四个权重文件 + bias 段
    initial begin
        if (INIT_EN) begin
            $readmemh(CONV1_FILE, mem, CONV1_BASE, CONV1_BASE + CONV1_LEN - 1);
            $readmemh(CONV2_FILE, mem, CONV2_BASE, CONV2_BASE + CONV2_LEN - 1);
            $readmemh(FC1_FILE,   mem, FC1_BASE,   FC1_BASE   + FC1_LEN   - 1);
            $readmemh(FC2_FILE,   mem, FC2_BASE,   FC2_BASE   + FC2_LEN   - 1);
            if (BIAS_FILE != "")
                $readmemh(BIAS_FILE, mem, BIAS_BASE, BIAS_BASE + BIAS_LEN - 1);
        end
    end

    // 端口 A：同步读写
    always @(posedge clk) begin
        if (a_we)
            mem[a_addr] <= a_wdata;
        a_rdata <= mem[a_addr];
    end

    // 端口 B：同步只读
    always @(posedge clk) begin
        b_rdata <= mem[b_addr];
    end

endmodule


// ----------------------------------------------------------------------------
// fm_bram — 16 KB 特征图存储（ping-pong）
// ----------------------------------------------------------------------------
module fm_bram #(
    parameter ADDR_WIDTH = 14,
    parameter DATA_WIDTH = 8
) (
    input  wire                    clk,

    // ---- 端口 A（读写） ----
    input  wire                    a_we,
    input  wire [ADDR_WIDTH-1:0]   a_addr,
    input  wire [DATA_WIDTH-1:0]   a_wdata,
    output reg  [DATA_WIDTH-1:0]   a_rdata,

    // ---- 端口 B（读写） ----
    input  wire                    b_we,
    input  wire [ADDR_WIDTH-1:0]   b_addr,
    input  wire [DATA_WIDTH-1:0]   b_wdata,
    output reg  [DATA_WIDTH-1:0]   b_rdata
);

    localparam DEPTH = 1 << ADDR_WIDTH;
    reg [DATA_WIDTH-1:0] mem [0:DEPTH-1];

    // 端口 A：同周期同地址双写时 B 优先（A 先写，B 覆盖）
    always @(posedge clk) begin
        if (a_we)
            mem[a_addr] <= a_wdata;
        a_rdata <= mem[a_addr];
    end

    always @(posedge clk) begin
        if (b_we)
            mem[b_addr] <= b_wdata;
        b_rdata <= mem[b_addr];
    end

endmodule
