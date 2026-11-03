`timescale 1ns / 1ps

// ============================================================================
// Testbench: mnist_soc_top_tb — SoC 级端到端验证（方案 A）
//
// 模拟 ARM 通过 AXI-Lite 走完整流程（与真 FPGA 完全一致）：
//   写 IMG_PTR=0 → 写 IMG_DATA×784（图片字节）→ 写 CTRL=START
//   → 轮询 STATUS.DONE → 读 RESULT_0~9 → 与金标准逐位比对
//
// 覆盖：axi_if 图片写入通路 + 控制通路 + cnn_accel_top（权重烘焙）
// 20 张图全量。
// ============================================================================

module mnist_soc_top_tb;

    localparam CLK_PERIOD = 20;
    logic clk = 0;
    always #(CLK_PERIOD/2) clk = ~clk;

    logic        rst_n = 1;
    logic        irq_done;

    // AXI Master（模拟 ARM）
    reg  [31:0] awaddr;  reg awvalid;  wire awready;
    reg  [31:0] wdata;   reg wvalid;   wire wready;
    reg  [3:0]  wstrb;
    wire [1:0]  bresp;   wire bvalid;  reg bready;
    reg  [31:0] araddr;  reg arvalid;  wire arready;
    wire [31:0] rdata;   wire [1:0] rresp;  wire rvalid;  reg rready;

    // 寄存器地址
    localparam CTRL   = 32'h00;
    localparam STATUS = 32'h04;
    localparam RES0   = 32'h10;
    localparam IMGDATA= 32'h40;
    localparam IMGPTR = 32'h44;

    mnist_soc_top u_dut (
        .clk(clk), .rst_n(rst_n),
        .s_axi_awaddr(awaddr), .s_axi_awvalid(awvalid), .s_axi_awready(awready),
        .s_axi_wdata(wdata),   .s_axi_wstrb(wstrb),     .s_axi_wvalid(wvalid),  .s_axi_wready(wready),
        .s_axi_bresp(bresp),   .s_axi_bvalid(bvalid),    .s_axi_bready(bready),
        .s_axi_araddr(araddr), .s_axi_arvalid(arvalid),  .s_axi_arready(arready),
        .s_axi_rdata(rdata),   .s_axi_rresp(rresp),      .s_axi_rvalid(rvalid),  .s_axi_rready(rready),
        .irq_done(irq_done)
    );

    // 数据载入
    reg [7:0]  img_all [0:15679];
    reg [31:0] golden  [0:199];
    reg [7:0]  labels  [0:19];
    reg [7:0] extra_pixels [0:3919];
    reg [31:0] extra_scores [0:49];
    reg [7:0] extra_labels [0:4];
    string profile_suite="frozen";
    initial begin
        $readmemh("ref_model/test_vectors/img_all.hex", img_all);
        $readmemh("ref_model/test_vectors/golden_all.hex", golden);
        labels[0]=5; labels[1]=0; labels[2]=4; labels[3]=1; labels[4]=9;
        labels[5]=2; labels[6]=1; labels[7]=3; labels[8]=4; labels[9]=3;
        labels[10]=5; labels[11]=6; labels[12]=7; labels[13]=2; labels[14]=8;
        labels[15]=6; labels[16]=9; labels[17]=0; labels[18]=7; labels[19]=8;
    end

    // AXI 写
    task axi_write(input [31:0] addr, input [31:0] data);
        begin
            @(posedge clk);
            awaddr <= addr; awvalid <= 1'b1;
            wdata <= data; wvalid <= 1'b1; wstrb <= 4'hF; bready <= 1'b1;
            wait (awready && wready);
            @(posedge clk);
            awvalid <= 1'b0; wvalid <= 1'b0;
            wait (bvalid);
            @(posedge clk);
            bready <= 1'b0;
        end
    endtask

    // AXI 读
    task axi_read(input [31:0] addr, output [31:0] data);
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

    integer errors = 0;
    integer i, img_idx;
    reg [31:0] rd;

    // 跑一张图
    integer profile_layers[0:5];
    integer profile_busy;
    reg profile_collect=0;
    always @(posedge clk) begin
        if (profile_collect && u_dut.accel_busy) begin
            profile_busy = profile_busy + 1;
            profile_layers[u_dut.u_accel.layer_id] = profile_layers[u_dut.u_accel.layer_id] + 1;
        end
    end
    task predict_image(input integer img_idx, input bit soft_reset_before_start = 0);
        integer timeout_count;
        begin
            // 写图（IMG_PTR 重置 + IMG_DATA×784）
            axi_write(IMGPTR, 32'h0);
            for (i = 0; i < 784; i++)
                axi_write(IMGDATA, {24'h0, img_all[img_idx*784 + i]} >> 1);
            if (soft_reset_before_start)
                axi_write(CTRL, 32'h2);
            for(integer pi=0;pi<6;pi++) profile_layers[pi]=0;
            profile_busy=0;profile_collect=1;
            // 启动
            axi_write(CTRL, 32'h1);
            // 轮询 DONE
            begin
                timeout_count = 0;
                rd = 0;
                while (!(rd & 32'h2) && timeout_count < 10000000) begin
                    axi_read(STATUS, rd);
                    timeout_count++;
                end
                if (!(rd & 32'h2))
                    $fatal(1, "[FAIL] SoC inference timeout img%02d", img_idx);
            end
            profile_collect=0;
            $display("PROFILE suite=%s img=%0d busy_cycles=%0d conv1=%0d pool1=%0d conv2=%0d pool2=%0d fc1=%0d fc2=%0d clock_ns=%0d",profile_suite,img_idx,
                profile_busy,profile_layers[0],profile_layers[1],profile_layers[2],profile_layers[3],profile_layers[4],profile_layers[5],CLK_PERIOD);
        end
    endtask

    task check_image(input integer img_idx, input bit reset_recovery = 0);
        begin
            integer rv[0:9], mismatch, mx, mv, j;
            for (j = 0; j < 10; j++) begin
                axi_read(RES0 + j*4, rd);
                rv[j] = rd;
            end
            mismatch = 0;
            for (j = 0; j < 10; j++)
                if (rv[j] !== golden[img_idx*10 + j]) mismatch = mismatch + 1;
            mx = 0; mv = rv[0];
            for (j = 1; j < 10; j++) if (rv[j] > mv) begin mv = rv[j]; mx = j; end
            if (mismatch == 0 && mx == labels[img_idx]) begin
                if (reset_recovery)
                    $display("[PASS] SoC reset recovery: soft reset preserves image, 10/10 bit-exact");
                else
                    $display("  [PASS] img%02d: label=%0d argmax=%0d (10/10 bit-exact)", img_idx, labels[img_idx], mx);
            end else begin
                $display("  [FAIL] img%02d: label=%0d argmax=%0d mismatch=%0d", img_idx, labels[img_idx], mx, mismatch);
                errors = errors + 1;
            end
        end
    endtask

    initial begin
        awaddr=0; awvalid=0; wdata=0; wvalid=0; wstrb=0; bready=0;
        araddr=0; arvalid=0; rready=0;
        rst_n = 0;
        repeat (5) @(posedge clk);
        rst_n = 1;
        repeat (2) @(posedge clk);

        $display("===== mnist_soc_top SoC 级端到端验证 (20 张) =====");

        if ($test$plusargs("RESET_STRESS")) begin
            profile_suite="reset";
            axi_write(IMGPTR, 32'h0);
            for (i = 0; i < 784; i++)
                axi_write(IMGDATA, {24'h0, img_all[i]} >> 1);
            axi_write(CTRL, 32'h1);
            repeat (2000) @(negedge clk);
            if (!u_dut.accel_busy)
                $fatal(1, "[FAIL] SoC reset fixture did not reach active inference");
            #3; rst_n = 0;
            repeat (5) @(negedge clk);
            if (bvalid || rvalid || u_dut.accel_busy || u_dut.fm_a_we)
                $fatal(1, "[FAIL] SoC held-reset state is not idle");
            rst_n = 1;
            repeat (2) @(negedge clk);
            predict_image(0, 1);
            check_image(0, 1);
        end

        profile_suite="frozen";
        for (img_idx = 0; img_idx < 20; img_idx++) begin
            predict_image(img_idx);
            check_image(img_idx);
        end

        if ($test$plusargs("EXTRA_INPUTS")) begin
            profile_suite="extra";
            $readmemh("ref_model/test_vectors/conv_extra_images.hex",extra_pixels);
            $readmemh("ref_model/test_vectors/conv_extra_logits.hex",extra_scores);
            $readmemh("ref_model/test_vectors/conv_extra_labels.hex",extra_labels);
            for(integer e=0;e<3920;e=e+1) img_all[e]=extra_pixels[e];
            for(integer e=0;e<50;e=e+1) golden[e]=extra_scores[e];
            for(integer e=0;e<5;e=e+1) labels[e]=extra_labels[e];
            for(img_idx=0;img_idx<5;img_idx=img_idx+1) begin
                predict_image(img_idx);check_image(img_idx);
            end
            if(errors==0) $display("[PASS] mnist_soc_top: 5/5 extra inputs, 50/50 exact logits");
        end

        $display("----------------------------------------------");
        if (errors == 0)
            $display("[PASS] mnist_soc_top: 20/20 bit-exact (AXI 通路)");
        else
            $fatal(1,"[FAIL] mnist_soc_top: %0d image(s) mismatched", errors);
        $finish;
    end

endmodule
