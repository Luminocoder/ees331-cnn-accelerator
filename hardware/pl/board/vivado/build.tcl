# Usage: vivado -mode batch -source build.tcl -tclargs <stage> <output> ?config.tcl?
source [file join [file dirname [info script]] .. .. scripts vivado-common.tcl]
source [file join [file dirname [info script]] package-accelerator.tcl]
source [file join [file dirname [info script]] ps-config.tcl]
source [file join [file dirname [info script]] implementation-checks.tcl]
source [file join [file dirname [info script]] hardware-handoff.tcl]

proc build_board {arguments} {
    if {[llength $arguments] < 2 || [llength $arguments] > 3} {
        error "Usage: build.tcl <prepare|synth|impl|bitstream> <output-dir> ?board-config.tcl?"
    }
    lassign $arguments stage output config_file
    if {$stage ni {prepare synth impl bitstream}} { error "Unsupported stage: $stage" }
    set output [file normalize $output]
    if {[regexp {[^\x20-\x7e]} "$aic::root$output"]} { error "Use an ASCII source mirror and output path" }
    if {[file exists $output]} { error "Output already exists; use a fresh directory: $output" }
    set config [dict create id unselected part xc7z020clg400-1 confirmed 0 ps_config {} xdc_files {}]
    if {$config_file ne ""} {
        source [aic::require_file [file normalize $config_file]]
        if {![info exists board_config]} { error "Config must define board_config" }
        set config [dict merge $config $board_config]
    }
    set confirmed [dict get $config confirmed]
    if {![string is boolean -strict $confirmed]} { error "confirmed must be a Tcl boolean" }
    set part [dict get $config part]
    if {![string match xc7z020* $part]} { error "Expected a Zynq-7020 part, got $part" }
    if {$stage ne "prepare" && !$confirmed} {
        error "BOARD_CONFIG_REQUIRED: final builds require reviewed board DDR/MIO/UART configuration"
    }
    if {$confirmed} {
        foreach key {id provenance ps_config} {
            if {![dict exists $config $key] || [dict get $config $key] eq ""} { error "Confirmed config missing $key" }
        }
        if {[dict get $config id] eq "unselected"} { error "Confirmed config needs a real board ID" }
        aic::validate_board_ps_config [dict get $config ps_config]
    }
    set constraints {}
    foreach constraint [dict get $config xdc_files] {
        if {[file pathtype $constraint] ne "relative"} { error "XDC files must be repository-relative" }
        set path [file normalize [file join $aic::root $constraint]]
        if {[string first "$aic::root/" $path] != 0} { error "XDC outside source mirror: $constraint" }
        lappend constraints [aic::require_file $path]
    }
    dict set config xdc_files $constraints
    file mkdir $output
    set_param general.maxThreads 2
    cd $aic::root
    set ip_repo [package_accelerator $output $part]
    create_project mnist_zynq [file join $output project] -part $part
    set_property target_language Verilog [current_project]
    if {[dict exists $config board_part] && [dict get $config board_part] ne ""} {
        set_property board_part [dict get $config board_part] [current_project]
        if {[get_property part [current_project]] ne $part} { error "Board preset and declared chip part differ" }
    }
    set_property ip_repo_paths [list $ip_repo] [current_project]
    update_ip_catalog
    # >>> SEVEN SEG (1/8) 把数码管 RTL 加进工程源码，供 BD 用"模块引用"例化。
    #     需先把 rtl/sevenseg_result.v 与 rtl/sevenseg_axi.v 复制到 source/rtl/display/
    #     注意：$aic::root 已经指向 <包根>/source（见 source/scripts/vivado-common.tcl），
    #     所以这里只能拼接 root 之下的相对段，不能再写一个 source。
    set seg_rtl_files [list \
        [file normalize [file join $aic::root rtl display sevenseg_result.v]] \
        [file normalize [file join $aic::root rtl display sevenseg_axi.v]]]
    foreach f $seg_rtl_files { aic::require_file $f }
    add_files -norecurse -fileset sources_1 $seg_rtl_files
    update_compile_order -fileset sources_1
    create_bd_design system
    set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7:5.5 processing_system7_0]
    set requested_ps [dict get $config ps_config]
    aic::apply_ps_config $ps $requested_ps
    # This fixed internal contract is independent of DDR chip, MIO and board revision.
    set internal_ps [dict create CONFIG.PCW_USE_M_AXI_GP0 1 CONFIG.PCW_EN_CLK0_PORT 1 \
        CONFIG.PCW_EN_RST0_PORT 1 CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ 50.000000 \
        CONFIG.PCW_USE_FABRIC_INTERRUPT 0]
    aic::apply_ps_config $ps $internal_ps
    set requested_ps [dict merge $requested_ps $internal_ps]
    make_bd_intf_pins_external [get_bd_intf_pins $ps/DDR]
    make_bd_intf_pins_external [get_bd_intf_pins $ps/FIXED_IO]
    set_property name DDR [get_bd_intf_ports DDR_0]
    set_property name FIXED_IO [get_bd_intf_ports FIXED_IO_0]
    set accel [create_bd_cell -type ip -vlnv user.org:user:mnist_accel:1.0 mnist_accel_0]
    dict for {name filename} $aic::weights {
        set_property CONFIG.$name [file normalize [file join $aic::root ref_model weights_int8 $filename]] $accel
    }
    set interconnect [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect:2.1 axi_interconnect_0]
    # >>> SEVEN SEG (2/8) 互联从 1 个主口扩到 2 个（加速器 + 数码管）
    set_property -dict [list CONFIG.NUM_SI 1 CONFIG.NUM_MI 2] $interconnect
    set reset [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 proc_sys_reset_0]
    # !!! 关键：aux_reset_in 在本设计里由常量 0（reset_zero）驱动。
    #     proc_sys_reset 的 C_AUX_RESET_HIGH 默认是 0（即 aux_reset 低有效），
    #     那样常量 0 就等于"复位一直有效"，整个 interconnect/加速器被永久按住，
    #     现象是：位流能烧进去、JTAG 正常，但 PL 侧 0x43C00000 完全不应答
    #     （mrd 全部 READ_FAIL，串口 0 字节）。必须显式置 1（高有效），
    #     这样常量 0 才表示"不复位"。同一根线还接着 mb_debug_sys_rst（高有效），
    #     所以只能改这个参数，不能反过来把常量拉高。
    set_property -dict [list CONFIG.C_AUX_RESET_HIGH {1}] $reset
    set locked [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 reset_locked]
    set_property CONFIG.CONST_VAL 1 $locked
    set zero [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:1.1 reset_zero]
    set_property CONFIG.CONST_VAL 0 $zero
    connect_bd_intf_net [get_bd_intf_pins $ps/M_AXI_GP0] [get_bd_intf_pins $interconnect/S00_AXI]
    connect_bd_intf_net [get_bd_intf_pins $interconnect/M00_AXI] [get_bd_intf_pins $accel/s_axi]
    # >>> SEVEN SEG (3/8) 例化数码管从机（模块引用，不需要打包成 IP）
    set sevenseg [create_bd_cell -type module -reference sevenseg_axi sevenseg_0]
    # 默认参数即可：CLK_HZ=50000000 SCAN_HZ=1000 SEG/DIG 高有效 ORDER_REVERSED=0。
    # 上板自检若发现极性/位序反了，取消下面两行注释后重跑（改的是 BD 参数，不动 RTL）：
    # set_property -dict [list CONFIG.SEG_ACTIVE_HIGH 0] $sevenseg
    # set_property -dict [list CONFIG.ORDER_REVERSED 1]  $sevenseg
    # >>> SEVEN SEG (4/8) 接到互联的第二个主口
    connect_bd_intf_net [get_bd_intf_pins $interconnect/M01_AXI] [get_bd_intf_pins $sevenseg/S_AXI]
    foreach pin [list $ps/M_AXI_GP0_ACLK $interconnect/ACLK $interconnect/S00_ACLK \
                      $interconnect/M00_ACLK $accel/clk $reset/slowest_sync_clk \
                      $interconnect/M01_ACLK $sevenseg/clk] {
        connect_bd_net [get_bd_pins $ps/FCLK_CLK0] [get_bd_pins $pin]
    }
    connect_bd_net [get_bd_pins $ps/FCLK_RESET0_N] [get_bd_pins $reset/ext_reset_in]
    connect_bd_net [get_bd_pins $locked/dout] [get_bd_pins $reset/dcm_locked]
    connect_bd_net [get_bd_pins $zero/dout] [get_bd_pins $reset/aux_reset_in] [get_bd_pins $reset/mb_debug_sys_rst]
    connect_bd_net [get_bd_pins $reset/interconnect_aresetn] [get_bd_pins $interconnect/ARESETN]
    # >>> SEVEN SEG (5/8) 数码管复位一并挂上
    connect_bd_net [get_bd_pins $reset/peripheral_aresetn] [get_bd_pins $interconnect/S00_ARESETN] \
        [get_bd_pins $interconnect/M00_ARESETN] [get_bd_pins $accel/rst_n] \
        [get_bd_pins $interconnect/M01_ARESETN] [get_bd_pins $sevenseg/rst_n]
    assign_bd_address -offset 0x43C00000 -range 64K -target_address_space \
        [get_bd_addr_spaces $ps/Data] [get_bd_addr_segs $accel/s_axi/reg0]
    # >>> SEVEN SEG (6/8) 数码管寄存器映射到 0x43C10000（紧邻加速器，64K）
    #     注意：以"模块引用"方式例化的从机，其地址段名是 Vivado 自己推断出来的，
    #     不一定叫 IP-XACT 打包 IP 的 "Reg"（打包 IP 才会叫 reg0/Reg）。
    #     所以这里用通配符取该 S_AXI 接口下的唯一地址段，不写死名字。
    set seg_segs [get_bd_addr_segs -quiet "$sevenseg/S_AXI/*"]
    if {[llength $seg_segs] == 0} {
        set seg_segs [get_bd_addr_segs -quiet -of_objects [get_bd_intf_pins $sevenseg/S_AXI]]
    }
    puts "SEVENSEG_ADDR_SEG=$seg_segs"
    if {[llength $seg_segs] != 1} {
        error "seven seg: expected exactly 1 address segment under S_AXI, got [llength $seg_segs]"
    }
    assign_bd_address -offset 0x43C10000 -range 64K -target_address_space \
        [get_bd_addr_spaces $ps/Data] $seg_segs
    # >>> SEVEN SEG (7/8) 24 根引脚引出成外部端口（管脚位置见 xdc）
    create_bd_port -dir O -from 15 -to 0 seg_bus
    create_bd_port -dir O -from 7 -to 0 dig_bus
    connect_bd_net [get_bd_pins $sevenseg/seg_bus] [get_bd_ports seg_bus]
    connect_bd_net [get_bd_pins $sevenseg/dig_bus] [get_bd_ports dig_bus]
    validate_bd_design
    if {[get_property CONFIG.C_EXT_RESET_HIGH $reset] != 0} {
        error "Reset polarity mismatch: PS FCLK_RESET0_N requires active-low ext_reset_in"
    }
    aic::audit_ps_config $ps $requested_ps [file join $output ps-config-audit.tcl]
    aic::snapshot_ps_config $ps [file join $output ps-config-effective.tsv]
    save_bd_design
    write_bd_tcl [file join $output system-export.tcl]
    set note [open [file join $output build-info.txt] w]
    puts $note "tool=[version -short]\nstage=$stage\nboard=[dict get $config id]\npart=$part\nboard_confirmed=$confirmed"
    puts $note "clock_mhz=50\nbase=0x43C00000\nrange=65536\nweights=baked\ninterrupt=polling"
    puts $note "ps_properties_audited=[dict size $requested_ps]"
    if {!$confirmed} { puts $note "NOT_BOARD_READY: unreviewed PS profile/defaults; graph validation only, not hardware" }
    close $note
    if {$stage eq "prepare"} {
        puts "BOARD_PREPARE_PASS: graph validated; board_confirmed=$confirmed; no bitstream or XSA"
        close_project
        return
    }
    foreach constraint [dict get $config xdc_files] { read_xdc $constraint }
    set bd [get_files */system.bd]
    generate_target all $bd
    add_files -norecurse [make_wrapper -files $bd -top]
    set_property top system_wrapper [current_fileset]
    update_compile_order -fileset sources_1
    launch_runs synth_1 -jobs 2
    wait_on_run synth_1
    require_run_complete synth_1
    open_run synth_1
    report_utilization -file [file join $output utilization-post-synth.rpt]
    report_timing_summary -file [file join $output timing-post-synth.rpt]
    aic::report_board_drc $output post-synth
    if {$stage eq "synth"} { close_project; puts "BOARD_BUILD_PASS: synth"; return }
    close_design
    launch_runs impl_1 -to_step route_design -jobs 2
    wait_on_run impl_1
    require_run_complete impl_1
    open_run impl_1
    report_utilization -file [file join $output utilization-post-route.rpt]
    report_methodology -no_waivers -file [file join $output methodology-post-route.rpt]
    report_timing_summary -delay_type min_max -check_timing_verbose -max_paths 10 \
        -report_unconstrained -file [file join $output timing-post-route.rpt]
    report_route_status -file [file join $output route-status-post-route.rpt]
    report_cdc -details -all_checks_per_endpoint -no_waiver -file [file join $output cdc-post-route.rpt]
    report_power -file [file join $output power-estimate-post-route.rpt]
    write_checkpoint [file join $output system-post-route.dcp]
    aic::report_board_drc $output post-route 1
    foreach delay {max min} {
        set paths [get_timing_paths -delay_type $delay -max_paths 1]
        if {![llength $paths] || [get_property SLACK [lindex $paths 0]] < 0} {
            error "Timing gate failed for $delay delay; see post-route reports"
        }
    }
    if {[llength [get_drc_violations -quiet -filter {SEVERITY == Error}]]} {
        error "DRC gate failed; do not downgrade checks to generate a bitstream"
    }
    if {$stage eq "bitstream"} {
        aic::export_board_handoff $output
    }
    close_project
    puts "BOARD_BUILD_PASS: $stage"
}

if {[catch {build_board $argv} message options]} {
    puts stderr "BOARD_BUILD_FAIL: $message"
    puts stderr [dict get $options -errorinfo]
    exit 1
}
exit 0
