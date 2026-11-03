//=============================================================================
// sevenseg_axi.v —— 数码管显示的 AXI4-Lite 从机（挂到已有 AXI interconnect 上）
//
// 寄存器映射（基址建议 0x43C10000，与加速器 0x43C00000 相邻）
//   0x00  VALUE   [31:0] R/W  要显示的数值（两位数模式写 10*十位 + 个位）
//   0x04  CTRL    [2:0]  R/W  bit0 BLANK(默认1=全灭)，bit2:1 FMT(0=十进制 1=十六进制 2=自检)
//   0x08  MAGIC   [31:0] R    固定 0x53454731 ("SEG1")，给 ARM 做在位自检
//   0x0C  PAGE_ID [31:0] R    0x50414731 ("PAG1"); CTRL=6 selects independent cells
//
// 端口名按 Xilinx 的 X_INTERFACE_INFO 约定标注，因此可以直接在 Block Design 里用
// "Add Module"（模块引用）方式把本文件连同 sevenseg_result.v 一起加进去，
// 不需要 ipx 打包成 IP。
//=============================================================================
`timescale 1ns / 1ps
`default_nettype none

module sevenseg_axi #(
    parameter integer CLK_HZ          = 50_000_000,
    parameter integer SCAN_HZ         = 1_000,
    parameter integer SEG_ACTIVE_HIGH = 1,
    parameter integer DIG_ACTIVE_HIGH = 1,
    parameter integer ORDER_REVERSED  = 0
)(
    // ---------------- AXI4-Lite 从机 ----------------
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 clk CLK" *)
    (* X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF S_AXI, ASSOCIATED_RESET rst_n, FREQ_HZ 50000000" *)
    input  wire        clk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 rst_n RST" *)
    (* X_INTERFACE_PARAMETER = "POLARITY ACTIVE_LOW" *)
    input  wire        rst_n,

    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWADDR" *)
    (* X_INTERFACE_PARAMETER = "PROTOCOL AXI4LITE, DATA_WIDTH 32, ADDR_WIDTH 6" *)
    input  wire [5:0]  s_axi_awaddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWVALID" *)
    input  wire        s_axi_awvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWREADY" *)
    output wire        s_axi_awready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WDATA" *)
    input  wire [31:0] s_axi_wdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WSTRB" *)
    input  wire [3:0]  s_axi_wstrb,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WVALID" *)
    input  wire        s_axi_wvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WREADY" *)
    output wire        s_axi_wready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BRESP" *)
    output wire [1:0]  s_axi_bresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BVALID" *)
    output wire        s_axi_bvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BREADY" *)
    input  wire        s_axi_bready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARADDR" *)
    input  wire [5:0]  s_axi_araddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARVALID" *)
    input  wire        s_axi_arvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARREADY" *)
    output wire        s_axi_arready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RDATA" *)
    output wire [31:0] s_axi_rdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RRESP" *)
    output wire [1:0]  s_axi_rresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RVALID" *)
    output wire        s_axi_rvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RREADY" *)
    input  wire        s_axi_rready,

    // ---------------- 数码管（24 根 PL 引脚，约束见 xdc/ees331_sevenseg.xdc）----
    output wire [15:0] seg_bus,       // {seg_high[7:0], seg_low[7:0]} = LED0_CA..DP, LED1_CA..DP
    output wire [7:0]  dig_bus        // bit0..7 = LED_BIT1..LED_BIT8（DK0..DK7）
);
    localparam [31:0] MAGIC = 32'h5345_4731;   // "SEG1"

    // ------------------------------------------------------------------
    // 写地址 / 写数据握手（AXI4-Lite 从机标准模板）
    // ------------------------------------------------------------------
    reg        axi_awready;
    reg [5:0]  axi_awaddr;
    reg        axi_wready;
    reg        axi_bvalid;
    reg [1:0]  axi_bresp;

    assign s_axi_awready = axi_awready;
    assign s_axi_wready  = axi_wready;
    assign s_axi_bvalid  = axi_bvalid;
    assign s_axi_bresp   = axi_bresp;

    wire write_accept = (~axi_awready) && s_axi_awvalid && s_axi_wvalid;

    always @(posedge clk) begin
        if (!rst_n) begin
            axi_awready <= 1'b0;
            axi_wready  <= 1'b0;
            axi_awaddr  <= 6'd0;
        end else begin
            axi_awready <= write_accept;
            axi_wready  <= write_accept;
            if (write_accept)
                axi_awaddr <= s_axi_awaddr;
        end
    end

    always @(posedge clk) begin
        if (!rst_n) begin
            axi_bvalid <= 1'b0;
            axi_bresp  <= 2'b00;
        end else if (axi_awready && s_axi_awvalid && axi_wready && s_axi_wvalid
                     && !axi_bvalid) begin
            axi_bvalid <= 1'b1;
            axi_bresp  <= 2'b00;
        end else if (axi_bvalid && s_axi_bready) begin
            axi_bvalid <= 1'b0;
        end
    end

    // ------------------------------------------------------------------
    // 寄存器
    // ------------------------------------------------------------------
    reg [31:0] value_reg;
    reg [2:0]  ctrl_reg;      // bit0 BLANK, bit2:1 FMT；复位值 3'b001 = 全灭

    wire [3:0] byte_we = {4{axi_awready && s_axi_awvalid && axi_wready && s_axi_wvalid
                            && !axi_bvalid}} & s_axi_wstrb;

    always @(posedge clk) begin
        if (!rst_n) begin
            value_reg <= 32'd0;
            ctrl_reg  <= 3'b001;
        end else if (|byte_we) begin
            case (axi_awaddr[5:2])
                4'd0: begin
                    if (byte_we[0]) value_reg[7:0]   <= s_axi_wdata[7:0];
                    if (byte_we[1]) value_reg[15:8]  <= s_axi_wdata[15:8];
                    if (byte_we[2]) value_reg[23:16] <= s_axi_wdata[23:16];
                    if (byte_we[3]) value_reg[31:24] <= s_axi_wdata[31:24];
                end
                4'd1: begin
                    if (byte_we[0]) ctrl_reg <= s_axi_wdata[2:0];
                end
                default: ;                      // 0x08 MAGIC 只读
            endcase
        end
    end

    // ------------------------------------------------------------------
    // 读通道
    // ------------------------------------------------------------------
    reg        axi_arready;
    reg [5:0]  axi_araddr;
    reg        axi_rvalid;
    reg [1:0]  axi_rresp;
    reg [31:0] axi_rdata;

    assign s_axi_arready = axi_arready;
    assign s_axi_rvalid  = axi_rvalid;
    assign s_axi_rresp   = axi_rresp;
    assign s_axi_rdata   = axi_rdata;

    always @(posedge clk) begin
        if (!rst_n) begin
            axi_arready <= 1'b0;
            axi_araddr  <= 6'd0;
        end else begin
            axi_arready <= (~axi_arready) && s_axi_arvalid && (~axi_rvalid);
            if ((~axi_arready) && s_axi_arvalid && (~axi_rvalid))
                axi_araddr <= s_axi_araddr;
        end
    end

    always @(posedge clk) begin
        if (!rst_n) begin
            axi_rvalid <= 1'b0;
            axi_rresp  <= 2'b00;
        end else if (axi_arready && s_axi_arvalid && !axi_rvalid) begin
            axi_rvalid <= 1'b1;
            axi_rresp  <= 2'b00;
        end else if (axi_rvalid && s_axi_rready) begin
            axi_rvalid <= 1'b0;
        end
    end

    always @* begin
        case (axi_araddr[5:2])
            4'd0:    axi_rdata = value_reg;
            4'd1:    axi_rdata = {29'd0, ctrl_reg};
            4'd2:    axi_rdata = MAGIC;
            4'd3:    axi_rdata = 32'h50414731;
            default: axi_rdata = 32'hDEAD_BEEF;
        endcase
    end

    // ------------------------------------------------------------------
    // 显示驱动
    // ------------------------------------------------------------------
    sevenseg_result #(
        .CLK_HZ          (CLK_HZ),
        .SCAN_HZ         (SCAN_HZ),
        .SEG_ACTIVE_HIGH (SEG_ACTIVE_HIGH),
        .DIG_ACTIVE_HIGH (DIG_ACTIVE_HIGH),
        .ORDER_REVERSED  (ORDER_REVERSED)
    ) u_display (
        .clk      (clk),
        .rst_n    (rst_n),
        .value    (value_reg),
        .fmt      (ctrl_reg[2:1]),
        .blank    (ctrl_reg[0]),
        .seg_low  (seg_bus[7:0]),
        .seg_high (seg_bus[15:8]),
        .dig_sel  (dig_bus)
    );

endmodule

`default_nettype wire
