# XSDB only. No hardware access unless --execute is explicitly provided.
namespace eval bringup { variable root [file normalize [file join [file dirname [info script]] ..]] }

proc bringup::select_one {filter} {
    set matches [targets -target-properties -filter $filter]
    if {[llength $matches] != 1} { error "Expected exactly one target matching $filter; got [llength $matches]" }
    targets -set [dict get [lindex $matches 0] target_id]
}

proc bringup::ensure_stopped {} {
    # System reset may already halt CPU0. Accept that response only after readback.
    if {[catch {stop} message options] && $message ne "Already stopped"} {
        return -options $options $message
    }
    set state [targets -target-properties -filter {is_current == 1}]
    if {[llength $state] != 1 || ![dict exists [lindex $state 0] suspended]
        || ![dict get [lindex $state 0] suspended]} {
        error "CPU stopped state could not be verified"
    }
    puts "CPU_STOPPED_VERIFIED: selected CPU reports suspended=1"
}

proc bringup::read_word {addr} {
    return [expr {[mrd -value $addr] & 0xFFFFFFFF}]
}

proc bringup::ddr_probe {} {
    # This 4 KiB is outside ALL ELF PT_LOAD segments (ELF confined to 1..2 MiB).
    # CPU is stopped after reset. Preserve the original words, including on failure.
    set base 0x01000000
    set words 1024
    set saved {}
    for {set i 0} {$i < $words} {incr i} {
        lappend saved [read_word [expr {$base + 4*$i}]]
    }
    set started [clock milliseconds]
    try {
        foreach pattern {0x55555555 0xAAAAAAAA 0xA5A55A5A} {
            for {set i 0} {$i < $words} {incr i} {
                mwr [expr {$base + 4*$i}] [expr {($pattern ^ ($base + 4*$i)) & 0xFFFFFFFF}]
            }
            for {set i 0} {$i < $words} {incr i} {
                if {[clock milliseconds] - $started > 120000} { error "DDR probe deadline exceeded" }
                set addr [expr {$base + 4*$i}]
                if {[read_word $addr] != (($pattern ^ $addr) & 0xFFFFFFFF)} {
                    error [format "DDR mismatch at 0x%08X" $addr]
                }
            }
        }
    } finally {
        for {set i 0} {$i < $words} {incr i} {
            mwr [expr {$base + 4*$i}] [lindex $saved $i]
        }
    }
    for {set i 0} {$i < $words} {incr i} {
        if {[read_word [expr {$base + 4*$i}]] != [lindex $saved $i]} { error "DDR restore verification failed" }
    }
    puts "DDR_PROBE_PASS: 4 KiB / 3 patterns + address XOR, originals restored; NOT full DDR qualification"
}

proc bringup::install_bounded_ps_helpers {} {
    # Keep the extracted vendor file unchanged. Its original mask_poll prints
    # and continues on timeout; this candidate must abort instead.
    proc ::mask_poll {addr mask} {
        set start [clock milliseconds]
        while {([bringup::read_word $addr] & $mask) == 0} {
            if {[clock milliseconds] - $start >= 10000} { error "PS init poll timeout at $addr mask $mask" }
            after 1
        }
    }
    proc ::mask_delay {addr delay} {
        if {$delay < 0 || $delay > 10000} { error "Unexpected PS delay $delay" }
        # Generated delay units are milliseconds; host delay avoids an unbounded timer poll.
        after [expr {int(ceil($delay))}]
    }
}

proc bringup::run {args} {
    variable root
    set execute 0
    set stage init
    set elf {}
    set server TCP:localhost:3121
    while {[llength $args]} {
        set key [lindex $args 0]
        set args [lrange $args 1 end]
        switch -- $key {
            --execute { set execute 1 }
            --stage - --elf - --server {
                if {![llength $args]} { error "Missing value for $key" }
                set value [lindex $args 0]
                set args [lrange $args 1 end]
                switch -- $key { --stage {set stage $value} --elf {set elf $value} --server {set server $value} }
            }
            default { error "Unknown argument $key" }
        }
    }
    if {$stage ni {init run}} { error "Stage must be init or run" }
    if {$stage eq "run" && ($elf eq {} || ![file isfile [file join $root $elf]])} { error "Missing verified ELF" }
    foreach rel {bitstream/system.bit prepared/ps7_init.tcl prepared/baseline.json} {
        if {![file isfile [file join $root $rel]]} { error "Missing $rel; run offline preflight" }
    }
    puts "PLAN: one Cortex-A9 #0 -> system reset -> PL program -> matching PS init -> post_config -> DDR probe -> MMIO"
    if {$stage eq "run"} { puts "PLAN: load $elf -> continue CPU -> verify UART/self-test/PC separately" }
    if {!$execute} { puts "DRY_RUN_ONLY: no connect/reset/program/memory/CPU operations"; return }
    if {![info exists ::env(EES331_PREFLIGHT_OK)] || $::env(EES331_PREFLIGHT_OK) ne "1"} {
        error "Use run_bringup.ps1: fresh hash/ELF preflight is required"
    }
    connect -url $server
    try {
        select_one {name =~ "*Cortex-A9*#0"}
        rst -system
        after 1000
        ensure_stopped
        select_one {name =~ "xc7z020*"}
        fpga -file [file join $root bitstream system.bit]
        select_one {name =~ "*Cortex-A9*#0"}
        ensure_stopped
        uplevel #0 [list source [file join $root prepared ps7_init.tcl]]
        install_bounded_ps_helpers
        ::ps7_init
        ::ps7_post_config
        puts "PS_INIT_RETURNED: now checking DDR, not claiming DDR training stability"
        ddr_probe
        # Register only the existing, hash-verified hardware's 64 KiB AXI window.
        select_one {name == "APU"}
        memmap -addr 0x43C00000 -size 0x10000 -flags 3
        puts "DEBUG_MMIO_MAP_SET: APU 0x43C00000..0x43C0FFFF read/write only"
        select_one {name =~ "*Cortex-A9*#0"}
        ensure_stopped
        set status [read_word 0x43C00004]
        if {$status != 0} { error "Unexpected PL status after system reset: $status" }
        # IMG_PTR is write-only; axi_if_v2 returns DEAD_BEEF for unsupported reads.
        mwr 0x43C00044 0
        set status [read_word 0x43C00004]
        if {$status != 0} { error "Unexpected PL status after IMG_PTR write: $status" }
        puts "MMIO_PROBE_PASS: STATUS=0 before/after completed IMG_PTR write; write-only pointer not read back; no inference yet"
        if {$stage eq "run"} {
            dow [file join $root $elf]
            con
            puts "CPU_STARTED_NOT_ACCEPTED: check UART_READY, PL_RESET_DONE and 10/200-logit comparison"
        } else { puts "INIT_PROBE_PASS: CPU remains stopped; UART/application not yet tested" }
    } finally { disconnect }
}

if {![info exists ::ees331_test_mode]} { bringup::run {*}$argv }
