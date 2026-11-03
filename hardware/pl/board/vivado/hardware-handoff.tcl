namespace eval aic {}

proc require_run_complete {name} {
    set run [get_runs $name]
    if {[get_property PROGRESS $run] ne "100%" || ![string match {*Complete*} [get_property STATUS $run]]} {
        error "Run $name failed: [get_property STATUS $run]"
    }
}

proc aic::export_board_handoff {output} {
    # XSA export locates the bitstream through impl_1, not a standalone write_bitstream.
    close_design
    launch_runs impl_1 -to_step write_bitstream -jobs 2
    wait_on_run impl_1
    require_run_complete impl_1
    open_run impl_1
    set run_bit [file join [get_property DIRECTORY [get_runs impl_1]] \
        [get_property TOP [current_fileset]].bit]
    if {![file isfile $run_bit] || [file size $run_bit] == 0} {
        error "BOARD_HANDOFF_MISSING_BIT: $run_bit"
    }
    file copy $run_bit [file join $output system.bit]
    set xsa [file join $output system.xsa]
    write_hw_platform -fixed -include_bit -file $xsa
    if {![file isfile $xsa] || [file size $xsa] == 0} {
        error "BOARD_HANDOFF_MISSING_XSA: $xsa"
    }
}
