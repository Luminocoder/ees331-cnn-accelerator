`timescale 1ns / 1ps

module pool_engine_tb;

    localparam CLK_PERIOD = 20;

    logic clk = 0;
    logic rst_n = 1;
    logic pool_start;
    logic pool_done;

    logic        fm_b_we;
    logic [13:0] fm_b_addr;
    logic [7:0]  fm_b_wdata;
    logic [7:0]  fm_b_rdata;
    
    logic        fm_a_we;
    logic [13:0] fm_a_addr;
    logic [7:0]  fm_a_wdata;
    logic [7:0]  fm_a_rdata;

    logic [7:0]  ifm_size = 28;
    logic [4:0]  ifm_ch = 1;
    logic [13:0] ifm_base_addr = 14'h0000;
    logic [13:0] ofm_base_addr = 14'h2000;

    pool_engine u_dut (.*);

    fm_bram #( .ADDR_WIDTH(14) ) u_fm_bram (
        .clk(clk),
        .a_we(fm_a_we), .a_addr(fm_a_addr), .a_wdata(fm_a_wdata), .a_rdata(fm_a_rdata),
        .b_we(fm_b_we), .b_addr(fm_b_addr), .b_wdata(fm_b_wdata), .b_rdata(fm_b_rdata)
    );

    initial forever #(CLK_PERIOD/2) clk = ~clk;

    task bram_write(input logic [15:0] addr, input logic [7:0] data);
        @(posedge clk);
        fm_a_we <= 1; fm_a_addr <= addr[13:0]; fm_a_wdata <= data;
        @(posedge clk);
        fm_a_we <= 0;
    endtask

    initial begin
        fm_a_we = 0; pool_start = 0;
        
        // Write pattern: 1, 2, 3, 4 repeated
        // Max should be 4
        for (int y = 0; y < 28; y++) begin
            for (int x = 0; x < 28; x++) begin
                int val = (y%2)*2 + (x%2) + 1; // values 1, 2, 3, 4
                bram_write(ifm_base_addr + y*28 + x, val);
            end
        end

        @(posedge clk);
        rst_n <= 0;
        repeat(2) @(posedge clk);
        rst_n <= 1;
        @(posedge clk);

        $display("TB: Issuing pool_start pulse...");
        pool_start <= 1;
        @(posedge clk);
        pool_start <= 0;

        wait(pool_done == 1);
        $display("TB: pool_done received!");
        @(posedge clk);

        // Verification (expect all 4)
        begin
            logic [7:0] read_val;
            int errors = 0;
            for (int i = 0; i < 14*14; i++) begin
                @(posedge clk);
                fm_a_addr <= ofm_base_addr + i;
                @(posedge clk);
                @(posedge clk);
                read_val = fm_a_rdata;
                if (read_val !== 4) begin
                    $error("TB: Mismatch at OFM(%0d)! Got %d, Expected 4", i, read_val);
                    errors++;
                end
            end
            if (errors == 0) $display("TB: [PASS] Pool Engine verified successfully!");
            else $display("TB: [FAIL] %0d errors found.", errors);
        end
        
        $finish;
    end
endmodule
