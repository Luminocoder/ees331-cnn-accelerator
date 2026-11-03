`timescale 1ns / 1ps

module axi_if_v2_tb;
    localparam [31:0] AXI_BASE = 32'h43C0_0000;
    localparam [1:0]  AXI_OKAY = 2'b00;
    localparam [1:0]  AXI_SLVERR = 2'b10;

    logic clk = 1'b0;
    logic rst_n = 1'b0;
    always #10 clk = ~clk;

    logic [31:0] awaddr;
    logic        awvalid;
    wire         awready;
    logic [31:0] wdata;
    logic [3:0]  wstrb;
    logic        wvalid;
    wire         wready;
    wire [1:0]   bresp;
    wire         bvalid;
    logic        bready;
    logic [31:0] araddr;
    logic        arvalid;
    wire         arready;
    wire [31:0]  rdata;
    wire [1:0]   rresp;
    wire         rvalid;
    logic        rready;

    wire         accel_start;
    wire         accel_soft_rst;
    logic        accel_busy;
    logic        accel_done;
    logic        accel_error;
    wire         fm_a_we;
    wire [13:0]  fm_a_addr;
    wire [7:0]   fm_a_wdata;
    logic [31:0] result_0, result_1, result_2, result_3, result_4;
    logic [31:0] result_5, result_6, result_7, result_8, result_9;
    logic [31:0] layer_status;

    axi_if_v2 dut (
        .clk(clk), .rst_n(rst_n),
        .s_axi_awaddr(awaddr), .s_axi_awvalid(awvalid), .s_axi_awready(awready),
        .s_axi_wdata(wdata), .s_axi_wstrb(wstrb), .s_axi_wvalid(wvalid), .s_axi_wready(wready),
        .s_axi_bresp(bresp), .s_axi_bvalid(bvalid), .s_axi_bready(bready),
        .s_axi_araddr(araddr), .s_axi_arvalid(arvalid), .s_axi_arready(arready),
        .s_axi_rdata(rdata), .s_axi_rresp(rresp), .s_axi_rvalid(rvalid), .s_axi_rready(rready),
        .accel_start(accel_start), .accel_soft_rst(accel_soft_rst),
        .accel_busy(accel_busy), .accel_done(accel_done), .accel_error(accel_error),
        .fm_a_we(fm_a_we), .fm_a_addr(fm_a_addr), .fm_a_wdata(fm_a_wdata),
        .result_0(result_0), .result_1(result_1), .result_2(result_2),
        .result_3(result_3), .result_4(result_4), .result_5(result_5),
        .result_6(result_6), .result_7(result_7), .result_8(result_8),
        .result_9(result_9), .layer_status(layer_status)
    );

    integer errors = 0;
    integer fm_write_count = 0;
    integer start_count = 0;
    integer soft_rst_count = 0;
    logic [13:0] last_fm_addr;
    logic [7:0]  last_fm_data;
    logic [13:0] memory_read_addr = 0;
    wire [7:0] memory_read_data;
    logic watch_clock_only_outputs = 1'b0;
    time last_clock_edge = 0;
    logic [1:0] wr_resp;
    logic [1:0] rd_resp;
    logic [31:0] rd_data;
    integer writes_before;

    fm_bram memory_sink (
        .clk(clk), .a_we(fm_a_we), .a_addr(fm_a_addr), .a_wdata(fm_a_wdata), .a_rdata(),
        .b_we(1'b0), .b_addr(memory_read_addr), .b_wdata(8'h0), .b_rdata(memory_read_data)
    );

    always @(posedge clk) last_clock_edge = $time;
    always @(fm_a_we or fm_a_addr or fm_a_wdata or accel_soft_rst) begin
        if (watch_clock_only_outputs && $time != last_clock_edge) begin
            $display("[FAIL] ASYNC_BRAM_CONTROL_CHANGE at %0t", $time);
            errors = errors + 1;
        end
    end

    always @(posedge clk) begin
        if (fm_a_we) begin
            fm_write_count <= fm_write_count + 1;
            last_fm_addr <= fm_a_addr;
            last_fm_data <= fm_a_wdata;
        end
        if (accel_start)
            start_count <= start_count + 1;
        if (accel_soft_rst)
            soft_rst_count <= soft_rst_count + 1;
    end

    task automatic check(input logic condition, input string message);
        begin
            if (condition)
                $display("[PASS] %s", message);
            else begin
                $display("[FAIL] %s", message);
                errors = errors + 1;
            end
        end
    endtask

    // Return one nanosecond after the bridge issues the registered side effect.
    task automatic issue_write_pulse(input logic [31:0] addr, input logic [31:0] data);
        begin
            @(negedge clk);
            bready = 1'b0;
            awaddr = addr; awvalid = 1'b1;
            wdata = data; wstrb = 4'hF; wvalid = 1'b1;
            @(posedge clk);
            check(awready && wready, "reset fixture accepts paired AW/W");
            @(negedge clk);
            awvalid = 1'b0; wvalid = 1'b0;
            @(posedge clk);
            #1;
            check(bvalid && bresp == AXI_OKAY, "side effect and B response retain their original cycle");
        end
    endtask

    task automatic hold_and_release_reset;
        begin
            repeat (3) @(negedge clk);
            check(!bvalid && !rvalid && !fm_a_we && !accel_soft_rst,
                  "held reset clears bus valids and clocked side effects");
            rst_n = 1'b1;
            repeat (2) @(negedge clk);
        end
    endtask

    task automatic reset_during_write(input integer offset_ns, input logic [13:0] pointer);
        logic [23:0] stable_outputs;
        integer before_write;
        begin
            axi_write(AXI_BASE + 32'h44, {18'h0, pointer}, 4'hF, 0, wr_resp);
            before_write = fm_write_count;
            memory_read_addr = pointer;
            issue_write_pulse(AXI_BASE + 32'h40, 32'hA5);
            check(fm_a_we && fm_a_addr == pointer && fm_a_wdata == 8'hA5,
                  "pending BRAM write has the original address and data");
            stable_outputs = {fm_a_we, fm_a_addr, fm_a_wdata, accel_soft_rst};
            #(offset_ns - 1);
            rst_n = 1'b0;
            #0.001;
            check({fm_a_we, fm_a_addr, fm_a_wdata, accel_soft_rst} === stable_outputs,
                  "reset assertion cannot change BRAM controls between clock edges");
            check(!bvalid && !rvalid && !accel_start, "AXI response reset remains asynchronous");
            @(posedge clk);
            #1;
            check(!fm_a_we && !accel_soft_rst && fm_a_addr == 0 && fm_a_wdata == 0,
                  "BRAM controls reach reset values on the next rising edge");
            check(fm_write_count == before_write + 1,
                  "already-issued BRAM write completes exactly once with stable inputs");
            hold_and_release_reset();
            check(fm_write_count == before_write + 1, "held reset cannot duplicate a pending BRAM write");
            check(memory_read_data == 8'hA5, "synchronous BRAM readback preserves the issued write across reset");
            axi_write(AXI_BASE + 32'h40, 32'h3C, 4'hF, 0, wr_resp);
            check(last_fm_addr == 0 && last_fm_data == 8'h3C,
                  "fresh image loading after reset restarts at pointer zero");
        end
    endtask

    task automatic reset_half_transaction(input logic address_first);
        integer before_write;
        begin
            before_write = fm_write_count;
            @(negedge clk);
            awaddr = AXI_BASE + 32'h40; wdata = 32'hD7; wstrb = 4'hF;
            awvalid = address_first; wvalid = !address_first;
            @(posedge clk);
            check(address_first ? awready : wready, "reset fixture accepts an unpaired channel");
            @(negedge clk);
            awvalid = 1'b0; wvalid = 1'b0;
            #3; rst_n = 1'b0;
            hold_and_release_reset();
            awvalid = !address_first; wvalid = address_first;
            @(posedge clk);
            @(negedge clk);
            awvalid = 1'b0; wvalid = 1'b0;
            repeat (3) @(negedge clk);
            check(!bvalid && fm_write_count == before_write,
                  "pre-reset AW or W cannot pair with a post-reset channel");
            #3; rst_n = 1'b0;
            hold_and_release_reset();
        end
    endtask

    // order: 0 = simultaneous, 1 = AW first, 2 = W first.
    task automatic axi_write(
        input  logic [31:0] addr,
        input  logic [31:0] data,
        input  logic [3:0]  strb,
        input  integer      order,
        output logic [1:0]  response
    );
        begin
            bready = 1'b0;
            if (order == 0) begin
                @(negedge clk);
                awaddr = addr;
                awvalid = 1'b1;
                wdata = data;
                wstrb = strb;
                wvalid = 1'b1;
                do @(posedge clk); while (!(awready && wready));
                @(negedge clk);
                awvalid = 1'b0;
                wvalid = 1'b0;
            end else if (order == 1) begin
                @(negedge clk);
                awaddr = addr;
                awvalid = 1'b1;
                do @(posedge clk); while (!awready);
                @(negedge clk);
                awvalid = 1'b0;
                repeat (2) @(posedge clk);
                @(negedge clk);
                wdata = data;
                wstrb = strb;
                wvalid = 1'b1;
                do @(posedge clk); while (!wready);
                @(negedge clk);
                wvalid = 1'b0;
            end else begin
                @(negedge clk);
                wdata = data;
                wstrb = strb;
                wvalid = 1'b1;
                do @(posedge clk); while (!wready);
                @(negedge clk);
                wvalid = 1'b0;
                repeat (2) @(posedge clk);
                @(negedge clk);
                awaddr = addr;
                awvalid = 1'b1;
                do @(posedge clk); while (!awready);
                @(negedge clk);
                awvalid = 1'b0;
            end

            wait (bvalid);
            response = bresp;
            repeat (2) begin
                @(posedge clk);
                check(bvalid && bresp == response, "B channel remains stable under backpressure");
            end
            @(negedge clk);
            bready = 1'b1;
            @(posedge clk);
            @(negedge clk);
            bready = 1'b0;
        end
    endtask

    task automatic axi_read(
        input  logic [31:0] addr,
        output logic [31:0] data,
        output logic [1:0]  response
    );
        begin
            rready = 1'b0;
            @(negedge clk);
            araddr = addr;
            arvalid = 1'b1;
            do @(posedge clk); while (!arready);
            @(negedge clk);
            arvalid = 1'b0;
            wait (rvalid);
            data = rdata;
            response = rresp;
            repeat (2) begin
                @(posedge clk);
                check(rvalid && rdata == data && rresp == response,
                      "R channel remains stable under backpressure");
            end
            @(negedge clk);
            rready = 1'b1;
            @(posedge clk);
            @(negedge clk);
            rready = 1'b0;
        end
    endtask

    initial begin
        awaddr = '0; awvalid = 1'b0;
        wdata = '0; wstrb = '0; wvalid = 1'b0;
        bready = 1'b0;
        araddr = '0; arvalid = 1'b0; rready = 1'b0;
        accel_busy = 1'b0; accel_done = 1'b1; accel_error = 1'b0;
        result_0 = 32'h0000_0055; result_1 = 32'h0000_0010;
        result_2 = 32'h0000_0003; result_3 = 32'h0000_0007;
        result_4 = 32'h0000_0001; result_5 = 32'h0000_0002;
        result_6 = 32'h0000_0001; result_7 = 32'h0000_0000;
        result_8 = 32'h0000_0009; result_9 = 32'h0000_0006;
        layer_status = 32'h0000_0004;

        repeat (5) @(posedge clk);
        rst_n = 1'b1;
        repeat (2) @(posedge clk);

        axi_read(32'h0000_0004, rd_data, rd_resp);
        check(rd_resp == AXI_OKAY && rd_data == 32'h2,
              "local STATUS offset returns DONE");

        axi_write(32'h0000_0000, 32'h1, 4'hF, 1, wr_resp);
        repeat (2) @(posedge clk);
        check(wr_resp == AXI_OKAY && start_count == 1,
              "AW-first CTRL write creates one START pulse");

        axi_write(32'h0000_0000, 32'h2, 4'hF, 2, wr_resp);
        repeat (2) @(posedge clk);
        check(wr_resp == AXI_OKAY && soft_rst_count == 1,
              "W-first CTRL write creates one SOFT_RESET pulse");

        axi_write(32'h0000_0044, 32'd5, 4'hF, 0, wr_resp);
        check(wr_resp == AXI_OKAY, "local IMG_PTR write is accepted");
        axi_write(32'h0000_0040, 32'h0000_00AA, 4'b0001, 0, wr_resp);
        repeat (2) @(posedge clk);
        check(wr_resp == AXI_OKAY && last_fm_addr == 14'd5 && last_fm_data == 8'hAA,
              "IMG_DATA writes byte at IMG_PTR and increments it");

        writes_before = fm_write_count;
        axi_write(32'h0000_0040, 32'h0000_BB00, 4'b0010, 0, wr_resp);
        repeat (2) @(posedge clk);
        check(wr_resp == AXI_SLVERR && fm_write_count == writes_before,
              "invalid IMG_DATA byte lane has no BRAM side effect");

        axi_read(AXI_BASE + 32'h04, rd_data, rd_resp);
        check(rd_resp == AXI_OKAY && rd_data == 32'h2,
              "system address 0x43C00004 reaches STATUS");

        axi_write(AXI_BASE + 32'h44, 32'd9, 4'hF, 0, wr_resp);
        check(wr_resp == AXI_OKAY, "system address 0x43C00044 reaches IMG_PTR");
        axi_write(AXI_BASE + 32'h40, 32'h0000_005A, 4'b0001, 0, wr_resp);
        repeat (2) @(posedge clk);
        check(wr_resp == AXI_OKAY && last_fm_addr == 14'd9 && last_fm_data == 8'h5A,
              "system address 0x43C00040 writes image data");

        rst_n = 1'b0;
        repeat (3) @(posedge clk);
        rst_n = 1'b1;
        repeat (2) @(posedge clk);
        axi_write(32'h0000_0040, 32'h0000_003C, 4'b0001, 0, wr_resp);
        repeat (2) @(posedge clk);
        check(last_fm_addr == 14'd0 && last_fm_data == 8'h3C,
              "hardware reset clears the image write pointer");

        watch_clock_only_outputs = 1'b1;
        reset_during_write(2, 14'h0123);
        reset_during_write(9, 14'h2345);
        reset_during_write(19, 14'h3FFF);
        reset_half_transaction(1'b1);
        reset_half_transaction(1'b0);

        issue_write_pulse(AXI_BASE, 32'h2);
        check(accel_soft_rst, "software reset remains a registered single-cycle pulse");
        #3; rst_n = 1'b0;
        #0.001;
        check(accel_soft_rst, "global reset cannot truncate the software reset pulse off-clock");
        hold_and_release_reset();

        issue_write_pulse(AXI_BASE + 32'h40, 32'h69);
        @(negedge clk);
        araddr = AXI_BASE + 32'h04; arvalid = 1'b1; rready = 1'b0;
        @(posedge clk);
        @(negedge clk);
        arvalid = 1'b0;
        check(bvalid && rvalid, "reset fixture holds both B and R under backpressure");
        #3; rst_n = 1'b0;
        #0.001;
        check(!bvalid && !rvalid, "global reset immediately aborts stalled B and R responses");
        hold_and_release_reset();
        axi_write(AXI_BASE + 32'h40, 32'h87, 4'hF, 0, wr_resp);
        check(last_fm_addr == 0 && last_fm_data == 8'h87, "AXI writes recover after all reset boundary cases");
        $display("[INFO] reset boundary cases complete; writes=%0d", fm_write_count);

        if (errors == 0) begin
            $display("[PASS] axi_if_v2 regression passed");
            $finish;
        end else begin
            $fatal(1, "[FAIL] axi_if_v2 regression found %0d error(s)", errors);
        end
    end

    initial begin
        #100000;
        $fatal(1, "[FAIL] AXI reset regression watchdog expired");
    end
endmodule
