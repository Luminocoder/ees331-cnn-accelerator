`timescale 1ns / 1ps

module fc_engine (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        fc_start,
    output logic        fc_done,

    //- BRAM �ӿ�
    output logic        fm_b_we,
    output logic [13:0] fm_b_addr,
    output logic [7:0]  fm_b_wdata,
    input  logic [7:0]  fm_b_rdata,

    output logic [15:0] w_b_addr,
    input  logic [7:0]  w_b_rdata,

    //- ����Ĵ����ӿ� (FC2 ʹ��)
    output logic        out_we,
    output logic [3:0]  out_addr,
    output logic [31:0] out_wdata,

    //- ���ýӿ�
    input  logic [15:0] ifm_len,
    input  logic [15:0] ofm_len,
    input  logic [13:0] ifm_base_addr,
    input  logic [13:0] ofm_base_addr,
    input  logic [15:0] w_base_addr,
    input  logic [15:0] bias_base_addr,
    input  logic [3:0]  S_layer,
    input  logic        is_fc2
);

    typedef enum logic [3:0] {
        S_IDLE,
        S_FETCH_BIAS_REQ0,
        S_FETCH_BIAS_REQ1,
        S_FETCH_BIAS_REQ2,
        S_FETCH_BIAS_REQ3,
        S_FETCH_BIAS_WAIT1,
        S_FETCH_BIAS_WAIT2,
        S_CALC_PIPELINE,
        S_WRITE_BACK,
        S_NEXT_OC,
        S_DONE
    } state_t;

    state_t state;

    logic [15:0] oc_cnt;
    logic [15:0] req_cnt;
    logic [15:0] calc_cnt;
    
    logic [1:0] valid_shift;

    logic signed [31:0] accum_reg;
    logic signed [31:0] bias_reg;

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            state <= S_IDLE;
            fc_done <= 0;
            fm_b_we <= 0;
            out_we <= 0;
            oc_cnt <= 0;
            req_cnt <= 0;
            calc_cnt <= 0;
            valid_shift <= 0;
            accum_reg <= 0;
            bias_reg <= 0;
            fm_b_addr <= 0;
            w_b_addr <= 0;
        end else begin
            case (state)
                S_IDLE: begin
                    if (fc_start) begin
                        fc_done <= 0;
                        oc_cnt <= 0;
                        state <= S_FETCH_BIAS_REQ0;
                    end
                end

                S_FETCH_BIAS_REQ0: begin
                    w_b_addr <= bias_base_addr + oc_cnt * 4 + 0;
                    state <= S_FETCH_BIAS_REQ1;
                end
                S_FETCH_BIAS_REQ1: begin
                    w_b_addr <= bias_base_addr + oc_cnt * 4 + 1;
                    state <= S_FETCH_BIAS_REQ2;
                end
                S_FETCH_BIAS_REQ2: begin
                    w_b_addr <= bias_base_addr + oc_cnt * 4 + 2;
                    bias_reg[31:24] <= w_b_rdata;
                    state <= S_FETCH_BIAS_REQ3;
                end
                S_FETCH_BIAS_REQ3: begin
                    w_b_addr <= bias_base_addr + oc_cnt * 4 + 3;
                    bias_reg[23:16] <= w_b_rdata;
                    state <= S_FETCH_BIAS_WAIT1;
                end
                S_FETCH_BIAS_WAIT1: begin
                    bias_reg[15:8] <= w_b_rdata;
                    state <= S_FETCH_BIAS_WAIT2;
                end
                S_FETCH_BIAS_WAIT2: begin
                    bias_reg[7:0] <= w_b_rdata;
                    req_cnt <= 0;
                    calc_cnt <= 0;
                    valid_shift <= 0;
                    accum_reg <= 0;
                    state <= S_CALC_PIPELINE;
                end

                S_CALC_PIPELINE: begin
                    // ����������
                    if (req_cnt < ifm_len) begin
                        fm_b_addr <= ifm_base_addr + req_cnt;
                        // 权重文件为输入主序(input-major)：w[req][oc]，与 golden 的 reshape(in,out) 一致
                        w_b_addr <= w_base_addr + req_cnt * ofm_len + oc_cnt;
                        req_cnt <= req_cnt + 1;
                        valid_shift[0] <= 1;
                    end else begin
                        valid_shift[0] <= 0;
                    end
                    
                    // ��λ�Ĵ�����¼�����Ƿ���Ч
                    valid_shift[1] <= valid_shift[0];

                    // �������ݲ��ۼ�
                    if (valid_shift[1]) begin
                        accum_reg <= accum_reg + $signed(fm_b_rdata) * $signed(w_b_rdata);
                        calc_cnt <= calc_cnt + 1;
                        if (calc_cnt == ifm_len - 1) begin
                            state <= S_WRITE_BACK;
                        end
                    end
                end

                S_WRITE_BACK: begin
                    logic signed [31:0] biased_val;
                    logic signed [31:0] shifted_val;
                    logic signed [31:0] relu_val;
                    
                    biased_val = accum_reg + bias_reg;
                    shifted_val = biased_val >>> S_layer;
                    
                    if (shifted_val < 0) relu_val = 0;
                    else if (shifted_val > 127) relu_val = 127;
                    else relu_val = shifted_val;

                    if (is_fc2) begin
                        out_we <= 1;
                        out_addr <= oc_cnt[3:0];
                        out_wdata <= biased_val;
                    end else begin
                        fm_b_we <= 1;
                        fm_b_addr <= ofm_base_addr + oc_cnt;
                        fm_b_wdata <= relu_val[7:0];
                    end
                    state <= S_NEXT_OC;
                end

                S_NEXT_OC: begin
                    fm_b_we <= 0;
                    out_we <= 0;
                    if (oc_cnt == ofm_len - 1) begin
                        state <= S_DONE;
                    end else begin
                        oc_cnt <= oc_cnt + 1;
                        state <= S_FETCH_BIAS_REQ0;
                    end
                end

                S_DONE: begin
                    fc_done <= 1;
                    if (!fc_start) state <= S_IDLE;
                end
            endcase
        end
    end

endmodule
