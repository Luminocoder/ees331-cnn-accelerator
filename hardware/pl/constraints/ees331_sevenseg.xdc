#=============================================================================
# ees331_sevenseg.xdc —— EES-331 Rev1.0 板载 8 位八段数码管 管脚约束
#
# 管脚来源：《4 Reference_EES331_UserManual_v1.0_1220.pdf》§18 八段数码管模块
#           管脚约束表（序号 1~24），并对照 p.23/24/25 原理图核对位选反相。
#
# 适用端口（sevenseg_axi / sevenseg_result）
#   seg_bus[7:0]   -> 低 4 位段总线 LED0_CA..LED0_DP （DK0..DK3 共用）
#   seg_bus[15:8]  -> 高 4 位段总线 LED1_CA..LED1_DP （DK4..DK7 共用）
#   dig_bus[7:0]   -> 位选 LED_BIT1..LED_BIT8        （DK0..DK7 各自一条）
#
# 位序约定（模块内部 bit0 = a 段）：
#   seg_bus[0]=a seg_bus[1]=b ... seg_bus[6]=g seg_bus[7]=dp（高 4 位同理 +8）
#
# !!! 上板第一件事：先做极性/位序自检 !!!
#   在 ARM 里写 CTRL = 0x5（bit0 BLANK=0, FMT=10b=自检），应当 8 位全部显示 "8"。
#   * 全灭        -> 把 SEG_ACTIVE_HIGH / DIG_ACTIVE_HIGH 取反
#   * 只亮半边    -> 两组段总线接错（检查 seg_bus[15:8]）
#   * 字是镜像/乱码 -> 段序 a..g 需要交换（改 XDC 里的位序，不改 RTL）
#   * 两位顺序反了  -> ORDER_REVERSED 置 1
#
# IOSTANDARD：EES-331 的 PL Bank 34/35 为 3.3 V，故用 LVCMOS33。
#   若综合时报 bank 电压冲突（IORC 类 DRC），说明该 bank 不是 3.3 V，
#   请按开发板实际供电改为 LVCMOS18 等，**不要**用 set_property 强行压掉 DRC。
#=============================================================================

# ---------------- 低 4 位段选（DK0..DK3 共用的四段总线）----------------
set_property -dict {PACKAGE_PIN R21  IOSTANDARD LVCMOS33} [get_ports {seg_bus[0]}] ;# LED0_CA  a
set_property -dict {PACKAGE_PIN P20  IOSTANDARD LVCMOS33} [get_ports {seg_bus[1]}] ;# LED0_CB  b
set_property -dict {PACKAGE_PIN P21  IOSTANDARD LVCMOS33} [get_ports {seg_bus[2]}] ;# LED0_CC  c
set_property -dict {PACKAGE_PIN N15  IOSTANDARD LVCMOS33} [get_ports {seg_bus[3]}] ;# LED0_CD  d
set_property -dict {PACKAGE_PIN P15  IOSTANDARD LVCMOS33} [get_ports {seg_bus[4]}] ;# LED0_CE  e
set_property -dict {PACKAGE_PIN P17  IOSTANDARD LVCMOS33} [get_ports {seg_bus[5]}] ;# LED0_CF  f
set_property -dict {PACKAGE_PIN P18  IOSTANDARD LVCMOS33} [get_ports {seg_bus[6]}] ;# LED0_CG  g
set_property -dict {PACKAGE_PIN T16  IOSTANDARD LVCMOS33} [get_ports {seg_bus[7]}] ;# LED0_DP  dp

# ---------------- 高 4 位段选（DK4..DK7 共用的四段总线）----------------
set_property -dict {PACKAGE_PIN T17  IOSTANDARD LVCMOS33} [get_ports {seg_bus[8]}] ;# LED1_CA  a
set_property -dict {PACKAGE_PIN R19  IOSTANDARD LVCMOS33} [get_ports {seg_bus[9]}] ;# LED1_CB  b
set_property -dict {PACKAGE_PIN T19  IOSTANDARD LVCMOS33} [get_ports {seg_bus[10]}] ;# LED1_CC  c
set_property -dict {PACKAGE_PIN R18  IOSTANDARD LVCMOS33} [get_ports {seg_bus[11]}] ;# LED1_CD  d
set_property -dict {PACKAGE_PIN T18  IOSTANDARD LVCMOS33} [get_ports {seg_bus[12]}] ;# LED1_CE  e
set_property -dict {PACKAGE_PIN P16  IOSTANDARD LVCMOS33} [get_ports {seg_bus[13]}] ;# LED1_CF  f
set_property -dict {PACKAGE_PIN R16  IOSTANDARD LVCMOS33} [get_ports {seg_bus[14]}] ;# LED1_CG  g
set_property -dict {PACKAGE_PIN R15  IOSTANDARD LVCMOS33} [get_ports {seg_bus[15]}] ;# LED1_DP  dp

# ---------------- 位选（经 2N5551 NPN 驱动，FPGA 高电平点亮）----------------
set_property -dict {PACKAGE_PIN M20  IOSTANDARD LVCMOS33} [get_ports {dig_bus[0]}] ;# LED_BIT1  DK0
set_property -dict {PACKAGE_PIN N19  IOSTANDARD LVCMOS33} [get_ports {dig_bus[1]}] ;# LED_BIT2  DK1
set_property -dict {PACKAGE_PIN N20  IOSTANDARD LVCMOS33} [get_ports {dig_bus[2]}] ;# LED_BIT3  DK2
set_property -dict {PACKAGE_PIN M21  IOSTANDARD LVCMOS33} [get_ports {dig_bus[3]}] ;# LED_BIT4  DK3
set_property -dict {PACKAGE_PIN M22  IOSTANDARD LVCMOS33} [get_ports {dig_bus[4]}] ;# LED_BIT5  DK4
set_property -dict {PACKAGE_PIN N22  IOSTANDARD LVCMOS33} [get_ports {dig_bus[5]}] ;# LED_BIT6  DK5
set_property -dict {PACKAGE_PIN P22  IOSTANDARD LVCMOS33} [get_ports {dig_bus[6]}] ;# LED_BIT7  DK6
set_property -dict {PACKAGE_PIN R20  IOSTANDARD LVCMOS33} [get_ports {dig_bus[7]}] ;# LED_BIT8  DK7

# 位选是单点扫描信号，扇出小；给个偏保守的驱动强度，减少相邻位串扰（鬼影）
set_property SLEW SLOW [get_ports {dig_bus[*]}]
set_property DRIVE 8     [get_ports {dig_bus[*]}]
set_property DRIVE 8     [get_ports {seg_bus[*]}]

# ---------------- 独立综合时的时钟约束（在 BD 里由 FCLK_CLK0 自动生成，无需重复）-------
# create_clock -name clk_50m -period 20.000 [get_ports clk]
