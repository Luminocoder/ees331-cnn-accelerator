`timescale 1ns / 1ps

// ============================================================================
// Testbench: conv_bias_tb — 专项验证 conv_engine 的 bias 组装 + S 移位
// 场景: 输入/权重全 0 → accum=0，输出 = clamp(bias >>> S, 0, 127)
//   - bias = 0x00000005 (大端字节 00 00 00 05)，S=0 → 期望输出 5
//     （若字节序或采集错位，会得到大数 → clamp 127 → 立刻暴露）
//   - 同一 bias，S=2 → 期望输出 1（验证算术右移路径）
// ============================================================================

module conv_bias_tb;

    localparam CLK_PERIOD = 20;
    logic clk = 0;
    always #(CLK_PERIOD/2) clk = ~clk;

    logic        rst_n = 1, conv_start = 0, conv_done;
    logic        fm_b_we, fm_a_we;
    logic [13:0] fm_b_addr, fm_a_addr;
    logic [7:0]  fm_b_wdata, fm_a_wdata, fm_b_rdata, fm_a_rdata;
    logic [15:0] w_b_addr, w_a_addr;
    logic [7:0]  w_b_rdata, w_a_rdata, w_a_wdata;
    logic        w_a_we;

    logic [7:0]  ifm_size = 4;
    logic [4:0]  ifm_ch = 1, ofm_ch = 2;
    logic [13:0] ifm_base_addr = 14'h0000, ofm_base_addr = 14'h2000;
    logic [15:0] w_base_addr = 16'h0000, bias_base_addr = 16'd52040;
    logic [3:0]  S_layer = 4'd0;

    conv_engine u_dut (
        .clk(clk), .rst_n(rst_n),
        .conv_start(conv_start), .conv_done(conv_done),
        .fm_b_we(fm_b_we), .fm_b_addr(fm_b_addr), .fm_b_wdata(fm_b_wdata), .fm_b_rdata(fm_b_rdata),
        .w_b_addr(w_b_addr), .w_b_rdata(w_b_rdata),
        .ifm_size(ifm_size), .ifm_ch(ifm_ch), .ofm_ch(ofm_ch),
        .ifm_base_addr(ifm_base_addr), .ofm_base_addr(ofm_base_addr),
        .w_base_addr(w_base_addr), .bias_base_addr(bias_base_addr), .S_layer(S_layer)
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
            conv_start <= 1;
            wait (conv_done == 0);   // 确认本轮已被接受（上一轮的 done 电平先清除）
            @(posedge clk);
            conv_start <= 0;
            wait (conv_done == 1);   // 等本轮真正完成
            @(posedge clk);
        end
    endtask

    integer errors = 0;
    logic [7:0] rd;

    // 监控写回（定位 oc1 平面问题）
    logic dbg_on = 0;
    always @(posedge clk) begin
        if (dbg_on && (fm_b_we || w_b_addr >= 52040))
            $display("  MON t=%0t state=%0d oc=%0d fm_we=%0d fm_addr=%0d fm_wd=%0d wb_addr=%0d bias=%0d",
                     $time, u_dut.state, u_dut.oc_cnt, fm_b_we, fm_b_addr, fm_b_wdata,
                     w_b_addr, u_dut.bias_reg);
    end

    initial begin
        fm_a_we = 0; w_a_we = 0;

        // 输入/权重全 0；bias 大端：oc0=5, oc1=7（逐通道不同）
        for (int i = 0; i < 16; i++) bram_write(1, ifm_base_addr + i, 8'd0);
        for (int i = 0; i < 18; i++) bram_write(0, w_base_addr + i, 8'd0);  // oc0+oc1 两通道权重区
        bram_write(0, bias_base_addr + 0, 8'h00);
        bram_write(0, bias_base_addr + 1, 8'h00);
        bram_write(0, bias_base_addr + 2, 8'h00);
        bram_write(0, bias_base_addr + 3, 8'h05);
        bram_write(0, bias_base_addr + 4, 8'h00);
        bram_write(0, bias_base_addr + 5, 8'h00);
        bram_write(0, bias_base_addr + 6, 8'h00);
        bram_write(0, bias_base_addr + 7, 8'h07);

        @(posedge clk);
        rst_n <= 0;
        repeat (2) @(posedge clk);
        rst_n <= 1;
        @(posedge clk);

        // ---- TEST A: S=0 → oc0 输出 5，oc1 输出 7（逐通道 bias 重取路径） ----
        S_layer <= 4'd0;
        @(posedge clk);
        dbg_on <= 1;
        run_once;
        dbg_on <= 0;
        bram_read(1, ofm_base_addr + 0, rd);
        if (rd === 8'h05)
            $display("[PASS] TEST A: oc0 bias=5 -> output 5 (byte order OK)");
        else begin
            $display("[FAIL] TEST A: oc0 got %0d, expected 5", rd);
            errors++;
        end
        bram_read(1, ofm_base_addr + 16, rd);   // oc1 平面在 16 字节之后 (4x4)
        if (rd === 8'h07)
            $display("[PASS] TEST A2: oc1 bias=7 -> output 7 (per-channel refetch OK)");
        else begin
            $display("[FAIL] TEST A2: oc1 got %0d, expected 7", rd);
            errors++;
        end

        // ---- TEST B: S=2 → (5)>>>2 = 1, (7)>>>2 = 1 ----
        S_layer <= 4'd2;
        @(posedge clk);
        run_once;
        bram_read(1, ofm_base_addr + 0, rd);
        if (rd === 8'h01)
            $display("[PASS] TEST B: bias=5 S=2 -> output 1 (arithmetic shift OK)");
        else begin
            $display("[FAIL] TEST B: got %0d, expected 1", rd);
            errors++;
        end
        bram_read(1, ofm_base_addr + 16, rd);
        if (rd === 8'h01)
            $display("[PASS] TEST B2: oc1 bias=7 S=2 -> output 1");
        else begin
            $display("[FAIL] TEST B2: got %0d, expected 1", rd);
            errors++;
        end

        if (errors == 0)
            $display("[PASS] conv_bias: all tests passed");
        else
            $display("[FAIL] conv_bias: %0d test(s) failed", errors);
        $finish;
    end

    initial begin
        #100000;
        $fatal(1,"conv_bias watchdog state=%0d start=%0b done=%0b size=%0d ic=%0d oc=%0d load=%0d fmvalid=%0b",
            u_dut.state,conv_start,conv_done,u_dut.size_reg,u_dut.in_channels,u_dut.out_channels,u_dut.load_count,u_dut.fm_valid);
    end

endmodule
