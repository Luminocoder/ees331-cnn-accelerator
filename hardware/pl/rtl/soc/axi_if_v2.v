`timescale 1ns / 1ps

// AXI4-Lite slave bridge: ARM PS register access to cnn_accel_top.
// External ports and register offsets are compatible with register_map.md.

module axi_if_v2 #(
    parameter ADDR_CTRL        = 8'h00,
    parameter ADDR_STATUS      = 8'h04,
    parameter ADDR_IMG_ADDR    = 8'h08,
    parameter ADDR_WEIGHT_ADDR = 8'h0C,
    parameter ADDR_RESULT_0    = 8'h10,
    parameter ADDR_LAYER_DBG   = 8'h38,
    parameter ADDR_IMG_DATA    = 8'h40,
    parameter ADDR_IMG_PTR     = 8'h44
) (
    input  wire        clk,
    input  wire        rst_n,

    input  wire [31:0] s_axi_awaddr,
    input  wire        s_axi_awvalid,
    output reg         s_axi_awready,

    input  wire [31:0] s_axi_wdata,
    input  wire [3:0]  s_axi_wstrb,
    input  wire        s_axi_wvalid,
    output reg         s_axi_wready,

    output reg  [1:0]  s_axi_bresp,
    output reg         s_axi_bvalid,
    input  wire        s_axi_bready,

    input  wire [31:0] s_axi_araddr,
    input  wire        s_axi_arvalid,
    output reg         s_axi_arready,

    output reg  [31:0] s_axi_rdata,
    output reg  [1:0]  s_axi_rresp,
    output reg         s_axi_rvalid,
    input  wire        s_axi_rready,

    output reg         accel_start,
    output reg         accel_soft_rst,
    input  wire        accel_busy,
    input  wire        accel_done,
    input  wire        accel_error,

    output reg         fm_a_we,
    output reg  [13:0] fm_a_addr,
    output reg  [7:0]  fm_a_wdata,

    input  wire [31:0] result_0,
    input  wire [31:0] result_1,
    input  wire [31:0] result_2,
    input  wire [31:0] result_3,
    input  wire [31:0] result_4,
    input  wire [31:0] result_5,
    input  wire [31:0] result_6,
    input  wire [31:0] result_7,
    input  wire [31:0] result_8,
    input  wire [31:0] result_9,

    input  wire [31:0] layer_status
);

    localparam [1:0] AXI_OKAY   = 2'b00;
    localparam [1:0] AXI_SLVERR = 2'b10;
    localparam [23:0] AXI_BASE_PAGE = 24'h43C000;

    reg [31:0] awaddr_reg;
    reg        awaddr_valid;
    reg [31:0] wdata_reg;
    reg [3:0]  wstrb_reg;
    reg        wdata_valid;
    reg [13:0] img_wr_ptr;

    wire [31:0] write_mask =
        {{8{wstrb_reg[3]}}, {8{wstrb_reg[2]}}, {8{wstrb_reg[1]}}, {8{wstrb_reg[0]}}};
    wire [31:0] write_data = wdata_reg & write_mask;
    wire        write_ready = awaddr_valid && wdata_valid && !s_axi_bvalid;

    // Direct module tests use local offsets; the Zynq interconnect forwards
    // the configured 32-bit system address unchanged to this slave.
    wire        write_addr_ok = (awaddr_reg[31:8] == 24'h0) ||
                                (awaddr_reg[31:8] == AXI_BASE_PAGE);
    wire        write_reserved_ok =
        (awaddr_reg[7:0] == ADDR_CTRL)     ? (write_data[31:2]  == 30'h0) :
        (awaddr_reg[7:0] == ADDR_IMG_PTR)  ? (write_data[31:14] == 18'h0) :
        (awaddr_reg[7:0] == ADDR_IMG_DATA) ? (write_data[31:8]  == 24'h0) :
                                             1'b1;

    always @(*) begin
        s_axi_awready = !awaddr_valid && !s_axi_bvalid;
        s_axi_wready  = !wdata_valid  && !s_axi_bvalid;
        s_axi_arready = !s_axi_rvalid;
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            awaddr_reg     <= 32'h0;
            awaddr_valid   <= 1'b0;
            wdata_reg      <= 32'h0;
            wstrb_reg      <= 4'h0;
            wdata_valid    <= 1'b0;
            s_axi_bvalid   <= 1'b0;
            s_axi_bresp    <= AXI_OKAY;
            accel_start    <= 1'b0;
            img_wr_ptr     <= 14'h0;
        end else begin
            accel_start    <= 1'b0;

            if (s_axi_awvalid && s_axi_awready) begin
                awaddr_reg   <= s_axi_awaddr;
                awaddr_valid <= 1'b1;
            end

            if (s_axi_wvalid && s_axi_wready) begin
                wdata_reg   <= s_axi_wdata;
                wstrb_reg   <= s_axi_wstrb;
                wdata_valid <= 1'b1;
            end

            if (write_ready) begin
                s_axi_bvalid <= 1'b1;
                s_axi_bresp  <= (write_addr_ok && write_reserved_ok) ? AXI_OKAY : AXI_SLVERR;
                awaddr_valid <= 1'b0;
                wdata_valid  <= 1'b0;

                if (write_addr_ok && write_reserved_ok) begin
                    case (awaddr_reg[7:0])
                        ADDR_CTRL: begin
                            accel_start    <= write_data[0];
                        end
                        ADDR_IMG_PTR: begin
                            img_wr_ptr <= write_data[13:0];
                        end
                        ADDR_IMG_DATA: begin
                            img_wr_ptr <= img_wr_ptr + 14'd1;
                        end
                        default: begin
                        end
                    endcase
                end
            end else if (s_axi_bvalid && s_axi_bready) begin
                s_axi_bvalid <= 1'b0;
            end
        end
    end

    // BRAM controls must not change on asynchronous reset edges (REQP-1839).
    // An issued write finishes at the next clock; reset does not erase BRAM.
    always @(posedge clk) begin
        if (!rst_n) begin
            accel_soft_rst <= 1'b0;
            fm_a_we        <= 1'b0;
            fm_a_addr      <= 14'h0;
            fm_a_wdata     <= 8'h0;
        end else begin
            accel_soft_rst <= 1'b0;
            fm_a_we        <= 1'b0;
            if (write_ready && write_addr_ok && write_reserved_ok) begin
                case (awaddr_reg[7:0])
                    ADDR_CTRL: accel_soft_rst <= write_data[1];
                    ADDR_IMG_DATA: begin
                        fm_a_we    <= 1'b1;
                        fm_a_addr  <= img_wr_ptr;
                        fm_a_wdata <= write_data[7:0];
                    end
                    default: begin
                    end
                endcase
            end
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            s_axi_rvalid <= 1'b0;
            s_axi_rdata  <= 32'h0;
            s_axi_rresp  <= AXI_OKAY;
        end else begin
            if (s_axi_arvalid && s_axi_arready) begin
                s_axi_rvalid <= 1'b1;
                s_axi_rresp  <= ((s_axi_araddr[31:8] == 24'h0) ||
                                 (s_axi_araddr[31:8] == AXI_BASE_PAGE)) ? AXI_OKAY : AXI_SLVERR;

                if ((s_axi_araddr[31:8] != 24'h0) &&
                    (s_axi_araddr[31:8] != AXI_BASE_PAGE)) begin
                    s_axi_rdata <= 32'hDEAD_BEEF;
                end else begin
                    case (s_axi_araddr[7:0])
                        ADDR_STATUS:          s_axi_rdata <= {29'h0, accel_error, accel_done, accel_busy};
                        ADDR_RESULT_0:        s_axi_rdata <= result_0;
                        ADDR_RESULT_0 + 8'h04: s_axi_rdata <= result_1;
                        ADDR_RESULT_0 + 8'h08: s_axi_rdata <= result_2;
                        ADDR_RESULT_0 + 8'h0C: s_axi_rdata <= result_3;
                        ADDR_RESULT_0 + 8'h10: s_axi_rdata <= result_4;
                        ADDR_RESULT_0 + 8'h14: s_axi_rdata <= result_5;
                        ADDR_RESULT_0 + 8'h18: s_axi_rdata <= result_6;
                        ADDR_RESULT_0 + 8'h1C: s_axi_rdata <= result_7;
                        ADDR_RESULT_0 + 8'h20: s_axi_rdata <= result_8;
                        ADDR_RESULT_0 + 8'h24: s_axi_rdata <= result_9;
                        ADDR_LAYER_DBG:       s_axi_rdata <= layer_status;
                        default:              s_axi_rdata <= 32'hDEAD_BEEF;
                    endcase
                end
            end else if (s_axi_rvalid && s_axi_rready) begin
                s_axi_rvalid <= 1'b0;
            end
        end
    end

endmodule
