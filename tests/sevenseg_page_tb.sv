`timescale 1ns/1ps
module sevenseg_page_tb;
    reg clk=0,rst_n=0;
    always #10 clk=~clk;
    reg [31:0] value=0;
    reg [1:0] fmt=0;
    reg blank=1;
    wire [7:0] low,high,sel,low_i,high_i,sel_i;
    sevenseg_result dut(clk,rst_n,value,fmt,blank,low,high,sel);
    sevenseg_result #(.SEG_ACTIVE_HIGH(0),.DIG_ACTIVE_HIGH(0),.ORDER_REVERSED(1)) inverted
        (clk,rst_n,value,fmt,blank,low_i,high_i,sel_i);
    function [7:0] expected(input [3:0] c);
        case(c)
            0:expected=8'h3f;1:expected=8'h06;2:expected=8'h5b;3:expected=8'h4f;
            4:expected=8'h66;5:expected=8'h6d;6:expected=8'h7d;7:expected=8'h07;
            8:expected=8'h7f;9:expected=8'h6f;default:expected=0;
        endcase
    endfunction
    integer off_count[0:1],stable_count[0:1],j,b,bank;
    reg [7:0] previous_sel[0:1],seen[0:1];
    reg [15:0] previous_seg[0:1];
    reg [7:0] ds,lo,hi,want;
    reg [15:0] segments;
    // Test pin transitions, not only static glyphs: old RTL must fail this.
    always @(negedge clk) begin
        for(bank=0;bank<2;bank=bank+1) begin
            ds=bank ? ~sel_i : sel;lo=bank ? ~low_i : low;hi=bank ? ~high_i : high;
            segments={hi,lo};
            if(!rst_n) begin
                off_count[bank]=0;stable_count[bank]=0;
                previous_sel[bank]=0;previous_seg[bank]=0;seen[bank]=0;
            end else begin
                if(blank && (ds!==0 || segments!==0)) $fatal(1,"global blank not off");
                if(ds!=0) begin
                    if((ds & (ds-1))!=0) $fatal(1,"multiple digits selected");
                    if(previous_sel[bank]==0) begin
                        if(off_count[bank]<600) $fatal(1,"less than 10us off + 2us settle");
                        if(stable_count[bank]+1<100) $fatal(1,"segment bus not settled for 2us");
                    end else if(ds!=previous_sel[bank] || segments!=previous_seg[bank])
                        $fatal(1,"digit or glyph changed without an off interval");
                    b=0;for(j=0;j<8;j=j+1) if(ds[j]) b=j;
                    if(fmt==3) begin
                        if(value[b*4+:4]>9) $fatal(1,"blank digit transistor selected");
                        want=expected(value[b*4+:4]);
                        if(b<4) begin
                            if(lo!==want || hi!==0) $fatal(1,"low glyph/bank mismatch");
                        end else if(hi!==want || lo!==0) $fatal(1,"high glyph/bank mismatch");
                    end
                    seen[bank][b]=1;off_count[bank]=0;
                end else off_count[bank]=off_count[bank]+1;
                if(segments!=previous_seg[bank]) begin
                    if(segments!=0 && (ds!=0 || previous_sel[bank]!=0))
                        $fatal(1,"new glyph driven while a digit is active");
                    stable_count[bank]=0;
                end else stable_count[bank]=stable_count[bank]+1;
                if(lo[7] || hi[7]) $fatal(1,"decimal point lit");
                previous_sel[bank]=ds;previous_seg[bank]=segments;
            end
        end
    end
    task check_raw(input [31:0] packed_value);
        reg [7:0] visible;
        integer k;
        begin
            @(negedge clk);#1;blank=1;repeat(3) @(negedge clk);
            value=packed_value;fmt=3;visible=0;
            for(k=0;k<8;k=k+1) visible[k]=(packed_value[k*4+:4]<=9);
            seen[0]=0;seen[1]=0;blank=0;
            repeat(100010) @(negedge clk);
            if(seen[0]!==visible || seen[1]!==visible) $fatal(1,"visible digit coverage mismatch");
        end
    endtask
    initial begin
        repeat(3) @(negedge clk);rst_n=1;
        check_raw(32'h88888888);check_raw(32'h01f02f03);
        check_raw(32'h79f45fff);check_raw(32'h00f09fff);
        check_raw(32'hffffffff);check_raw(32'h45f67f89);check_raw(32'habcdef01);
        #1;blank=1;repeat(3) @(negedge clk);
        fmt=0;value=49;#1;
        if(dut.nib[0]!==9 || dut.nib[1]!==4 || dut.vis[2]!==0) $fatal(1,"decimal regression");
        fmt=2;#1;if(dut.nib[7]!==8 || dut.vis[7]!==1) $fatal(1,"selftest regression");
        $display("PASS: eight-cell scan, blank transistors off, 10us dead time, 2us settling, both polarities/orders, legacy modes");
        $finish;
    end
    initial begin #100000000;$fatal(1,"timeout");end
endmodule
