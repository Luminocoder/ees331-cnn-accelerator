# V5: old verified bringup guards + v4.4 segment-capable bit/ELF.
set ::ees331_test_mode 1
set here [file dirname [file normalize [info script]]]
source [file join $here board bringup.tcl]

rename bringup::select_one bringup::original_select_one
proc bringup::select_one {filter} {
    # After reconnect XSDB may expose a target before its cable properties.
    # Never accept an unknown cable; only wait briefly for complete properties.
    for {set attempt 0} {$attempt < 20} {incr attempt} {
        set matches [targets -target-properties -filter $filter]
        if {[llength $matches] > 1} { error "Expected exactly one target matching $filter" }
        if {[llength $matches] == 1} {
            set target [lindex $matches 0]
            if {[dict exists $target jtag_cable_serial] && [dict get $target jtag_cable_serial] ne {}} {
                if {[dict get $target jtag_cable_serial] ne "1234-tulA"} {
                    error "Unexpected JTAG cable for $filter: $target; expected 1234-tulA"
                }
                targets -set [dict get $target target_id]
                return
            }
        }
        if {$attempt == 0} { puts "V5_TARGET_PROPERTIES_PENDING: $filter; properties=$matches" }
        targets
        after 100
    }
    error "JTAG target/cable properties unavailable after bounded wait for $filter: $matches"
}

connect -url TCP:localhost:3121
try {
    puts "V5_TARGETS: [targets]"
    bringup::select_one {name =~ "xc7z020*"}
    bringup::select_one {name =~ "*Cortex-A9*#0"}
    puts "V5_TARGET_PREFLIGHT_PASS"
} finally { disconnect }

bringup::run --stage run --server TCP:localhost:3121 --elf arm_firmware/app.elf --execute
after 1500
connect -url TCP:localhost:3121
try {
    bringup::select_one {name == "APU"}
    # Debugger maps are connection-local. Reinstall both verified AXI windows.
    memmap -addr 0x43C00000 -size 0x10000 -flags 3
    memmap -addr 0x43C10000 -size 0x10000 -flags 3
    bringup::select_one {name =~ "*Cortex-A9*#0"}
    bringup::ensure_stopped
    set st [bringup::read_word 0x43C00004]
    set mg [bringup::read_word 0x43C10008]
    set sv [bringup::read_word 0x43C10000]
    set pg [bringup::read_word 0x43C1000C]
    puts "VERIFY> STATUS=$st MAGIC=$mg SEGVAL=$sv PAGE_ID=$pg"
    if {$st != 0 || $mg != 0x53454731 || $pg != 0x50414731} { error "V5.3 MMIO/page capability verification failed" }
    con
    puts "V5_DEPLOYMENT_PASS"
} finally { disconnect }
