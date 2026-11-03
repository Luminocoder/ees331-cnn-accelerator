`timescale 1ns / 1ps

// Reuse the unmodified SoC test; stop only after its first bit-exact check.
module system_waveform_tb;
    mnist_soc_top_tb u_test();
    `define ACC u_test.u_dut.u_accel
    integer trace_file;
    integer input_writes = 0;
    integer result_writes = 0;
    integer result_reads = 0;
    integer layer_writes [1:6];
    integer layer_starts [1:6];
    integer layer_dones [1:6];
    integer expected_writes [1:6];
    integer previous_layer = -1;
    reg previous_conv_done = 0, previous_pool_done = 0, previous_fc_done = 0;
    reg previous_done = 0;
    reg [31:0] read_address = 0;
    integer k;
    // Overview aliases exclude repeated STATUS polling, but retain actual result handshakes.
    wire result_read_valid = u_test.rvalid && read_address >= 32'h10 && read_address <= 32'h34;
    wire result_read_ready = u_test.rready && read_address >= 32'h10 && read_address <= 32'h34;
    wire [31:0] result_read_address = (read_address >= 32'h10 && read_address <= 32'h34) ? read_address : 0;
    wire signed [31:0] result_read_data = result_read_address != 0 ? u_test.rdata : 0;

    task trace(input string event_name, input integer layer,
               input integer address, input integer value);
        $fdisplay(trace_file, "%0d,%s,%0d,%0d,%0d", $time, event_name, layer, address, value);
    endtask

    initial begin
        trace_file = $fopen("events.csv", "w");
        if (!trace_file) $fatal(1, "Cannot open events.csv");
        $fdisplay(trace_file, "time_ns,event,layer,address,value");
        expected_writes[1]=6272; expected_writes[2]=1568;
        expected_writes[3]=3136; expected_writes[4]=784;
        expected_writes[5]=64; expected_writes[6]=10;
        for (k=1; k<=6; k=k+1) begin
            layer_writes[k]=0; layer_starts[k]=0; layer_dones[k]=0;
        end
    end

    // These events describe transfers consumed on the rising clock edge.
    always @(posedge u_test.clk) if (u_test.rst_n) begin
        if (u_test.bvalid && u_test.bready && u_test.bresp !== 2'b00)
            $fatal(1, "AXI write response not OKAY");
        if (u_test.arvalid && u_test.arready) read_address = u_test.araddr;
        if (u_test.rvalid && u_test.rready) begin
            if (u_test.rresp !== 2'b00) $fatal(1, "AXI read response not OKAY");
            if (read_address == 32'h04 && u_test.rdata[1])
                trace("status_done_read", 0, read_address, u_test.rdata);
            if (read_address >= 32'h10 && read_address <= 32'h34) begin
                if (`ACC.accel_done !== 1'b1) $fatal(1, "Result read before DONE");
                if (read_address !== (32'h10 + result_reads*4) ||
                    u_test.rdata !== u_test.golden[result_reads])
                    $fatal(1, "Result read address/value mismatch");
                trace("result_read", 6, read_address, $signed(u_test.rdata));
                result_reads = result_reads + 1;
            end
        end
        if (`ACC.fm_a_we) begin
            if (`ACC.fm_a_addr !== input_writes ||
                `ACC.fm_a_wdata !== (u_test.img_all[input_writes] >> 1))
                $fatal(1, "Input BRAM address/value mismatch");
            trace("input_write", 0, `ACC.fm_a_addr, `ACC.fm_a_wdata);
            input_writes = input_writes + 1;
        end
        if (`ACC.fm_b_we) begin
            if (`ACC.layer_status < 1 || `ACC.layer_status > 5)
                $fatal(1, "Feature write outside a computing layer");
            layer_writes[`ACC.layer_status] = layer_writes[`ACC.layer_status] + 1;
            trace("feature_write", `ACC.layer_status, `ACC.fm_b_addr, `ACC.fm_b_wdata);
        end
        if (`ACC.fc_out_we) begin
            if (`ACC.layer_status !== 32'd6 || `ACC.fc_out_addr !== result_writes ||
                `ACC.fc_out_wdata !== u_test.golden[result_writes])
                $fatal(1, "FC2 result write address/value mismatch");
            trace("result_write", 6, `ACC.fc_out_addr, $signed(`ACC.fc_out_wdata));
            result_writes = result_writes + 1;
            layer_writes[6] = layer_writes[6] + 1;
        end
    end

    // Sample after NBA updates; CSV control timestamps are 1 ns after transitions.
    always @(posedge u_test.clk) begin
        #1;
        if (u_test.rst_n) begin
            if (`ACC.accel_error !== 1'b0) $fatal(1, "Unexpected accelerator error");
            if (`ACC.layer_status !== previous_layer) begin
                trace("layer", `ACC.layer_status, 0, `ACC.layer_status);
                previous_layer = `ACC.layer_status;
            end
            if (`ACC.accel_start) begin
                if (input_writes != 784) $fatal(1, "START before complete input");
                trace("start", 0, 0, 1);
            end
            if (`ACC.conv_start || `ACC.pool_start || `ACC.fc_start) begin
                layer_starts[`ACC.layer_status] = layer_starts[`ACC.layer_status] + 1;
                trace("engine_start", `ACC.layer_status, 0, 1);
            end
            if ((`ACC.conv_done && !previous_conv_done) ||
                (`ACC.pool_done && !previous_pool_done) ||
                (`ACC.fc_done && !previous_fc_done)) begin
                layer_dones[`ACC.layer_status] = layer_dones[`ACC.layer_status] + 1;
                trace("engine_done", `ACC.layer_status, 0, 1);
            end
            if (`ACC.accel_done && !previous_done) begin
                if (result_writes != 10) $fatal(1, "DONE before ten FC2 results");
                trace("done", 0, 0, 1);
            end
            previous_conv_done = `ACC.conv_done;
            previous_pool_done = `ACC.pool_done;
            previous_fc_done = `ACC.fc_done;
            previous_done = `ACC.accel_done;
        end
    end

    initial begin
        wait (u_test.img_idx == 1);
        #2;
        if (u_test.errors != 0 || input_writes != 784 || result_writes != 10 || result_reads != 10 ||
            `ACC.accel_done !== 1'b1 || u_test.irq_done !== 1'b1)
            $fatal(1, "Incomplete end-to-end waveform test");
        for (k=1; k<=6; k=k+1) begin
            if (layer_writes[k] != expected_writes[k] || layer_starts[k] != 1 || layer_dones[k] != 1)
                $fatal(1, "Layer %0d counts: writes=%0d starts=%0d dones=%0d", k,
                       layer_writes[k], layer_starts[k], layer_dones[k]);
            $display("[PASS] layer %0d: writes=%0d start=1 done=1", k, layer_writes[k]);
        end
        trace("finish", 0, 0, 0);
        $fclose(trace_file);
        $display("[PASS] system waveform: 784 input writes, six real layers, 10/10 bit-exact results and AXI readback");
        $finish;
    end

    initial begin
        #30000000;
        $fatal(1, "System waveform simulation exceeded 30 ms");
    end
    `undef ACC
endmodule
