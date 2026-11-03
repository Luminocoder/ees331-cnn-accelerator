`timescale 1ns / 1ps

// Fixed-network 3x3 stride-1, padding-1 INT8 convolution.
// Layer busy time includes all input, weight and bias preload cycles.
// Nine local read lanes feed one input-channel window per clock. Pipeline tags
// preserve signed INT32 accumulation, big-endian bias, shift, ReLU and clamp.
// Capacity: input<=2048 bytes, OC*IC<=128, OC<=16, size<=28, IC<=16.
// Frozen MNIST shapes: 28x28x1 ->8, 14x14x8 ->16. AXI/model unchanged.
module conv_engine (
    input logic clk, rst_n, conv_start,
    output logic conv_done,
    output logic fm_b_we,
    output logic [13:0] fm_b_addr,
    output logic [7:0] fm_b_wdata,
    input logic [7:0] fm_b_rdata,
    output logic [15:0] w_b_addr,
    input logic [7:0] w_b_rdata,
    input logic [7:0] ifm_size,
    input logic [4:0] ifm_ch, ofm_ch,
    input logic [13:0] ifm_base_addr, ofm_base_addr,
    input logic [15:0] w_base_addr, bias_base_addr,
    input logic [3:0] S_layer
);
    typedef enum logic [3:0] {
        S_IDLE, S_LOAD_FM, S_DRAIN_FM, S_LOAD_W, S_DRAIN_W,
        S_LOAD_BIAS, S_DRAIN_BIAS, S_RUN, S_DRAIN, S_FINISH, S_DONE
    } state_t;
    state_t state;
    logic [7:0] size_reg;
    logic [4:0] in_channels, out_channels;
    logic [3:0] shift_reg;
    logic [9:0] plane_size;
    logic [11:0] input_length;
    logic [10:0] weight_length;
    logic [13:0] input_base, output_base;
    logic [15:0] weight_base, bias_base;

    logic [11:0] load_count;
    logic [3:0] load_tap;
    logic [6:0] load_pair;
    logic [1:0] fm_valid, w_valid, bias_valid;
    logic [10:0] fm_index [0:1];
    logic [3:0] w_tap [0:1];
    logic [6:0] w_pair [0:1];
    logic [5:0] bias_index [0:1];
    logic signed [31:0] biases [0:15];

    logic [7:0] ox_cnt, oy_cnt;
    logic [3:0] oc_cnt, ic_cnt;
    logic [9:0] spatial_index;
    logic [10:0] channel_offset;
    logic [12:0] output_index;
    logic [6:0] pair_base;
    wire [10:0] center = channel_offset + spatial_index;
    wire [6:0] pair_index = pair_base + ic_cnt;
    wire first_channel = (ic_cnt == 0);
    wire last_channel = ({1'b0,ic_cnt} == in_channels-1);
    wire last_pixel = (ox_cnt == size_reg-1 && oy_cnt == size_reg-1);
    wire final_issue = last_channel && last_pixel && ({1'b0,oc_cnt} == out_channels-1);
    wire signed [31:0] bias_reg = biases[oc_cnt]; // legacy TB diagnostic

    logic signed [7:0] lane_d [0:8];
    logic signed [7:0] lane_w [0:8];
    wire signed [7:0] mac_d [0:7];
    wire signed [7:0] mac_w [0:7];
    wire signed [31:0] mac_out;
    for (genvar m=0;m<8;m=m+1) begin: mac_inputs
        assign mac_d[m]=lane_d[m];
        assign mac_w[m]=lane_w[m];
    end
    mac_array u_mac(.clk(clk),.rst_n(rst_n),.data_in(mac_d),.weight_in(mac_w),.accum_out(mac_out));
    logic signed [31:0] ninth [0:2];
    logic [3:0] valid_pipe, first_pipe, last_pipe, final_pipe;
    logic [13:0] address_pipe [0:3];
    logic signed [31:0] bias_pipe [0:3];
    logic signed [31:0] accum_reg;
    wire signed [31:0] dot = mac_out + ninth[2];
    wire signed [31:0] accumulated = (first_pipe[3] ? bias_pipe[3] : accum_reg) + dot;
    wire signed [31:0] shifted = accumulated >>> shift_reg;

    // Replicated LUTRAM gives nine independent reads from the same cached
    // input plane. No reset on memories: each used byte is loaded before RUN.
    for(genvar tap=0;tap<9;tap=tap+1) begin: local_lane
        (* ram_style="distributed" *) logic signed [7:0] pixels [0:2047];
        (* ram_style="distributed" *) logic signed [7:0] weights [0:127];
        wire [10:0] row_address = (tap/3 == 0) ? center-size_reg :
                                  (tap/3 == 2) ? center+size_reg : center;
        wire [10:0] read_address = (tap%3 == 0) ? row_address-11'd1 :
                                   (tap%3 == 2) ? row_address+11'd1 : row_address;
        wire padding = ((tap/3 == 0) && (oy_cnt == 0)) ||
                       ((tap/3 == 2) && (oy_cnt == size_reg-1)) ||
                       ((tap%3 == 0) && (ox_cnt == 0)) ||
                       ((tap%3 == 2) && (ox_cnt == size_reg-1));
        always_ff @(posedge clk) begin
            if (rst_n && fm_valid[1]) pixels[fm_index[1]] <= fm_b_rdata;
            if (rst_n && w_valid[1] && w_tap[1] == tap) weights[w_pair[1]] <= w_b_rdata;
            if (!rst_n) begin
                lane_d[tap] <= 0;
                lane_w[tap] <= 0;
            end else if (state == S_RUN) begin
                lane_d[tap] <= padding ? 8'sd0 : pixels[read_address];
                lane_w[tap] <= weights[pair_index];
            end
        end
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            state <= S_IDLE;
            conv_done <= 0;
            fm_b_we <= 0;
            fm_b_addr <= 0; fm_b_wdata <= 0; w_b_addr <= 0;
            size_reg <= 0; in_channels <= 0; out_channels <= 0; shift_reg <= 0;
            plane_size <= 0; input_length <= 0; weight_length <= 0;
            input_base <= 0; output_base <= 0; weight_base <= 0; bias_base <= 0;
            load_count <= 0; load_tap <= 0; load_pair <= 0;
            fm_valid <= 0; w_valid <= 0; bias_valid <= 0;
            ox_cnt <= 0; oy_cnt <= 0; oc_cnt <= 0; ic_cnt <= 0;
            spatial_index <= 0; channel_offset <= 0; output_index <= 0; pair_base <= 0;
            valid_pipe <= 0; first_pipe <= 0; last_pipe <= 0; final_pipe <= 0;
            accum_reg <= 0;
            for(integer n=0;n<2;n=n+1) begin
                fm_index[n]<=0;w_tap[n]<=0;w_pair[n]<=0;bias_index[n]<=0;
            end
            for(integer n=0;n<3;n=n+1) ninth[n]<=0;
            for(integer n=0;n<4;n=n+1) begin address_pipe[n]<=0;bias_pipe[n]<=0;end
        end else begin
            fm_b_we <= 0;
            // Registered address plus synchronous BRAM: response at T+2.
            fm_valid <= {fm_valid[0],state == S_LOAD_FM};
            w_valid <= {w_valid[0],state == S_LOAD_W};
            bias_valid <= {bias_valid[0],state == S_LOAD_BIAS};
            fm_index[1]<=fm_index[0]; w_tap[1]<=w_tap[0]; w_pair[1]<=w_pair[0];
            bias_index[1]<=bias_index[0];
            if (bias_valid[1])
                biases[bias_index[1][5:2]][(3-bias_index[1][1:0])*8 +:8] <= w_b_rdata;

            // Existing eight-lane three-stage adder tree plus a latency-matched
            // ninth product; tags align output address, first/last IC and bias.
            ninth[0] <= $signed(lane_d[8]) * $signed(lane_w[8]);
            ninth[1] <= ninth[0]; ninth[2] <= ninth[1];
            valid_pipe <= {valid_pipe[2:0],state == S_RUN};
            first_pipe <= {first_pipe[2:0],first_channel};
            last_pipe <= {last_pipe[2:0],last_channel};
            final_pipe <= {final_pipe[2:0],final_issue};
            address_pipe[0] <= output_base + output_index;
            bias_pipe[0] <= bias_reg;
            for(integer n=1;n<4;n=n+1) begin
                address_pipe[n]<=address_pipe[n-1]; bias_pipe[n]<=bias_pipe[n-1];
            end
            if (valid_pipe[3]) begin
                accum_reg <= accumulated;
                if (last_pipe[3]) begin
                    fm_b_we <= 1;
                    fm_b_addr <= address_pipe[3];
                    if (shifted < 0) fm_b_wdata <= 0;
                    else if (shifted > 127) fm_b_wdata <= 127;
                    else fm_b_wdata <= shifted[7:0];
                end
                if (final_pipe[3]) state <= S_FINISH;
            end

            case(state)
                S_IDLE: if (conv_start) begin
                    conv_done <= 0;
                    size_reg<=ifm_size; in_channels<=ifm_ch; out_channels<=ofm_ch; shift_reg<=S_layer;
                    plane_size<=ifm_size*ifm_size;
                    input_length<=ifm_size*ifm_size*ifm_ch;
                    weight_length<=ifm_ch*ofm_ch*9;
                    input_base<=ifm_base_addr; output_base<=ofm_base_addr;
                    weight_base<=w_base_addr; bias_base<=bias_base_addr;
                    load_count<=0; load_tap<=0; load_pair<=0;
                    ox_cnt<=0;oy_cnt<=0;oc_cnt<=0;ic_cnt<=0;
                    spatial_index<=0;channel_offset<=0;output_index<=0;pair_base<=0;
                    state<=S_LOAD_FM;
                end
                S_LOAD_FM: begin
                    fm_b_addr<=input_base+load_count;
                    fm_index[0]<=load_count[10:0];
                    if (load_count==input_length-1) state<=S_DRAIN_FM;
                    else load_count<=load_count+1;
                end
                S_DRAIN_FM: if(fm_valid[1] && fm_index[1]==input_length-1) begin
                    load_count<=0;state<=S_LOAD_W;
                end
                S_LOAD_W: begin
                    w_b_addr<=weight_base+load_count;
                    w_tap[0]<=load_tap;w_pair[0]<=load_pair;
                    if(load_tap==8) begin load_tap<=0;load_pair<=load_pair+1;end
                    else load_tap<=load_tap+1;
                    if(load_count==weight_length-1) state<=S_DRAIN_W;
                    else load_count<=load_count+1;
                end
                S_DRAIN_W: if(w_valid[1] && w_tap[1]==8 && w_pair[1]==in_channels*out_channels-1) begin
                    load_count<=0;state<=S_LOAD_BIAS;
                end
                S_LOAD_BIAS: begin
                    w_b_addr<=bias_base+load_count;
                    bias_index[0]<=load_count[5:0];
                    if(load_count==out_channels*4-1) state<=S_DRAIN_BIAS;
                    else load_count<=load_count+1;
                end
                S_DRAIN_BIAS: if(bias_valid[1] && bias_index[1]==out_channels*4-1) state<=S_RUN;
                S_RUN: begin
                    if(last_channel) begin
                        ic_cnt<=0;channel_offset<=0;output_index<=output_index+1;
                        if(last_pixel) begin
                            ox_cnt<=0;oy_cnt<=0;spatial_index<=0;
                            oc_cnt<=oc_cnt+1;pair_base<=pair_base+in_channels;
                            if(final_issue) state<=S_DRAIN;
                        end else begin
                            spatial_index<=spatial_index+1;
                            if(ox_cnt==size_reg-1) begin ox_cnt<=0;oy_cnt<=oy_cnt+1;end
                            else ox_cnt<=ox_cnt+1;
                        end
                    end else begin
                        ic_cnt<=ic_cnt+1;channel_offset<=channel_offset+plane_size;
                    end
                end
                S_DRAIN: begin end
                S_FINISH: state<=S_DONE; // last BRAM write commits here
                S_DONE: begin conv_done<=1; if(!conv_start) state<=S_IDLE;end
                default: state<=S_IDLE;
            endcase
        end
    end

    // synthesis translate_off
    always @(posedge clk) if(rst_n && state==S_IDLE && conv_start) begin
        if(ifm_size<1 || ifm_size>28 || ifm_ch<1 || ifm_ch>16 || ofm_ch<1 || ofm_ch>16 ||
           ifm_size*ifm_size*ifm_ch>2048 || ifm_ch*ofm_ch>128)
            $fatal(1,"conv_engine: unsupported shape exceeds frozen-network cache capacity");
    end
    // synthesis translate_on
endmodule
