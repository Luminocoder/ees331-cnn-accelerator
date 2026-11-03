```systemverilog
`timescale 1ns / 1ps

module mac_array_tb;

    localparam DATA_WIDTH   = 8;
    localparam ACCUM_WIDTH  = 32;
    localparam PARALLELISM  = 8;
    localparam CLK_PERIOD   = 20;

    logic clk = 0;
    logic rst_n = 1; 
    logic signed [DATA_WIDTH-1:0]   data_in   [0:PARALLELISM-1];
    logic signed [DATA_WIDTH-1:0]   weight_in [0:PARALLELISM-1];
    logic signed [ACCUM_WIDTH-1:0]  accum_out;

    mac_array #(
        .DATA_WIDTH(DATA_WIDTH),
        .ACCUM_WIDTH(ACCUM_WIDTH),
        .PARALLELISM(PARALLELISM)
    ) dut (
        .clk(clk),
        .rst_n(rst_n),
        .data_in(data_in),       // 优化: 直接连接数组
        .weight_in(weight_in),   // 优化: 直接连接数组
        .accum_out(accum_out)
    );

    // ... (Testbench 的其余部分保持不变) ...
    initial begin
        forever #(CLK_PERIOD / 2) clk = ~clk;
    end

    initial begin
        $display("TB: Starting simulation...");
        
        @(posedge clk);
        rst_n <= 0;
        repeat(2) @(posedge clk);
        rst_n <= 1;
        @(posedge clk);
        
        $display("TB: DUT is out of reset. Starting tests.");

        $display("TB: Test Case 1: All positive numbers");
        for (int i = 0; i < PARALLELISM; i++) begin
            data_in[i]   <= i + 1;
            weight_in[i] <= i + 2;
        end
        check_result(240, 3);

        $display("TB: Test Case 2: Mixed positive and negative numbers");
        data_in[0] <= 10;    weight_in[0] <= -5;
        data_in[1] <= -2;    weight_in[1] <= 20;
        data_in[2] <= 3;     weight_in[2] <= 3;
        data_in[3] <= 0;     weight_in[3] <= 100;
        data_in[4] <= 127;   weight_in[4] <= 1;
        data_in[5] <= -128;  weight_in[5] <= -1;
        data_in[6] <= 4;     weight_in[6] <= 5;
        data_in[7] <= -6;    weight_in[7] <= -6;
        check_result(230, 3);

        $display("TB: All test cases PASSED!");
        $finish;
    end

    task check_result(input signed [ACCUM_WIDTH-1:0] expected, input int latency);
        repeat(latency) @(posedge clk);
        if (accum_out === expected) begin
            $display("TB: PASSED! Expected: %d, Got: %d", expected, accum_out);
        end else begin
            $error("TB: FAILED! Expected: %d, Got: %d", expected, accum_out);
        end
        @(posedge clk);
    endtask

endmodule