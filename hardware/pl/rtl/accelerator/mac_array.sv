`timescale 1ns / 1ps

//
// 模块: mac_array
// 版本: v1.1 (根据 R2 Review 优化)
// 功能: 8路并行的INT8乘加阵列，带3级流水线。
// 优化: 将独立的8路输入/输出端口合并为数组，使上层模块连接更简洁。
//
module mac_array #(
    parameter DATA_WIDTH  = 8,
    parameter ACCUM_WIDTH = 32,
    parameter PARALLELISM = 8
) (
    input  wire                         clk,
    input  wire                         rst_n,

    // 优化: 使用数组端口
    input  wire signed [DATA_WIDTH-1:0] data_in   [0:PARALLELISM-1],
    input  wire signed [DATA_WIDTH-1:0] weight_in [0:PARALLELISM-1],

    output reg  signed [ACCUM_WIDTH-1:0] accum_out
);
    
    localparam MUL_WIDTH = DATA_WIDTH * 2;
    wire signed [MUL_WIDTH-1:0] mul_res [0:PARALLELISM-1];

    // 优化: 使用 generate for 循环例化乘法器，更具扩展性
    genvar i;
    generate
        for (i = 0; i < PARALLELISM; i = i + 1) begin : mul_gen
            assign mul_res[i] = data_in[i] * weight_in[i];
        end
    endgenerate

    localparam ADD1_WIDTH = MUL_WIDTH + 1;
    reg signed [ADD1_WIDTH-1:0] add1_res [0:3];

    localparam ADD2_WIDTH = ADD1_WIDTH + 1;
    reg signed [ADD2_WIDTH-1:0] add2_res [0:1];
    
    always @(posedge clk) begin
        if (!rst_n) begin
            add1_res[0] <= 'd0;
            add1_res[1] <= 'd0;
            add1_res[2] <= 'd0;
            add1_res[3] <= 'd0;
            add2_res[0] <= 'd0;
            add2_res[1] <= 'd0;
            accum_out   <= 'd0;
        end else begin
            add1_res[0] <= mul_res[0] + mul_res[1];
            add1_res[1] <= mul_res[2] + mul_res[3];
            add1_res[2] <= mul_res[4] + mul_res[5];
            add1_res[3] <= mul_res[6] + mul_res[7];

            add2_res[0] <= add1_res[0] + add1_res[1];
            add2_res[1] <= add1_res[2] + add1_res[3];

            accum_out   <= add2_res[0] + add2_res[1];
        end
    end

endmodule