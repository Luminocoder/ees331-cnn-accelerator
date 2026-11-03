`timescale 1ns / 1ps

// ============================================================================
// Testbench: fc_bias_tb — 专项验证 fc_engine 的 bias 组装 / S 移位 / FC2 路径
// 场景: ifm_len=4, ofm_len=2, 输入全 1, 权重全 1 → 每输出 accum=4
//   - bias = 0x00000003，S=2，is_fc2=0 → (4+3)>>>2 = 1 → fm 输出 1
//   - 同一配置 is_fc2=1 → out_wdata = 4+3 = 7（INT32 不移位）
// ============================================================================

module fc_bias_tb;

    localparam CLK_PERIOD = 20;
    logic clk = 0;
    always #(CLK_PERIOD/2) clk = ~clk;

    logic        rst_n = 1, fc_start = 0, fc_done;
    logic        fm_b_we, fm_a_we;
    logic [13:0] fm_b_addr, fm_a_addr;
    logic [7:0]  fm_b_wdata, fm_a_wdata, fm_b_rdata, fm_a_rdata;
    logic [15:0] w_b_addr, w_a_addr;
    logic [7:0]  w_b_rdata, w_a_rdata, w_a_wdata;
    logic        w_a_we;

    logic        out_we;
    logic [3:0]  out_addr;
    logic [31:0] out_wdata;

    logic [15:0] ifm_len = 16'd4, ofm_len = 16'd2;
    logic [13:0] ifm_base_addr = 14'h0000, ofm_base_addr = 14'h2000;
    logic [15:0] w_base_addr = 16'h0000, bias_base_addr = 16'd52040;
    logic [3:0]  S_layer = 4'd2;
    logic        is_fc2 = 1'b0;

    fc_engine u_dut (
        .clk(clk), .rst_n(rst_n),
        .fc_start(fc_start), .fc_done(fc_done),
        .fm_b_we(fm_b_we), .fm_b_addr(fm_b_addr), .fm_b_wdata(fm_b_wdata), .fm_b_rdata(fm_b_rdata),
        .w_b_addr(w_b_addr), .w_b_rdata(w_b_rdata),
        .out_we(out_we), .out_addr(out_addr), .out_wdata(out_wdata),
        .ifm_len(ifm_len), .ofm_len(ofm_len),
        .ifm_base_addr(ifm_base_addr), .ofm_base_addr(ofm_base_addr),
        .w_base_addr(w_base_addr), .bias_base_addr(bias_base_addr),
        .S_layer(S_layer), .is_fc2(is_fc2)
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

    task bram_write(input logic is_fm, input logic [15:0] addr, input logic [7:0] data);
        begin
            @(posedge clk);
            if (is_fm) begin fm_a_we <= 1; fm_a_addr <= addr[13:0]; fm_a_wdata <= data; end
            else       begin w_a_we <= 1; w_a_addr <= addr; w_a_wdata <= data; end
            @(posedge clk);
            fm_a_we <= 0; w_a_we <= 0;
        end
    endtask

    task bram_read(input logic is_fm, input logic [15:0] addr, output logic [7:0] data);
        begin
            @(posedge clk);
            if (is_fm) fm_a_addr <= addr[13:0];
            else       w_a_addr <= addr;
            @(posedge clk); @(posedge clk);
            if (is_fm) data = fm_a_rdata;
            else       data = w_a_rdata;
        end
    endtask

    task run_once;
        begin
            @(posedge clk);
            fc_start <= 1;
            wait (fc_done == 0);   // 确认本轮已被接受（上一轮的 done 电平先清除）
            @(posedge clk);
            fc_start <= 0;
            wait (fc_done == 1);   // 等本轮真正完成
            @(posedge clk);
        end
    endtask

    integer errors = 0;
    logic [7:0] rd;

    // 监控第二段运行期间 bias 取数（在 TEST B 时打开）
    logic dbg_on = 0;
    always @(posedge clk) begin
        if (dbg_on && w_b_addr >= 52040)
            $display("  MON t=%0t state=%0d wb_addr=%0d wb_rdata=%0d bias=%0d",
                     $time, u_dut.state, w_b_addr, w_b_rdata, u_dut.bias_reg);
    end

    initial begin
        fm_a_we = 0; w_a_we = 0;

        for (int i = 0; i < 4; i++) bram_write(1, ifm_base_addr + i, 8'd1);
        for (int i = 0; i < 8; i++) bram_write(0, w_base_addr + i, 8'd1);
        // bias 大端：oc0 = 00 00 00 03，oc1 = 00 00 00 05（逐通道不同值）
        bram_write(0, bias_base_addr + 0, 8'h00);
        bram_write(0, bias_base_addr + 1, 8'h00);
        bram_write(0, bias_base_addr + 2, 8'h00);
        bram_write(0, bias_base_addr + 3, 8'h03);
        bram_write(0, bias_base_addr + 4, 8'h00);
        bram_write(0, bias_base_addr + 5, 8'h00);
        bram_write(0, bias_base_addr + 6, 8'h00);
        bram_write(0, bias_base_addr + 7, 8'h05);

        @(posedge clk);
        rst_n <= 0;
        repeat (2) @(posedge clk);
        rst_n <= 1;
        @(posedge clk);

        // ---- TEST A: FC1 模式 (is_fc2=0), S=2 → oc0=(4+3)>>>2=1, oc1=(4+5)>>>2=2 ----
        run_once;
        bram_read(1, ofm_base_addr + 0, rd);
        if (rd === 8'h01)
            $display("[PASS] TEST A: FC1 mode oc0 -> output 1 (bias+S OK)");
        else begin
            $display("[FAIL] TEST A: oc0 got %0d, expected 1", rd);
            errors++;
        end
        bram_read(1, ofm_base_addr + 1, rd);
        if (rd === 8'h02)
            $display("[PASS] TEST A2: FC1 mode oc1 -> output 2 (per-channel bias OK)");
        else begin
            $display("[FAIL] TEST A2: oc1 got %0d, expected 2", rd);
            errors++;
        end

        // ---- TEST B: FC2 模式 (is_fc2=1) → out_wdata = 4+5 = 9 不移位 ----
        is_fc2 <= 1'b1;
        @(posedge clk);
        dbg_on <= 1;
        run_once;
        dbg_on <= 0;
        if (out_wdata === 32'd9)
            $display("[PASS] TEST B: FC2 mode -> out_wdata 9 (INT32 unshifted)");
        else begin
            $display("[FAIL] TEST B: got %0d, expected 9", out_wdata);
            errors++;
        end

        if (errors == 0)
            $display("[PASS] fc_bias: all tests passed");
        else
            $display("[FAIL] fc_bias: %0d test(s) failed", errors);
        $finish;
    end

endmodule
