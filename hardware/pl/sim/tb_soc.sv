// ============================================================================
// tb_soc — 系统级 Testbench
// 模拟 ARM 完成一次 MNIST 推理：写权重地址 → 写图片地址 → 启动 → 等完成 → 读结果
// ============================================================================
// 当前：加速器用 stub 占位（模拟 busy/done 时序 + 返回假结果）
// B 交付 cnn_accel_top.v 后替换 stub 即可，顶层端口不变
// ============================================================================

`timescale 1ns / 1ps

module tb_soc;

    // ======== 全局 ========
    reg         clk;
    reg         rst_n;

    // ======== AXI Master（模拟 ARM）========
    reg  [31:0] awaddr;   reg awvalid;   wire awready;
    reg  [31:0] wdata;    reg wvalid;    wire wready;
    reg  [3:0]  wstrb;
    wire [1:0]  bresp;    wire bvalid;   reg bready;
    reg  [31:0] araddr;   reg arvalid;   wire arready;
    wire [31:0] rdata;    wire [1:0] rresp; wire rvalid;   reg rready;

    // ======== 加速器侧（连接 axios_if）========
    wire        accel_start;
    wire        accel_soft_rst;
    wire        accel_busy;
    wire        accel_done;
    wire        accel_error;
    wire [31:0] img_base_addr;
    wire [31:0] weight_base_addr;
    wire [31:0] result_0, result_1, result_2, result_3, result_4;
    wire [31:0] result_5, result_6, result_7, result_8, result_9;
    wire [31:0] layer_status;

    // ======== DUT: axi_if ========
    axi_if #(
        .ADDR_CTRL       (8'h00),
        .ADDR_STATUS     (8'h04),
        .ADDR_IMG_ADDR   (8'h08),
        .ADDR_WEIGHT_ADDR(8'h0C),
        .ADDR_RESULT_0   (8'h10),
        .ADDR_LAYER_DBG  (8'h38)
    ) u_axi_if (
        .clk(clk), .rst_n(rst_n),
        .s_axi_awaddr(awaddr), .s_axi_awvalid(awvalid), .s_axi_awready(awready),
        .s_axi_wdata(wdata),   .s_axi_wstrb(wstrb),     .s_axi_wvalid(wvalid),  .s_axi_wready(wready),
        .s_axi_bresp(bresp),   .s_axi_bvalid(bvalid),    .s_axi_bready(bready),
        .s_axi_araddr(araddr), .s_axi_arvalid(arvalid),  .s_axi_arready(arready),
        .s_axi_rdata(rdata),   .s_axi_rresp(rresp),      .s_axi_rvalid(rvalid),  .s_axi_rready(rready),
        .accel_start(accel_start),  .accel_soft_rst(accel_soft_rst),
        .accel_busy(accel_busy),    .accel_done(accel_done), .accel_error(accel_error),
        .img_base_addr(img_base_addr),   .weight_base_addr(weight_base_addr),
        .result_0(result_0), .result_1(result_1), .result_2(result_2), .result_3(result_3), .result_4(result_4),
        .result_5(result_5), .result_6(result_6), .result_7(result_7), .result_8(result_8), .result_9(result_9),
        .layer_status(layer_status)
    );

    // ======== 加速器 Stub（模拟 CNN 加速器行为，等 B 交付后替换）========
    // 行为：收到 start → 拉高 busy（模拟计算中）→ 50 周期后拉低 busy 并拉高 done
    //       返回值：result_0=85（最高置信度=数字 0），其余 1-9
    reg [7:0]  accel_counter;
    reg        accel_running;

    assign accel_busy   = accel_running;
    assign accel_error  = 1'b0;

    reg        stub_done_reg;
    assign accel_done   = stub_done_reg;
    assign layer_status = {24'h0, accel_counter};

    // 假结果：模拟一次推理后输出 "数字 0 置信度最高(85)，数字 7 次之(70)"
    assign result_0 = 32'd85;   assign result_1 = 32'd3;    assign result_2 = 32'd8;
    assign result_3 = 32'd15;   assign result_4 = 32'd2;    assign result_5 = 32'd4;
    assign result_6 = 32'd12;   assign result_7 = 32'd70;   assign result_8 = 32'd1;
    assign result_9 = 32'd5;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            accel_running <= 1'b0;
            stub_done_reg  <= 1'b0;
            accel_counter  <= 8'd0;
        end else begin
            if (accel_soft_rst) begin
                accel_running <= 1'b0;
                stub_done_reg  <= 1'b0;
                accel_counter  <= 8'd0;
            end else if (accel_start && !accel_running) begin
                // 新的推理开始，清除上一次的 DONE
                accel_running <= 1'b1;
                stub_done_reg  <= 1'b0;
                accel_counter  <= 8'd1;
            end else if (accel_running) begin
                if (accel_counter < 8'd50) begin
                    accel_counter <= accel_counter + 8'd1;
                end else begin
                    accel_running <= 1'b0;
                    stub_done_reg  <= 1'b1;   // DONE 保持高，直到下一次 START
                end
            end
        end
    end

    // ======== 时钟 50 MHz ========
    always #10 clk = ~clk;

    // ======== AXI 写入任务 ========
    task axi_write;
        input [31:0] addr;
        input [31:0] data;
        begin
            @(posedge clk);
            awaddr <= addr; awvalid <= 1'b1;
            wdata  <= data;  wvalid  <= 1'b1;
            wstrb  <= 4'hF;  bready  <= 1'b1;
            wait (awready && wready);
            @(posedge clk);
            awvalid <= 1'b0; wvalid <= 1'b0;
            wait (bvalid);
            @(posedge clk);
            bready <= 1'b0;
        end
    endtask

    // ======== AXI 读取任务 ========
    task axi_read;
        input  [31:0] addr;
        output [31:0] data;
        begin
            @(posedge clk);
            araddr <= addr; arvalid <= 1'b1; rready <= 1'b1;
            wait (arready);
            @(posedge clk);
            arvalid <= 1'b0;
            wait (rvalid);
            data = rdata;
            @(posedge clk);
            rready <= 1'b0;
        end
    endtask

    // ======== 主测试：模拟一次完整 MNIST 推理 ========
    integer errors;
    reg [31:0] rd_val;
    reg [31:0] results[0:9];
    integer    i;
    integer    max_class;
    integer    max_confidence;

    initial begin
        // --- 初始化 ---
        clk <= 1'b0; rst_n <= 1'b0;
        {awaddr, awvalid, wdata, wvalid, wstrb, bready} = 0;
        {araddr, arvalid, rready} = 0;
        errors = 0;

        repeat (5) @(posedge clk);
        rst_n <= 1'b1;
        repeat (2) @(posedge clk);

        $display("==============================================");
        $display("  tb_soc — System-Level Testbench");
        $display("  MNIST Inference: ARM -> AXI -> Accelerator");
        $display("==============================================\n");

        // ===== PHASE 1: 配置 =====
        $display("--- PHASE 1: Configuration ---");

        axi_write(32'h0000_0008, 32'h4000_0000);  // IMG_ADDR
        if (img_base_addr === 32'h4000_0000)
            $display("[PASS] Write IMG_ADDR = 0x40000000");
        else begin
            $display("[FAIL] IMG_ADDR: expected 0x40000000, got 0x%h", img_base_addr);
            errors = errors + 1;
        end

        axi_write(32'h0000_000C, 32'h4001_0000);  // WEIGHT_ADDR
        if (weight_base_addr === 32'h4001_0000)
            $display("[PASS] Write WEIGHT_ADDR = 0x40010000");
        else begin
            $display("[FAIL] WEIGHT_ADDR: expected 0x40010000, got 0x%h", weight_base_addr);
            errors = errors + 1;
        end

        // ===== PHASE 2: 启动推理 =====
        $display("\n--- PHASE 2: Start Inference ---");

        axi_write(32'h0000_0000, 32'h0000_0001);  // CTRL[START]=1
        repeat (2) @(posedge clk);

        // 轮询 STATUS 直到 DONE
        // 最多等 200 次（每次 6 周期 ≈ 2400ns），
        for (i = 0; i < 200; i = i + 1) begin
            axi_read(32'h0000_0004, rd_val);
            if ((i % 10) == 0) begin
                $display("  [POLL] STATUS = 0x%08h (BUSY=%0d DONE=%0d)",
                         rd_val, rd_val[0], rd_val[1]);
            end
            if (rd_val[1]) begin  // DONE = 1
                $display("[PASS] Inference completed (DONE=1) after %0d polls", i+1);
                break;
            end
            repeat (5) @(posedge clk);
        end
        if (!rd_val[1]) begin
            $display("[FAIL] Timeout: DONE never asserted");
            errors = errors + 1;
        end

        // ===== PHASE 3: 读取结果 =====
        $display("\n--- PHASE 3: Read Results ---");

        $display("  Class | Confidence");
        $display("  ------+-----------");
        max_class      = 0;
        max_confidence = 0;
        for (i = 0; i < 10; i = i + 1) begin
            axi_read(32'h0000_0010 + i*4, results[i]);
            $display("    %0d   |   %0d", i, results[i]);
            if (results[i] > max_confidence) begin
                max_confidence = results[i];
                max_class      = i;
            end
        end

        $display("\n  => Predicted digit: %0d (confidence: %0d)", max_class, max_confidence);

        // ===== PHASE 4: 验证结果 =====
        $display("\n--- PHASE 4: Verify Against Golden ---");

        // 金标准（与 stub 的假结果一致）：数字 0 置信度 85 最高
        if (max_class === 0 && max_confidence === 85) begin
            $display("[PASS] Prediction matches golden: digit 0 with confidence 85");
        end else begin
            $display("[FAIL] Prediction mismatch!");
            $display("       Golden: digit 0 (confidence 85)");
            $display("       Got:    digit %0d (confidence %0d)", max_class, max_confidence);
            errors = errors + 1;
        end

        // 检查所有 10 个结果值是否与 stub 一致
        if (results[0] === 85 && results[1] === 3 && results[2] === 8 &&
            results[3] === 15 && results[4] === 2 && results[5] === 4 &&
            results[6] === 12 && results[7] === 70 && results[8] === 1 && results[9] === 5) begin
            $display("[PASS] All 10 result values match golden");
        end else begin
            $display("[FAIL] Result values mismatch");
            errors = errors + 1;
        end

        // ===== 总结 =====
        $display("\n==============================================");
        if (errors == 0) begin
            $display("  [PASS] System-level test PASSED!");
        end else begin
            $display("  [FAIL] %0d check(s) failed!", errors);
        end
        $display("==============================================");
        // 多等一会儿让波形完整（仿真默认 1000ns 不够，50MHz 下需要更长）
        #10000;
        $finish;
    end

endmodule
