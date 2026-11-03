`timescale 1ns / 1ps

module fc_engine_tb;

    localparam CLK_PERIOD = 20;

    logic clk = 0;
    logic rst_n = 1;
    logic fc_start;
    logic fc_done;

    logic        fm_b_we;
    logic [13:0] fm_b_addr;
    logic [7:0]  fm_b_wdata;
    logic [7:0]  fm_b_rdata;
    
    logic        fm_a_we;
    logic [13:0] fm_a_addr;
    logic [7:0]  fm_a_wdata;
    logic [7:0]  fm_a_rdata;

    logic [15:0] w_b_addr;
    logic [7:0]  w_b_rdata;
    
    logic        w_a_we;
    logic [15:0] w_a_addr;
    logic [7:0]  w_a_wdata;
    logic [7:0]  w_a_rdata;

    logic        out_we;
    logic [3:0]  out_addr;
    logic [31:0] out_wdata;

    logic [15:0] ifm_len = 256;
    logic [15:0] ofm_len = 64;
    logic [13:0] ifm_base_addr = 14'h0000;
    logic [13:0] ofm_base_addr = 14'h2000;
    logic [15:0] w_base_addr = 16'h0000;
    logic [15:0] bias_base_addr = 16'd52136; // FC1 bias base
    logic [3:0]  S_layer = 4'd0;
    logic        is_fc2 = 0;

    fc_engine u_dut (.*);

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

    initial begin
        fm_a_we = 0; w_a_we = 0; fc_start = 0;
        
        // Write ones to inputs and weights
        for (int i = 0; i < 256; i++) bram_write(1, ifm_base_addr + i, 8'd1);
        for (int i = 0; i < 256*64; i++) bram_write(0, w_base_addr + i, 8'd1);
        
        // Write zero bias
        for (int i = 0; i < 64*4; i++) bram_write(0, bias_base_addr + i, 8'h00);

        @(posedge clk);
        rst_n <= 0;
        repeat(2) @(posedge clk);
        rst_n <= 1;
        @(posedge clk);

        $display("TB: Issuing fc_start pulse...");
        fc_start <= 1;
        @(posedge clk);
        fc_start <= 0;

        wait(fc_done == 1);
        $display("TB: fc_done received!");
        @(posedge clk);

        // Verification (expect 256 * 1 * 1 = 256, but clamped to 127 for FC1)
        begin
            logic [7:0] read_val;
            int errors = 0;
            for (int i = 0; i < 64; i++) begin
                @(posedge clk);
                fm_a_addr <= ofm_base_addr + i;
                @(posedge clk);
                @(posedge clk);
                read_val = fm_a_rdata;
                if (read_val !== 127) begin
                    $error("TB: Mismatch at OFM(%0d)! Got %d, Expected 127", i, read_val);
                    errors++;
                end
            end
            if (errors == 0) $display("TB: [PASS] FC Engine verified successfully for FC1!");
            else $display("TB: [FAIL] %0d errors found.", errors);
        end
        
        $finish;
    end
endmodule
