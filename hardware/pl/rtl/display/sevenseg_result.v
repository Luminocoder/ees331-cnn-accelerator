//=============================================================================
// sevenseg_result.v —— EES-331 板载 8 位共阴极八段数码管 动态扫描显示
//
// 硬件事实（来自 4 Reference_EES331_UserManual_v1.0_1220.pdf §18 + 原理图）
//   * 8 个 LED4_HS_2011AX，共阴极；段选 = DKx_7(A) 6(B) 4(C) 2(D) 1(E) 9(F) 10(G) 5(DP)
//   * 低 4 位 DK0..DK3 共用段总线 LED0_CA..LED0_DP，位选 LED_BIT1..BIT4
//   * 高 4 位 DK4..DK7 共用段总线 LED1_CA..LED1_DP，位选 LED_BIT5..BIT8
//   * 位选经 2N5551(NPN) 反相：基极(FPGA) 高 -> 三极管导通 -> 阴极拉低 -> 该位点亮
//     段选为 FPGA 直接驱动阳极 => 高电平点亮
//   所以电气上 **段选高有效、位选高有效**。手册说"位选反向驱动"，指的是相对
//   "共阴极直接驱动（低电平选中）"而言的反相。若上板实测全灭/常亮/反相，改
//   SEG_ACTIVE_HIGH / DIG_ACTIVE_HIGH / ORDER_REVERSED 三个参数即可，不必动逻辑。
//
// 每位先关闭全部位选/段选10us，再装入字模并稳定2us，最后选通可见位。
// 50MHz / 1000Hz => 每位6250拍，其中600拍消隐/稳定；空白位完全不选通。
//=============================================================================
`timescale 1ns / 1ps
`default_nettype none

module sevenseg_result #(
    parameter integer CLK_HZ          = 50_000_000, // PL 时钟频率
    parameter integer SCAN_HZ         = 1_000,      // 整帧刷新率
    parameter integer SEG_ACTIVE_HIGH = 1,          // 段选有效电平
    parameter integer DIG_ACTIVE_HIGH = 1,          // 位选有效电平
    parameter integer ORDER_REVERSED  = 0           // 1 = 左右颠倒（DK0 当最左位）
)(
    input  wire        clk,
    input  wire        rst_n,
    input  wire [31:0] value,    // 要显示的数值
    input  wire [1:0]  fmt,      // 0=decimal, 1=hex, 2=selftest, 3=eight independent cells
    input  wire        blank,    // 1 = 全灭
    output wire [7:0]  seg_low,  // DK0..DK3 段选 {dp,g,f,e,d,c,b,a}
    output wire [7:0]  seg_high, // DK4..DK7 段选
    output wire [7:0]  dig_sel   // bit0..bit7 -> LED_BIT1..LED_BIT8（对应 DK0..DK7）
);

    // ------------------------------------------------------------------
    // 字模：bit0=a bit1=b bit2=c bit3=d bit4=e bit5=f bit6=g bit7=dp
    // ------------------------------------------------------------------
    function [7:0] glyph;
        input [3:0] code;
        begin
            case (code)
                4'h0: glyph = 8'h3F;
                4'h1: glyph = 8'h06;
                4'h2: glyph = 8'h5B;
                4'h3: glyph = 8'h4F;
                4'h4: glyph = 8'h66;
                4'h5: glyph = 8'h6D;
                4'h6: glyph = 8'h7D;
                4'h7: glyph = 8'h07;
                4'h8: glyph = 8'h7F;
                4'h9: glyph = 8'h6F;
                4'hA: glyph = 8'h77;
                4'hB: glyph = 8'h7C;
                4'hC: glyph = 8'h39;
                4'hD: glyph = 8'h5E;
                4'hE: glyph = 8'h79;
                default: glyph = 8'h71;      // F
            endcase
        end
    endfunction

    localparam [7:0] SEG_OFF = 8'h00;

    // ------------------------------------------------------------------
    // 十进制拆位（0..9999；两位数只用低两位）
    // ------------------------------------------------------------------
    reg [3:0]  dec_digit [0:3];
    reg [2:0]  dec_top;
    reg [15:0] dec_work;
    integer    i;

    always @* begin
        dec_work = (value[15:0] > 16'd9999) ? 16'd9999 : value[15:0];
        for (i = 0; i < 4; i = i + 1) begin
            dec_digit[i] = dec_work % 10;
            dec_work      = dec_work / 10;
        end
        if      (dec_digit[3] != 4'd0) dec_top = 3'd3;
        else if (dec_digit[2] != 4'd0) dec_top = 3'd2;
        else if (dec_digit[1] != 4'd0) dec_top = 3'd1;
        else                           dec_top = 3'd0;
    end

    // ------------------------------------------------------------------
    // 8 位各自要显示的字 + 是否点亮（前导零熄灭）
    // ------------------------------------------------------------------
    reg [3:0] nib [0:7];
    reg       vis [0:7];
    reg       seen;
    integer   k;

    always @* begin
        seen = 1'b0;
        for (k = 0; k < 8; k = k + 1) begin
            nib[k] = 4'd0;
            vis[k] = 1'b0;
        end
        case (fmt)
            2'd0: begin                                  // 十进制，个位在 DK0
                for (k = 0; k < 4; k = k + 1) begin
                    nib[k] = dec_digit[k];
                    vis[k] = (k <= dec_top);
                end
            end
            2'd1: begin                                  // 八位十六进制
                for (k = 0; k < 8; k = k + 1)
                    nib[k] = value[k*4 +: 4];
                seen = 1'b0;
                for (k = 7; k >= 0; k = k - 1) begin     // 从高到低找首个非零
                    if (nib[k] != 4'd0) seen = 1'b1;
                    vis[k] = seen | (k == 0);
                end
            end
            2'd2: begin                                  // 自检：全 8
                for (k = 0; k < 8; k = k + 1) begin
                    nib[k] = 4'd8;
                    vis[k] = 1'b1;
                end
            end
            2'd3: begin
                // Each nibble is independent: 0..9 stays visible, A..F is off.
                // DK0 is the rightmost cell, so the first ASCII page character
                // is packed into bits 31:28. Decimal points remain off.
                for (k = 0; k < 8; k = k + 1) begin
                    nib[k] = value[k*4 +: 4];
                    vis[k] = (nib[k] <= 4'd9);
                end
            end
        endcase
    end

    // ------------------------------------------------------------------
    // 扫描节拍
    // ------------------------------------------------------------------
    localparam integer PHASE_CYCLES = (CLK_HZ / (SCAN_HZ * 8) < 3)
                                    ? 3 : (CLK_HZ / (SCAN_HZ * 8));
    localparam integer OFF_REQUEST = ((CLK_HZ / 1000) * 10 + 999) / 1000;
    localparam integer SETTLE_REQUEST = ((CLK_HZ / 1000) * 2 + 999) / 1000;
    localparam integer OFF_CYCLES = (OFF_REQUEST < 1) ? 1 :
                     ((OFF_REQUEST > PHASE_CYCLES-2) ? PHASE_CYCLES-2 : OFF_REQUEST);
    localparam integer SETTLE_CYCLES = (SETTLE_REQUEST < 1) ? 1 :
                     ((SETTLE_REQUEST > PHASE_CYCLES-OFF_CYCLES-1) ?
                        PHASE_CYCLES-OFF_CYCLES-1 : SETTLE_REQUEST);

    reg [31:0] tick;
    reg [2:0]  phase;

    always @(posedge clk) begin
        if (!rst_n) begin
            tick  <= 32'd0;
            phase <= 3'd0;
        end else if (tick == PHASE_CYCLES - 1) begin
            tick  <= 32'd0;
            phase <= phase + 3'd1;
        end else begin
            tick <= tick + 32'd1;
        end
    end

    wire [2:0] scan = ORDER_REVERSED ? (3'd7 - phase) : phase;

    // ------------------------------------------------------------------
    // 逻辑侧输出（高有效），最后再按参数取反
    // ------------------------------------------------------------------
    reg [7:0] seg_low_l;
    reg [7:0] seg_high_l;
    reg [7:0] dig_l;
    reg pending_visible;

    // Registered pins with break-before-make allow NPN storage charge to decay.
    always @(posedge clk) begin
        if (!rst_n || blank || tick == PHASE_CYCLES-1 || tick < OFF_CYCLES) begin
            seg_low_l <= SEG_OFF;
            seg_high_l <= SEG_OFF;
            dig_l <= 8'h00;
            pending_visible <= 1'b0;
        end else if (tick == OFF_CYCLES) begin
            dig_l <= 8'h00;
            pending_visible <= vis[scan];
            seg_low_l <= (scan < 4 && vis[scan]) ? glyph(nib[scan]) : SEG_OFF;
            seg_high_l <= (scan >= 4 && vis[scan]) ? glyph(nib[scan]) : SEG_OFF;
        end else if (tick == OFF_CYCLES + SETTLE_CYCLES) begin
            dig_l <= pending_visible ? (8'b0000_0001 << scan) : 8'h00;
        end
    end

    assign seg_low  = (SEG_ACTIVE_HIGH != 0) ? seg_low_l  : ~seg_low_l;
    assign seg_high = (SEG_ACTIVE_HIGH != 0) ? seg_high_l : ~seg_high_l;
    assign dig_sel  = (DIG_ACTIVE_HIGH != 0) ? dig_l      : ~dig_l;

endmodule

`default_nettype wire
