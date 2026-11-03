`timescale 1ns/1ps
// Independent scalar convolution oracle. Check every output address exactly
// once, signed extrema, padding, channel transitions, stale-cache reloads,
// shifts/saturation and reset during preload / active pipeline.
module conv_pipeline_tb;
    reg clk=0; always #10 clk=~clk;
    reg rst_n=0,conv_start=0;
    wire conv_done,fm_we;
    wire [13:0] fm_addr;
    wire [7:0] fm_wdata,fm_rdata;
    wire [15:0] w_addr;
    wire [7:0] w_rdata;
    reg [7:0] size;
    reg [4:0] ic,oc;
    reg [3:0] shift;
    conv_engine dut(.clk(clk),.rst_n(rst_n),.conv_start(conv_start),.conv_done(conv_done),
        .fm_b_we(fm_we),.fm_b_addr(fm_addr),.fm_b_wdata(fm_wdata),.fm_b_rdata(fm_rdata),
        .w_b_addr(w_addr),.w_b_rdata(w_rdata),.ifm_size(size),.ifm_ch(ic),.ofm_ch(oc),
        .ifm_base_addr(14'd0),.ofm_base_addr(14'h2000),.w_base_addr(16'd0),
        .bias_base_addr(16'd52040),.S_layer(shift));
    fm_bram fm(.clk(clk),.a_we(1'b0),.a_addr(14'd0),.a_wdata(8'd0),.a_rdata(),
        .b_we(fm_we),.b_addr(fm_addr),.b_wdata(fm_wdata),.b_rdata(fm_rdata));
    weight_bram wm(.clk(clk),.a_we(1'b0),.a_addr(16'd0),.a_wdata(8'd0),.a_rdata(),
        .b_addr(w_addr),.b_rdata(w_rdata));
    integer expected[0:8191];
    integer seen[0:8191];
    integer writes=0,total=0,case_id=0,checked=0;
    bit monitor=0;
    integer biases[0:15];
    always @(posedge clk) if(rst_n && monitor && fm_we) begin
        if(fm_addr<8192 || fm_addr>=8192+total)
            $fatal(1,"out-of-range write case=%0d addr=%0d",case_id,fm_addr);
        if(seen[fm_addr-8192]) $fatal(1,"duplicate write case=%0d addr=%0d",case_id,fm_addr);
        if(fm_wdata !== expected[fm_addr-8192][7:0])
            $fatal(1,"conv mismatch case=%0d addr=%0d got=%0d expected=%0d",case_id,fm_addr,fm_wdata,expected[fm_addr-8192]);
        seen[fm_addr-8192]=1;writes=writes+1;
    end
    task setup_case(input integer ident,n,ni,no,s);
        integer value,sum,iy,ix,index;
        begin
            @(negedge clk);case_id=ident;size=n;ic=ni;oc=no;shift=s;
            for(integer i=0;i<n*n*ni;i=i+1) begin
                value=(i*37+ident*71)%256-128;
                fm.mem[i]=value[7:0];
            end
            for(integer i=0;i<no*ni*9;i=i+1) begin
                value=(i*13+ident*17)%256-128;
                wm.mem[i]=value[7:0];
            end
            for(integer o=0;o<no;o=o+1) begin
                case(o%5)
                    0:biases[o]=131071;
                    1:biases[o]=-32769;
                    2:biases[o]=129;
                    3:biases[o]=-5;
                    4:biases[o]=0;
                endcase
                for(integer b=0;b<4;b=b+1)
                    wm.mem[52040+o*4+b]=(biases[o]>>((3-b)*8))&255;
                for(integer y=0;y<n;y=y+1) for(integer x=0;x<n;x=x+1) begin
                    sum=biases[o];
                    for(integer c=0;c<ni;c=c+1) for(integer ky=0;ky<3;ky=ky+1)
                        for(integer kx=0;kx<3;kx=kx+1) begin
                            iy=y+ky-1;ix=x+kx-1;
                            if(iy>=0 && iy<n && ix>=0 && ix<n)
                                sum=sum+$signed(fm.mem[c*n*n+iy*n+ix])*$signed(wm.mem[(o*ni+c)*9+ky*3+kx]);
                        end
                    sum=sum>>>s;
                    index=o*n*n+y*n+x;
                    expected[index]=(sum<0)?0:((sum>127)?127:sum);
                    seen[index]=0;fm.mem[8192+index]=8'hc3;
                end
            end
            total=n*n*no;writes=0;
        end
    endtask
    task run_case;
        integer cycles;
        begin
            @(negedge clk);monitor=1;conv_start=1;
            @(negedge clk);conv_start=0;
            cycles=0;
            while(!conv_done && cycles<100000) begin @(negedge clk);cycles=cycles+1;end
            if(!conv_done) $fatal(1,"timeout case=%0d",case_id);
            if(writes!=total) $fatal(1,"missing writes case=%0d got=%0d total=%0d",case_id,writes,total);
            for(integer i=0;i<total;i=i+1)
                if(fm.mem[8192+i] !== expected[i][7:0]) $fatal(1,"committed BRAM mismatch case=%0d i=%0d",case_id,i);
            checked=checked+total;monitor=0;
            $display("PASS conv_pipeline case=%0d shape=%0dx%0dx%0d->%0d shift=%0d outputs=%0d cycles=%0d",case_id,size,size,ic,oc,shift,total,cycles);
            repeat(3) @(negedge clk);
        end
    endtask
    task reset_abort(input integer delay_cycles);
        begin
            monitor=0;@(negedge clk);conv_start=1;
            @(negedge clk);conv_start=0;
            repeat(delay_cycles) @(negedge clk);
            rst_n=0;repeat(4) @(negedge clk);
            if(fm_we || conv_done || dut.valid_pipe) $fatal(1,"reset failed to flush pipeline");
            rst_n=1;repeat(3) @(negedge clk);
        end
    endtask
    initial begin
        repeat(4) @(negedge clk);rst_n=1;
        setup_case(0,1,1,1,0);run_case();
        setup_case(1,4,2,3,0);run_case();
        setup_case(2,4,2,3,9);run_case();
        setup_case(3,28,1,8,9);run_case();
        setup_case(4,14,8,16,9);run_case();
        setup_case(5,28,1,8,10);run_case();
        setup_case(6,8,16,8,15);run_case();
        setup_case(7,28,1,8,0);run_case();
        setup_case(8,14,8,16,9);reset_abort(50);
        setup_case(9,14,8,16,9);run_case();
        setup_case(10,14,8,16,9);reset_abort(3000);
        setup_case(11,14,8,16,9);run_case();
        $display("PASS: conv_pipeline scalar oracle, %0d outputs, signed boundaries, reload and 2 reset phases",checked);
        $finish;
    end
    initial begin #10000000;$fatal(1,"testbench watchdog");end
endmodule
