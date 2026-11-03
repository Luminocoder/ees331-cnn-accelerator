`timescale 1ns / 1ps

module pool_engine (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        pool_start,
    output logic        pool_done,

    //- BRAM �ӿ�
    output logic        fm_b_we,
    output logic [13:0] fm_b_addr,
    output logic [7:0]  fm_b_wdata,
    input  logic [7:0]  fm_b_rdata,

    //- ���ýӿ�
    input  logic [7:0]  ifm_size,      // poolingǰ�ߴ�, ��28
    input  logic [4:0]  ifm_ch,        // 通道数，需 5 位以容纳 16（Pool2）
    input  logic [13:0] ifm_base_addr, // ��������ַ
    input  logic [13:0] ofm_base_addr  // д������ַ
);

    logic [7:0] ox_cnt;
    logic [7:0] oy_cnt;
    logic [3:0] c_cnt;

    logic [7:0] ofm_size;
    assign ofm_size = ifm_size >> 1; // ����2

    logic [7:0] max_reg;

    typedef enum logic [3:0] {
        S_IDLE,
        S_REQ_0,
        S_REQ_1,
        S_REQ_2,
        S_REQ_3,
        S_WAIT_2,
        S_WAIT_3,
        S_NEXT,
        S_DONE
    } state_t;

    state_t state;

    // Address helpers
    logic [7:0] ix, iy;
    assign ix = ox_cnt << 1;
    assign iy = oy_cnt << 1;
    
    logic [13:0] base_addr_c;
    logic [13:0] out_base_addr_c;
    
    assign base_addr_c = ifm_base_addr + c_cnt * {6'd0, ifm_size} * {6'd0, ifm_size};
    assign out_base_addr_c = ofm_base_addr + c_cnt * {6'd0, ofm_size} * {6'd0, ofm_size};

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            state <= S_IDLE;
            pool_done <= 0;
            fm_b_we <= 0;
            fm_b_addr <= 0;
            fm_b_wdata <= 0;
            ox_cnt <= 0; oy_cnt <= 0; c_cnt <= 0;
            max_reg <= 0;
        end else begin
            case (state)
                S_IDLE: begin
                    if (pool_start) begin
                        pool_done <= 0;
                        ox_cnt <= 0; oy_cnt <= 0; c_cnt <= 0;
                        state <= S_REQ_0;
                    end
                end

                S_REQ_0: begin
                    fm_b_we <= 0;
                    fm_b_addr <= base_addr_c + iy * {6'd0, ifm_size} + ix;
                    state <= S_REQ_1;
                end

                S_REQ_1: begin
                    fm_b_addr <= base_addr_c + iy * {6'd0, ifm_size} + (ix + 1);
                    state <= S_REQ_2;
                end

                S_REQ_2: begin
                    fm_b_addr <= base_addr_c + (iy + 1) * {6'd0, ifm_size} + ix;
                    max_reg <= fm_b_rdata; // data 0
                    state <= S_REQ_3;
                end

                S_REQ_3: begin
                    fm_b_addr <= base_addr_c + (iy + 1) * {6'd0, ifm_size} + (ix + 1);
                    if (fm_b_rdata > max_reg) max_reg <= fm_b_rdata; // data 1
                    state <= S_WAIT_2;
                end

                S_WAIT_2: begin
                    if (fm_b_rdata > max_reg) max_reg <= fm_b_rdata; // data 2
                    state <= S_WAIT_3;
                end

                S_WAIT_3: begin
                    logic [7:0] final_max;
                    if (fm_b_rdata > max_reg) final_max = fm_b_rdata; // data 3
                    else final_max = max_reg;

                    fm_b_we <= 1;
                    fm_b_addr <= out_base_addr_c + oy_cnt * {6'd0, ofm_size} + ox_cnt;
                    fm_b_wdata <= final_max;
                    state <= S_NEXT;
                end

                S_NEXT: begin
                    fm_b_we <= 0;
                    if (ox_cnt == ofm_size - 1) begin
                        ox_cnt <= 0;
                        if (oy_cnt == ofm_size - 1) begin
                            oy_cnt <= 0;
                            if (c_cnt == ifm_ch - 1) begin
                                state <= S_DONE;
                            end else begin
                                c_cnt <= c_cnt + 1;
                                state <= S_REQ_0;
                            end
                        end else begin
                            oy_cnt <= oy_cnt + 1;
                            state <= S_REQ_0;
                        end
                    end else begin
                        ox_cnt <= ox_cnt + 1;
                        state <= S_REQ_0;
                    end
                end

                S_DONE: begin
                    pool_done <= 1;
                    if (!pool_start) state <= S_IDLE;
                end
            endcase
        end
    end
endmodule
