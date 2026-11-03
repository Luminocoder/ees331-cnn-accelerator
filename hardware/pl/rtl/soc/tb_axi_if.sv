// ============================================================================
// axi_if 独立仿真 Testbench
// 模拟 ARM 通过 AXI-Lite 读写加速器寄存器，验证接口功能
// 运行方式：在 Vivado Sim 中添加本文件 + ../rtl/soc/axi_if.v
// ============================================================================

`timescale 1ns / 1ps

module tb_axi_if;

    reg         clk;
    reg         rst_n;

    // --- AXI Master 侧（模拟 ARM）---
    reg  [31:0] awaddr;
    reg         awvalid;
    wire        awready;
    reg  [31:0] wdata;
    reg         wvalid;
    wire        wready;
    reg  [3:0]  wstrb;
    wire [1:0]  bresp;
    wire        bvalid;
    reg         bready;
    reg  [31:0] araddr;
    reg         arvalid;
    wire        arready;
    wire [31:0] rdata;
    wire [1:0]  rresp;
    wire        rvalid;
    reg         rready;

    // --- 加速器侧（模拟 accelerator）---
    wire        accel_start;
    wire        accel_soft_rst;
    reg         accel_busy;
    reg         accel_done;
    reg         accel_error;
    wire [31:0] img_base_addr;
    wire [31:0] weight_base_addr;
    reg  [31:0] result_0, result_1, result_2, result_3, result_4;
    reg  [31:0] result_5, result_6, result_7, result_8, result_9;
    reg  [31:0] layer_status;

    // ======== DUT 例化 ========
    axi_if #(
        .ADDR_CTRL       (8'h00),
        .ADDR_STATUS     (8'h04),
        .ADDR_IMG_ADDR   (8'h08),
        .ADDR_WEIGHT_ADDR(8'h0C),
        .ADDR_RESULT_0   (8'h10),
        .ADDR_LAYER_DBG  (8'h38)
    ) u_axi_if (
        .clk             (clk),
        .rst_n           (rst_n),
        // AXI
        .s_axi_awaddr    (awaddr),
        .s_axi_awvalid   (awvalid),
        .s_axi_awready   (awready),
        .s_axi_wdata     (wdata),
        .s_axi_wstrb     (wstrb),
        .s_axi_wvalid    (wvalid),
        .s_axi_wready    (wready),
        .s_axi_bresp     (bresp),
        .s_axi_bvalid    (bvalid),
        .s_axi_bready    (bready),
        .s_axi_araddr    (araddr),
        .s_axi_arvalid   (arvalid),
        .s_axi_arready   (arready),
        .s_axi_rdata     (rdata),
        .s_axi_rresp     (rresp),
        .s_axi_rvalid    (rvalid),
        .s_axi_rready    (rready),
        // Accelerator
        .accel_start     (accel_start),
        .accel_soft_rst  (accel_soft_rst),
        .accel_busy      (accel_busy),
        .accel_done      (accel_done),
        .accel_error     (accel_error),
        .img_base_addr   (img_base_addr),
        .weight_base_addr(weight_base_addr),
        .result_0        (result_0),
        .result_1        (result_1),
        .result_2        (result_2),
        .result_3        (result_3),
        .result_4        (result_4),
        .result_5        (result_5),
        .result_6        (result_6),
        .result_7        (result_7),
        .result_8        (result_8),
        .result_9        (result_9),
        .layer_status    (layer_status)
    );

    // ======== Clock 50 MHz ========
    always #10 clk = ~clk;

    // ======== AXI Write Task ========
    task axi_write;
        input [31:0] addr;
        input [31:0] data;
        begin
            @(posedge clk);
            awaddr  <= addr;
            awvalid <= 1'b1;
            wdata   <= data;
            wvalid  <= 1'b1;
            wstrb   <= 4'hF;
            bready  <= 1'b1;
            wait (awready && wready);
            @(posedge clk);
            awvalid <= 1'b0;
            wvalid  <= 1'b0;
            wait (bvalid);
            @(posedge clk);
            bready  <= 1'b0;
        end
    endtask

    // ======== AXI Read Task ========
    task axi_read;
        input  [31:0] addr;
        output [31:0] data;
        begin
            @(posedge clk);
            araddr  <= addr;
            arvalid <= 1'b1;
            rready  <= 1'b1;
            wait (arready);
            @(posedge clk);
            arvalid <= 1'b0;
            wait (rvalid);
            data = rdata;
            @(posedge clk);
            rready  <= 1'b0;
        end
    endtask

    // ======== Main Test Sequence ========
    integer errors;
    reg [31:0] rd_val;

    initial begin
        // --- 初始化 ---
        clk     <= 1'b0;
        rst_n   <= 1'b0;
        awaddr  <= 32'h0; awvalid <= 1'b0;
        wdata   <= 32'h0; wvalid  <= 1'b0; wstrb <= 4'h0;
        bready  <= 1'b0;
        araddr  <= 32'h0; arvalid <= 1'b0;
        rready  <= 1'b0;
        // 模拟加速器状态
        accel_busy  <= 1'b0;
        accel_done  <= 1'b0;
        accel_error <= 1'b0;
        result_0 <= 32'h0000_0055;  // 数字 0 置信度 = 85
        result_1 <= 32'h0000_0010;  // 数字 1 置信度 = 16
        result_2 <= 32'h0000_0003;
        result_3 <= 32'h0000_0007;
        result_4 <= 32'h0000_0001;
        result_5 <= 32'h0000_0002;
        result_6 <= 32'h0000_0001;
        result_7 <= 32'h0000_0000;
        result_8 <= 32'h0000_0009;
        result_9 <= 32'h0000_0006;
        layer_status <= 32'h0000_0001;

        errors = 0;

        // --- 释放复位 ---
        repeat (5) @(posedge clk);
        rst_n <= 1'b1;
        repeat (2) @(posedge clk);

        $display("===== AXI-Lite Interface Test Start =====\n");

        // --- Test 1: Write IMG_ADDR ---
        axi_write(32'h0000_0008, 32'h4000_0000);
        if (img_base_addr === 32'h4000_0000) begin
            $display("[PASS] TEST 1: Write IMG_ADDR = 0x40000000");
        end else begin
            $display("[FAIL] TEST 1: Write IMG_ADDR, expected 0x40000000, got 0x%h", img_base_addr);
            errors = errors + 1;
        end

        // --- Test 2: Write WEIGHT_ADDR ---
        axi_write(32'h0000_000C, 32'h4001_0000);
        if (weight_base_addr === 32'h4001_0000) begin
            $display("[PASS] TEST 2: Write WEIGHT_ADDR = 0x40010000");
        end else begin
            $display("[FAIL] TEST 2: Write WEIGHT_ADDR, expected 0x40010000, got 0x%h", weight_base_addr);
            errors = errors + 1;
        end

        // --- Test 3: Write CTRL to start inference ---
        axi_write(32'h0000_0000, 32'h0000_0001);
        repeat (2) @(posedge clk);
        accel_busy <= 1'b1;
        repeat (5) @(posedge clk);
        accel_busy <= 1'b0;
        accel_done <= 1'b1;

        $display("[INFO] TEST 3: Start inference, accel_start pulse sent");

        // --- Test 4: Read STATUS (done=1) ---
        axi_read(32'h0000_0004, rd_val);
        if (rd_val === 32'h0000_0002) begin
            $display("[PASS] TEST 4: Read STATUS = 0x00000002 (DONE=1)");
        end else begin
            $display("[FAIL] TEST 4: Read STATUS, expected 0x00000002, got 0x%h", rd_val);
            errors = errors + 1;
        end

        // --- Test 5: Read RESULT_0 ---
        axi_read(32'h0000_0010, rd_val);
        if (rd_val === 32'h0000_0055) begin
            $display("[PASS] TEST 5: Read RESULT_0 = 85");
        end else begin
            $display("[FAIL] TEST 5: Read RESULT_0, expected 85, got %0d", rd_val);
            errors = errors + 1;
        end

        // --- Test 6: Read RESULT_9 ---
        axi_read(32'h0000_0034, rd_val);
        if (rd_val === 32'h0000_0006) begin
            $display("[PASS] TEST 6: Read RESULT_9 = 6");
        end else begin
            $display("[FAIL] TEST 6: Read RESULT_9, expected 6, got %0d", rd_val);
            errors = errors + 1;
        end

        // --- Test 7: Read invalid address (should return DEAD_BEEF) ---
        axi_read(32'h0000_00FC, rd_val);
        if (rd_val === 32'hDEAD_BEEF) begin
            $display("[PASS] TEST 7: Read invalid addr returns DEAD_BEEF");
        end else begin
            $display("[FAIL] TEST 7: Read invalid addr, expected DEAD_BEEF, got 0x%h", rd_val);
            errors = errors + 1;
        end

        // --- Test 8: Soft reset ---
        axi_write(32'h0000_0000, 32'h0000_0002);
        repeat (3) @(posedge clk);
        accel_done  <= 1'b0;
        accel_error <= 1'b0;
        $display("[INFO] TEST 8: Soft reset triggered");

        // --- Test 9: Read LAYER_DBG ---
        axi_read(32'h0000_0038, rd_val);
        if (rd_val === 32'h0000_0001) begin
            $display("[PASS] TEST 9: Read LAYER_DBG = 1");
        end else begin
            $display("[FAIL] TEST 9: Read LAYER_DBG, expected 1, got %0d", rd_val);
            errors = errors + 1;
        end

        // --- Summary ---
        $display("\n===== Test Complete =====");
        if (errors == 0) begin
            $display("[PASS] All 9 tests passed!");
        end else begin
            $display("[FAIL] %0d test(s) failed", errors);
        end

        $finish;
    end

endmodule
