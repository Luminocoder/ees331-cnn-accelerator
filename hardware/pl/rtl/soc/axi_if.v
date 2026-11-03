`timescale 1ns / 1ps
// ============================================================================
// AXI-Lite 从机接口 — ARM PS ↔ CNN 加速器 的寄存器读写桥梁
// ============================================================================
// 协议：AXI4-Lite（32位数据，单拍读写，无 burst）
// 从机角色：ARM 是 Master（M_AXI_GP0），本模块是 Slave
// 连接对象：cnn_accel_top 的控制/状态/数据寄存器
// ============================================================================

module axi_if #(
    // 寄存器地址参数（与 register_map.md 一致）
    parameter ADDR_CTRL       = 8'h00,  // 控制寄存器 (W)
    parameter ADDR_STATUS     = 8'h04,  // 状态寄存器 (R)
    parameter ADDR_IMG_ADDR   = 8'h08,  // 输入图片基地址 (W)
    parameter ADDR_WEIGHT_ADDR= 8'h0C,  // 权重基地址 (W)
    parameter ADDR_RESULT_0   = 8'h10,  // 结果 0-9 (R)
    parameter ADDR_LAYER_DBG  = 8'h38,  // 调试：当前层 (R)
    parameter ADDR_IMG_DATA   = 8'h40,  // 图片数据（写一字节，指针自增）(W)
    parameter ADDR_IMG_PTR    = 8'h44   // 图片写指针重置 (W)
) (
    // ======== 全局信号 ========
    input  wire        clk,
    input  wire        rst_n,

    // ======== AXI4-Lite 从机接口 ========
    // 写地址通道 (AW)
    input  wire [31:0] s_axi_awaddr,
    input  wire        s_axi_awvalid,
    output reg         s_axi_awready,

    // 写数据通道 (W)
    input  wire [31:0] s_axi_wdata,
    input  wire [3:0]  s_axi_wstrb,
    input  wire        s_axi_wvalid,
    output reg         s_axi_wready,

    // 写响应通道 (B)
    output reg  [1:0]  s_axi_bresp,
    output reg         s_axi_bvalid,
    input  wire        s_axi_bready,

    // 读地址通道 (AR)
    input  wire [31:0] s_axi_araddr,
    input  wire        s_axi_arvalid,
    output reg         s_axi_arready,

    // 读数据通道 (R)
    output reg  [31:0] s_axi_rdata,
    output reg  [1:0]  s_axi_rresp,
    output reg         s_axi_rvalid,
    input  wire        s_axi_rready,

    // ======== 加速器侧信号（连接到 cnn_accel_top）========
    // 控制寄存器输出
    output reg         accel_start,       // CTRL[0]: 启动推理
    output reg         accel_soft_rst,    // CTRL[1]: 软复位

    // 状态寄存器输入
    input  wire        accel_busy,        // STATUS[0]: 加速器忙碌
    input  wire        accel_done,        // STATUS[1]: 推理完成
    input  wire        accel_error,       // STATUS[2]: 出错

    // ======== 图片写入通路（ADR-0002 方案A：权重烘焙，图片运行时写）========
    output reg         fm_a_we,           // 写 IMG_DATA 时置 1 一拍
    output reg  [13:0] fm_a_addr,         // 图片写入地址（= img_wr_ptr）
    output reg  [7:0]  fm_a_wdata,        // 图片字节（uint8>>1 后）

    // 推理结果输入
    input  wire [31:0] result_0,          // 数字 0 的置信度
    input  wire [31:0] result_1,
    input  wire [31:0] result_2,
    input  wire [31:0] result_3,
    input  wire [31:0] result_4,
    input  wire [31:0] result_5,
    input  wire [31:0] result_6,
    input  wire [31:0] result_7,
    input  wire [31:0] result_8,
    input  wire [31:0] result_9,

    // 调试信息
    input  wire [31:0] layer_status      // 当前执行层编号
);

    // ======== 内部信号 ========
    reg [7:0]  wr_addr;     // 缓存的写地址
    reg [7:0]  rd_addr;     // 缓存的读地址
    reg        wr_pending;  // 有一个写事务在处理中
    reg        rd_pending;  // 有一个读事务在处理中
    reg [13:0] img_wr_ptr;  // 图片写入指针（IMG_DATA 每次自增）

    // ======== AXI 写通道状态机 ========
    localparam W_IDLE  = 2'b00;
    localparam W_DATA  = 2'b01;
    localparam W_RESP  = 2'b10;

    reg [1:0] w_state, w_next;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            w_state <= W_IDLE;
        else
            w_state <= w_next;
    end

    always @(*) begin
        w_next = w_state;
        case (w_state)
            W_IDLE: if (s_axi_awvalid && s_axi_wvalid)  // 地址和数据同时到达
                        w_next = W_RESP;
                    else if (s_axi_awvalid)
                        w_next = W_DATA;
            W_DATA: if (s_axi_wvalid)
                        w_next = W_RESP;
            W_RESP: if (s_axi_bready)
                        w_next = W_IDLE;
        endcase
    end

    // 写通道握手
    always @(*) begin
        s_axi_awready = (w_state == W_IDLE) || (w_state == W_DATA && !s_axi_awvalid);
        s_axi_wready  = (w_state == W_DATA) || (w_state == W_IDLE && s_axi_awvalid);
    end

    // ======== AXI 读通道状态机 ========
    localparam R_IDLE = 2'b00;
    localparam R_RESP = 2'b01;

    reg [1:0] r_state, r_next;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            r_state <= R_IDLE;
        else
            r_state <= r_next;
    end

    always @(*) begin
        r_next = r_state;
        case (r_state)
            R_IDLE: if (s_axi_arvalid) r_next = R_RESP;
            R_RESP: if (s_axi_rready)  r_next = R_IDLE;
        endcase
    end

    always @(*) begin
        s_axi_arready = (r_state == R_IDLE);
    end

    // ======== 地址缓存 ========
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            wr_addr <= 8'h00;
            rd_addr <= 8'h00;
        end else begin
            if (s_axi_awvalid && s_axi_awready)
                wr_addr <= s_axi_awaddr[7:0];  // 取地址低 8 位
            if (s_axi_arvalid && s_axi_arready)
                rd_addr <= s_axi_araddr[7:0];
        end
    end

    // ======== 写响应 ========
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            s_axi_bvalid <= 1'b0;
            s_axi_bresp  <= 2'b00;
        end else begin
            if (w_state == W_RESP && !s_axi_bvalid) begin
                s_axi_bvalid <= 1'b1;
                s_axi_bresp  <= 2'b00;  // OKAY
            end else if (s_axi_bready) begin
                s_axi_bvalid <= 1'b0;
            end
        end
    end

    // ======== 写寄存器逻辑 ========
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            accel_start      <= 1'b0;
            accel_soft_rst   <= 1'b0;
            fm_a_we          <= 1'b0;
            fm_a_addr        <= 14'h0;
            fm_a_wdata       <= 8'h0;
            img_wr_ptr       <= 14'h0;
        end else begin
            // START / fm_a_we 写完后自动清零（单周期脉冲）
            accel_start    <= 1'b0;
            accel_soft_rst <= 1'b0;
            fm_a_we        <= 1'b0;

            // 使用 s_axi_awaddr 而非 wr_addr，避免地址锁存比数据晚一个周期
            if (s_axi_wvalid && s_axi_wready) begin
                case (s_axi_awaddr[7:0])
                    ADDR_CTRL: begin
                        accel_start    <= s_axi_wdata[0];  // bit0 = START
                        accel_soft_rst <= s_axi_wdata[1];  // bit1 = SOFT_RST
                    end
                    ADDR_IMG_PTR:     img_wr_ptr       <= s_axi_wdata[13:0];
                    ADDR_IMG_DATA: begin
                        fm_a_we    <= 1'b1;                // 一拍写使能
                        fm_a_addr  <= img_wr_ptr;          // 写当前指针位置
                        fm_a_wdata <= s_axi_wdata[7:0];    // 图片字节（uint8>>1 后）
                        img_wr_ptr <= img_wr_ptr + 1'b1;   // 指针自增
                    end
                    default: ;  // 只读寄存器，写操作被忽略
                endcase
            end
        end
    end

    // ======== 读数据逻辑 ========
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            s_axi_rvalid <= 1'b0;
            s_axi_rdata  <= 32'h0;
            s_axi_rresp  <= 2'b00;
        end else begin
            if (r_state == R_RESP && !s_axi_rvalid) begin
                s_axi_rvalid <= 1'b1;
                s_axi_rresp  <= 2'b00;  // OKAY
                case (s_axi_araddr[7:0])
                    ADDR_STATUS: begin
                        s_axi_rdata <= {29'h0,
                                        accel_error,  // bit2
                                        accel_done,   // bit1
                                        accel_busy};  // bit0
                    end
                    ADDR_RESULT_0:  s_axi_rdata <= result_0;
                    ADDR_RESULT_0 + 8'h04: s_axi_rdata <= result_1;
                    ADDR_RESULT_0 + 8'h08: s_axi_rdata <= result_2;
                    ADDR_RESULT_0 + 8'h0C: s_axi_rdata <= result_3;
                    ADDR_RESULT_0 + 8'h10: s_axi_rdata <= result_4;
                    ADDR_RESULT_0 + 8'h14: s_axi_rdata <= result_5;
                    ADDR_RESULT_0 + 8'h18: s_axi_rdata <= result_6;
                    ADDR_RESULT_0 + 8'h1C: s_axi_rdata <= result_7;
                    ADDR_RESULT_0 + 8'h20: s_axi_rdata <= result_8;
                    ADDR_RESULT_0 + 8'h24: s_axi_rdata <= result_9;
                    ADDR_LAYER_DBG: s_axi_rdata <= layer_status;
                    default:       s_axi_rdata <= 32'hDEAD_BEEF;  // 调试标记
                endcase
            end else if (s_axi_rready) begin
                s_axi_rvalid <= 1'b0;
            end
        end
    end

endmodule
