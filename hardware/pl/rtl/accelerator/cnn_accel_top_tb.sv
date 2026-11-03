`timescale 1ns / 1ps

// ============================================================================
// Testbench: cnn_accel_top_tb — R3 全量 20 张 bit-exact 验证
//
// 流程（循环 20 张）：
//   weight_bram $readmemh 加载真实权重+bias（INIT 参数透传）
//   → 写 imgN（784 字节，uint8>>1 输入量化）→ accel_start → 等 done
//   → 读 result_0~9 → 与 golden_all.hex（A 的整数金标准）逐位比对
//
// 数据文件：
//   ref_model/weights_int8/{Conv1,Conv2,FC1,FC2}_int8.txt + bias_layout.txt
//   ref_model/test_vectors/img_all.hex   （20×784 字节，每行 1 字节）
//   ref_model/test_vectors/golden_all.hex（20×10 logits，每行 1 个 INT32 hex）
// ============================================================================

module cnn_accel_top_tb;

    localparam CLK_PERIOD = 20;
    logic clk = 0;
    always #(CLK_PERIOD/2) clk = ~clk;

    logic        rst_n = 1, accel_start = 0, accel_soft_rst = 0;
    logic        accel_busy, accel_done, accel_error;
    logic [31:0] img_base_addr = 0, weight_base_addr = 0;
    logic [31:0] result_0, result_1, result_2, result_3, result_4;
    logic [31:0] result_5, result_6, result_7, result_8, result_9;
    logic [31:0] layer_status;
    logic        irq_done;

    logic        fm_a_we = 0;
    logic [13:0] fm_a_addr = 0;
    logic [7:0]  fm_a_wdata = 0;
    logic [7:0]  fm_a_rdata;
    logic        w_a_we = 0;
    logic [15:0] w_a_addr = 0;
    logic [7:0]  w_a_wdata = 0;
    logic [7:0]  w_a_rdata;

    localparam [8*255:0] P_CONV1 = "ref_model/weights_int8/Conv1_int8.txt";
    localparam [8*255:0] P_CONV2 = "ref_model/weights_int8/Conv2_int8.txt";
    localparam [8*255:0] P_FC1   = "ref_model/weights_int8/FC1_int8.txt";
    localparam [8*255:0] P_FC2   = "ref_model/weights_int8/FC2_int8.txt";
    localparam [8*255:0] P_BIAS  = "ref_model/weights_int8/bias_layout.txt";

    cnn_accel_top #(
        .INIT_EN(1),
        .W_CONV1_FILE(P_CONV1), .W_CONV2_FILE(P_CONV2),
        .W_FC1_FILE(P_FC1), .W_FC2_FILE(P_FC2),
        .W_BIAS_FILE(P_BIAS)
    ) u_dut (
        .clk(clk), .rst_n(rst_n),
        .accel_start(accel_start), .accel_soft_rst(accel_soft_rst),
        .accel_busy(accel_busy), .accel_done(accel_done), .accel_error(accel_error),
        .img_base_addr(img_base_addr), .weight_base_addr(weight_base_addr),
        .result_0(result_0), .result_1(result_1), .result_2(result_2),
        .result_3(result_3), .result_4(result_4), .result_5(result_5),
        .result_6(result_6), .result_7(result_7), .result_8(result_8),
        .result_9(result_9),
        .layer_status(layer_status), .irq_done(irq_done),
        .fm_a_we(fm_a_we), .fm_a_addr(fm_a_addr), .fm_a_wdata(fm_a_wdata), .fm_a_rdata(fm_a_rdata),
        .w_a_we(w_a_we), .w_a_addr(w_a_addr), .w_a_wdata(w_a_wdata), .w_a_rdata(w_a_rdata)
    );

    // 数据载入
    reg [7:0]  img_all [0:15679];   // 20×784
    reg [31:0] golden  [0:199];     // 20×10
    reg [7:0]  labels  [0:19];
    initial begin
        $readmemh("ref_model/test_vectors/img_all.hex", img_all);
        $readmemh("ref_model/test_vectors/golden_all.hex", golden);
        // 标签来自 images.npy 的顺序
        labels[0]=5;  labels[1]=0;  labels[2]=4;  labels[3]=1;  labels[4]=9;
        labels[5]=2;  labels[6]=1;  labels[7]=3;  labels[8]=4;  labels[9]=3;
        labels[10]=5; labels[11]=6; labels[12]=7; labels[13]=2; labels[14]=8;
        labels[15]=6; labels[16]=9; labels[17]=0; labels[18]=7; labels[19]=8;
    end

    integer errors = 0;
    integer i, img_idx;

    // 写一张图（784 字节，输入量化 >>1）
    task automatic write_image(input integer img_idx);
        begin
            for (i = 0; i < 784; i++) begin
                @(posedge clk);
                fm_a_we <= 1; fm_a_addr <= i[13:0]; fm_a_wdata <= img_all[img_idx*784 + i] >> 1;
            end
            @(posedge clk);
            fm_a_we <= 0;
        end
    endtask

    // 跑一次推理
    task automatic run_once;
        begin
            @(posedge clk);
            accel_start <= 1;
            wait (accel_done == 0);   // 确认本轮已被接受
            @(posedge clk);
            accel_start <= 0;
            wait (accel_done == 1);   // 等本轮完成
            @(posedge clk);
        end
    endtask

    // 比对一张图
    task automatic check_image(input integer img_idx);
        begin
            integer mismatch = 0;
            integer rv[0:9];
            integer mx, mv, j;
            rv[0]=result_0; rv[1]=result_1; rv[2]=result_2; rv[3]=result_3; rv[4]=result_4;
            rv[5]=result_5; rv[6]=result_6; rv[7]=result_7; rv[8]=result_8; rv[9]=result_9;
            for (j = 0; j < 10; j++) begin
                if (rv[j] !== golden[img_idx*10 + j])
                    mismatch = mismatch + 1;
            end
            mx = 0; mv = rv[0];
            for (j = 1; j < 10; j++) if (rv[j] > mv) begin mv = rv[j]; mx = j; end
            if (mismatch == 0 && mx == labels[img_idx])
                $display("  [PASS] img%02d: label=%0d argmax=%0d (10/10 bit-exact)", img_idx, labels[img_idx], mx);
            else begin
                $display("  [FAIL] img%02d: label=%0d argmax=%0d mismatch=%0d", img_idx, labels[img_idx], mx, mismatch);
                errors = errors + 1;
            end
        end
    endtask

    initial begin
        rst_n = 0;
        repeat (5) @(posedge clk);
        rst_n = 1;
        repeat (2) @(posedge clk);

        $display("===== cnn_accel_top 全量 20 张 bit-exact 验证 =====");

        for (img_idx = 0; img_idx < 20; img_idx++) begin
            write_image(img_idx);
            run_once;
            check_image(img_idx);
        end

        $display("----------------------------------------------");
        if (errors == 0)
            $display("[PASS] cnn_accel_top: 20/20 bit-exact with golden");
        else
            $display("[FAIL] cnn_accel_top: %0d image(s) mismatched", errors);
        $finish;
    end

endmodule
