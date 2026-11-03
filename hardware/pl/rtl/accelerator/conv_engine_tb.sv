`timescale 1ns / 1ps

module conv_engine_tb;

    localparam CLK_PERIOD = 20;

    logic clk = 0;
    logic rst_n = 1;
    logic conv_start;
    logic conv_done;

    logic        fm_b_we;
    logic [13:0] fm_b_addr;
    logic [7:0]  fm_b_wdata;
    logic [7:0]  fm_b_rdata;
    logic [15:0] w_b_addr;
    logic [7:0]  w_b_rdata;

    logic        fm_a_we;
    logic [13:0] fm_a_addr;
    logic [7:0]  fm_a_wdata;
    logic [7:0]  fm_a_rdata;
    logic        w_a_we;
    logic [15:0] w_a_addr;
    logic [7:0]  w_a_wdata;
    logic [7:0]  w_a_rdata;

    logic [7:0]  ifm_size = 28;
    logic [4:0]  ifm_ch = 1;
    logic [4:0]  ofm_ch = 1;
    logic [13:0] ifm_base_addr = 14'h0000;
    logic [13:0] ofm_base_addr = 14'h2000;
    logic [15:0] w_base_addr = 16'h0000;
    logic [15:0] bias_base_addr = 16'd52040;
    logic [3:0]  S_layer = 4'd0; // 设置为0以便兼容之前的全1测试

    conv_engine u_dut (
        .clk(clk),
        .rst_n(rst_n),
        .conv_start(conv_start),
        .conv_done(conv_done),
        .fm_b_we(fm_b_we),
        .fm_b_addr(fm_b_addr),
        .fm_b_wdata(fm_b_wdata),
        .fm_b_rdata(fm_b_rdata),
        .w_b_addr(w_b_addr),
        .w_b_rdata(w_b_rdata),
        .ifm_size(ifm_size),
        .ifm_ch(ifm_ch),
        .ofm_ch(ofm_ch),
        .ifm_base_addr(ifm_base_addr),
        .ofm_base_addr(ofm_base_addr),
        .w_base_addr(w_base_addr),
        .bias_base_addr(bias_base_addr),
        .S_layer(S_layer)
    );

    fm_bram #( .ADDR_WIDTH(14) ) u_fm_bram (
        .clk(clk),
        .a_we(fm_a_we), .a_addr(fm_a_addr), .a_wdata(fm_a_wdata), .a_rdata(fm_a_rdata),
        .b_we(fm_b_we), .b_addr(fm_b_addr), .b_wdata(fm_b_wdata), .b_rdata(fm_b_rdata)
    );
    weight_bram #( .ADDR_WIDTH(16) ) u_w_bram (
        .clk(clk),
        .a_we(w_a_we), .a_addr(w_a_addr), .a_wdata(w_a_wdata), .a_rdata(w_a_rdata),
        .b_addr(w_b_addr), .b_rdata(w_b_rdata)
    );

    initial forever #(CLK_PERIOD/2) clk = ~clk;

    task bram_write(input logic is_fm, input logic [15:0] addr, input logic [7:0] data);
        @(posedge clk);
        if (is_fm) begin fm_a_we <= 1; fm_a_addr <= addr[13:0]; fm_a_wdata <= data; end 
        else       begin w_a_we <= 1; w_a_addr <= addr; w_a_wdata <= data; end
        @(posedge clk);
        fm_a_we <= 0; w_a_we <= 0;
    endtask

    task bram_read(input logic is_fm, input logic [15:0] addr, output logic [7:0] data);
        @(posedge clk);
        if (is_fm) fm_a_addr <= addr[13:0];
        else       w_a_addr <= addr;
        @(posedge clk);
        @(posedge clk);
        if (is_fm) data = fm_a_rdata;
        else       data = w_a_rdata;
    endtask

    initial begin
        logic [7:0] read_val;
        integer errors = 0;
        int valid_pixels;
        int expected_sum;

        fm_a_we = 0; w_a_we = 0; conv_start = 0;
        
        // 灌入测试数据：全 1
        for (int i = 0; i < 28*28*1; i++) bram_write(1, ifm_base_addr + i, 8'd1);
        for (int i = 0; i < 1*1*9; i++) bram_write(0, w_base_addr + i, 8'd1);
        
        // 灌入 Bias 数据（测试用，写入 0）
        for (int i = 0; i < 4; i++) bram_write(0, bias_base_addr + i, 8'h00);

        @(posedge clk);
        rst_n <= 0;
        repeat(2) @(posedge clk);
        rst_n <= 1;
        @(posedge clk);

        $display("TB: Issuing conv_start pulse...");
        conv_start <= 1;
        @(posedge clk);
        conv_start <= 0;

        // 等待整个卷积层计算完成 (因为是串行计算，这里需要等待相当长的一段时间)
        wait(conv_done == 1);
        $display("TB: conv_done received! Checking results...");
        @(posedge clk);

        // 验证结果：动态计算 Padding 预期值
        for (int c = 0; c < ofm_ch; c++) begin
            for (int y = 0; y < ifm_size; y++) begin
                for (int x = 0; x < ifm_size; x++) begin
                    
                    // 硬件有 Padding 逻辑，边缘像素的预期和会变小
                    valid_pixels = 0;
                    for(int ky=0; ky<3; ky++) begin
                        for(int kx=0; kx<3; kx++) begin
                            int iy = y + ky - 1;
                            int ix = x + kx - 1;
                            if (iy >= 0 && iy < ifm_size && ix >= 0 && ix < ifm_size) 
                                valid_pixels++;
                        end
                    end
                    
                    // sum = valid_pixels * (data=1) * (weight=1) * channels
                    expected_sum = valid_pixels * ifm_ch; 
                    if (expected_sum > 127) expected_sum = 127; // ReLU Clamp

                    // 读取硬件写回的结果
                    bram_read(1, ofm_base_addr + c*ifm_size*ifm_size + y*ifm_size + x, read_val);
                    
                    if (read_val != expected_sum) begin
                        $error("TB: Mismatch at OFM(c=%0d, y=%0d, x=%0d)! Got %d, Expected %d",
                               c, y, x, read_val, expected_sum);
                        errors++;
                    end
                end
            end
        end

        if (errors == 0) $display("TB: [PASS] Convolution Engine verified successfully! Padding and ReLU are correct!");
        else $display("TB: [FAIL] %0d errors found in convolution result.", errors);

        $finish;
    end

endmodule
