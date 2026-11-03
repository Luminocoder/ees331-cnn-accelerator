`timescale 1ns / 1ps

// ============================================================================
// Testbench: controller_tb — 验证主控制器 FSM
// 版本: v1.0
//
// 用 mock engine 模拟 conv/pool/fc 的 done 行为（收到 start 后清 done，
// 若干周期后拉高 done 并保持），验证：
//   1. 复位后 idle 状态
//   2. START 后 busy 拉高，六层按序调度
//   3. 每类 engine 恰好收到 2 次单周期 start 脉冲
//   4. 全部完成后 done/irq 拉高、busy 拉低，并保持
//   5. 新 START 清除 done
//   6. 运行中 soft_rst 立即回 idle
// ============================================================================

module controller_tb;

    localparam CLK_PERIOD = 20;

    logic clk = 0;
    always #(CLK_PERIOD/2) clk = ~clk;

    integer errors = 0;

    // ---- DUT 信号 ----
    logic        rst_n, accel_start, accel_soft_rst;
    logic        accel_busy, accel_done, accel_error, irq_done;
    logic        conv_start, pool_start, fc_start;
    logic        conv_done, pool_done, fc_done;
    logic [31:0] layer_cfg, layer_status;

    controller u_dut (
        .clk(clk), .rst_n(rst_n),
        .accel_start(accel_start), .accel_soft_rst(accel_soft_rst),
        .accel_busy(accel_busy), .accel_done(accel_done), .accel_error(accel_error),
        .conv_start(conv_start), .pool_start(pool_start), .fc_start(fc_start),
        .conv_done(conv_done), .pool_done(pool_done), .fc_done(fc_done),
        .layer_cfg(layer_cfg), .layer_status(layer_status),
        .irq_done(irq_done)
    );

    // ---- mock engines：start 后清 done，N 周期后拉高并保持 ----
    logic conv_done_r = 0, pool_done_r = 0, fc_done_r = 0;
    logic [3:0] ccnt = 0, pcnt = 0, fcnt = 0;

    assign conv_done = conv_done_r;
    assign pool_done = pool_done_r;
    assign fc_done   = fc_done_r;

    always @(posedge clk) begin
        if (conv_start) begin conv_done_r <= 0; ccnt <= 4; end
        else if (ccnt) begin ccnt <= ccnt - 1; if (ccnt == 1) conv_done_r <= 1; end

        if (pool_start) begin pool_done_r <= 0; pcnt <= 2; end
        else if (pcnt) begin pcnt <= pcnt - 1; if (pcnt == 1) pool_done_r <= 1; end

        if (fc_start) begin fc_done_r <= 0; fcnt <= 6; end
        else if (fcnt) begin fcnt <= fcnt - 1; if (fcnt == 1) fc_done_r <= 1; end
    end

    // ---- start 脉冲计数（应各为 2 次，且每次仅 1 周期） ----
    integer conv_pulses = 0, pool_pulses = 0, fc_pulses = 0;
    always @(posedge clk) begin
        if (conv_start) conv_pulses++;
        if (pool_start) pool_pulses++;
        if (fc_start)   fc_pulses++;
    end

    // ---- layer_cfg S 位段采样（ADR-0002：[6:3]=shift, [2:0]=layer_id） ----
    logic [3:0] s_conv1_seen = 0, s_conv2_seen = 0, s_fc1_seen = 0;
    always @(posedge clk) begin
        case (layer_status[3:0])
            4'd1: s_conv1_seen <= layer_cfg[6:3];  // S_CONV1 状态
            4'd3: s_conv2_seen <= layer_cfg[6:3];  // S_CONV2 状态
            4'd5: s_fc1_seen   <= layer_cfg[6:3];  // S_FC1 状态
        endcase
    end

    integer i;
    integer max_layer;

    initial begin
        rst_n = 0; accel_start = 0; accel_soft_rst = 0;
        repeat (3) @(posedge clk);
        rst_n = 1;
        @(posedge clk);

        $display("==============================================");
        $display("  controller_tb — main FSM");
        $display("==============================================");

        // ---- TEST 1: 复位后 idle ----
        if (!accel_busy && !accel_done && !irq_done && layer_status == 0)
            $display("[PASS] TEST 1: after reset, idle (busy=0 done=0 irq=0)");
        else begin
            $display("[FAIL] TEST 1: bad reset state busy=%0d done=%0d", accel_busy, accel_done);
            errors++;
        end

        // ---- TEST 2: START → busy，六层调度 ----
        @(posedge clk);
        accel_start <= 1;
        @(posedge clk);
        accel_start <= 0;
        @(posedge clk);
        if (accel_busy)
            $display("[PASS] TEST 2: busy asserted after START");
        else begin
            $display("[FAIL] TEST 2: busy not asserted");
            errors++;
        end

        // 等完成，记录最大 layer_status
        max_layer = 0;
        for (i = 0; i < 300; i++) begin
            @(posedge clk);
            if (layer_status > max_layer) max_layer = layer_status;
            if (accel_done) break;
        end

        // ---- TEST 3: 六层全部执行（状态编号应到 6=S_FC2, 7=S_DONE） ----
        if (max_layer >= 7)
            $display("[PASS] TEST 3: all 6 layers scheduled (max state = %0d)", max_layer);
        else begin
            $display("[FAIL] TEST 3: only reached state %0d", max_layer);
            errors++;
        end

        // ---- TEST 4: 完成后 done/irq 高、busy 低 ----
        if (accel_done && irq_done && !accel_busy)
            $display("[PASS] TEST 4: done=1 irq=1 busy=0 after completion");
        else begin
            $display("[FAIL] TEST 4: done=%0d irq=%0d busy=%0d", accel_done, irq_done, accel_busy);
            errors++;
        end

        // ---- TEST 5: start 脉冲各 2 次 ----
        if (conv_pulses == 2 && pool_pulses == 2 && fc_pulses == 2)
            $display("[PASS] TEST 5: start pulses conv=2 pool=2 fc=2");
        else begin
            $display("[FAIL] TEST 5: pulses conv=%0d pool=%0d fc=%0d", conv_pulses, pool_pulses, fc_pulses);
            errors++;
        end

        // ---- TEST 6: done 保持 20 周期 ----
        repeat (20) @(posedge clk);
        if (accel_done)
            $display("[PASS] TEST 6: DONE held high (frozen convention)");
        else begin
            $display("[FAIL] TEST 6: DONE dropped early");
            errors++;
        end

        // ---- TEST 7: 新 START 清除 done 并重新 busy ----
        @(posedge clk);
        accel_start <= 1;
        @(posedge clk);
        accel_start <= 0;
        @(posedge clk);
        if (!accel_done && accel_busy)
            $display("[PASS] TEST 7: new START clears DONE, busy again");
        else begin
            $display("[FAIL] TEST 7: done=%0d busy=%0d after new START", accel_done, accel_busy);
            errors++;
        end

        // ---- TEST 8: 运行中 soft_rst 回 idle ----
        repeat (3) @(posedge clk);
        @(posedge clk);
        accel_soft_rst <= 1;
        @(posedge clk);
        accel_soft_rst <= 0;
        @(posedge clk);
        if (!accel_busy && !accel_done && layer_status == 0)
            $display("[PASS] TEST 8: soft_rst mid-run returns to idle");
        else begin
            $display("[FAIL] TEST 8: busy=%0d done=%0d after soft_rst", accel_busy, accel_done);
            errors++;
        end

        // ---- TEST 9: layer_cfg S 位段 = 9/9/10（ADR-0002 标定值） ----
        if (s_conv1_seen == 4'd9 && s_conv2_seen == 4'd9 && s_fc1_seen == 4'd10)
            $display("[PASS] TEST 9: layer_cfg S fields = 9/9/10");
        else begin
            $display("[FAIL] TEST 9: S fields conv1=%0d conv2=%0d fc1=%0d",
                     s_conv1_seen, s_conv2_seen, s_fc1_seen);
            errors++;
        end

        $display("----------------------------------------------");
        if (errors == 0)
            $display("[PASS] controller: all 9 tests passed");
        else
            $display("[FAIL] controller: %0d test(s) failed", errors);
        $finish;
    end

endmodule
