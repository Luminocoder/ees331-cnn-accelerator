namespace eval aic {}

proc aic::validate_board_ps_config {config} {
    set required {
        PCW_CRYSTAL_PERIPHERAL_FREQMHZ PCW_APU_PERIPHERAL_FREQMHZ
        PCW_PRESET_BANK0_VOLTAGE PCW_PRESET_BANK1_VOLTAGE
        PCW_UIPARAM_DDR_ENABLE PCW_UIPARAM_DDR_MEMORY_TYPE PCW_UIPARAM_DDR_PARTNO
        PCW_UIPARAM_DDR_BUS_WIDTH PCW_UIPARAM_DDR_DRAM_WIDTH PCW_UIPARAM_DDR_DEVICE_CAPACITY
        PCW_UIPARAM_DDR_FREQ_MHZ PCW_UIPARAM_DDR_ROW_ADDR_COUNT PCW_UIPARAM_DDR_COL_ADDR_COUNT
        PCW_UIPARAM_DDR_BANK_ADDR_COUNT PCW_UIPARAM_DDR_SPEED_BIN PCW_UIPARAM_DDR_CL
        PCW_UIPARAM_DDR_CWL PCW_UIPARAM_DDR_BL PCW_UIPARAM_DDR_AL PCW_UIPARAM_DDR_T_RCD
        PCW_UIPARAM_DDR_T_RP PCW_UIPARAM_DDR_T_RC PCW_UIPARAM_DDR_T_RAS_MIN PCW_UIPARAM_DDR_T_FAW
        PCW_UIPARAM_DDR_ECC PCW_UIPARAM_DDR_TRAIN_WRITE_LEVEL PCW_UIPARAM_DDR_TRAIN_READ_GATE
        PCW_UIPARAM_DDR_TRAIN_DATA_EYE PCW_UIPARAM_DDR_BOARD_DELAY0 PCW_UIPARAM_DDR_BOARD_DELAY1
        PCW_UIPARAM_DDR_BOARD_DELAY2 PCW_UIPARAM_DDR_BOARD_DELAY3 PCW_UIPARAM_DDR_DQS_TO_CLK_DELAY_0
        PCW_UIPARAM_DDR_DQS_TO_CLK_DELAY_1 PCW_UIPARAM_DDR_DQS_TO_CLK_DELAY_2
        PCW_UIPARAM_DDR_DQS_TO_CLK_DELAY_3 PCW_UART0_PERIPHERAL_ENABLE PCW_UART1_PERIPHERAL_ENABLE
    }
    set missing {}
    foreach name $required {
        set key CONFIG.$name
        if {![dict exists $config $key] || [string trim [dict get $config $key]] eq ""} {
            lappend missing $key
        }
    }
    if {[llength $missing]} { error "BOARD_PS_CONFIG_INCOMPLETE: missing [join $missing {, }]" }
    foreach name {PCW_UIPARAM_DDR_ENABLE PCW_UIPARAM_DDR_TRAIN_WRITE_LEVEL
                  PCW_UIPARAM_DDR_TRAIN_READ_GATE PCW_UIPARAM_DDR_TRAIN_DATA_EYE
                  PCW_UART0_PERIPHERAL_ENABLE PCW_UART1_PERIPHERAL_ENABLE} {
        if {![string is boolean -strict [dict get $config CONFIG.$name]]} {
            error "BOARD_PS_CONFIG_INVALID: CONFIG.$name must be boolean"
        }
    }
    if {![dict get $config CONFIG.PCW_UIPARAM_DDR_ENABLE]} {
        error {BOARD_PS_CONFIG_INVALID: PS DDR must be enabled}
    }
    foreach name {PCW_CRYSTAL_PERIPHERAL_FREQMHZ PCW_APU_PERIPHERAL_FREQMHZ PCW_UIPARAM_DDR_FREQ_MHZ} {
        set value [dict get $config CONFIG.$name]
        if {![string is double -strict $value] || !($value > 0 && $value < Inf)} {
            error "BOARD_PS_CONFIG_INVALID: CONFIG.$name must be a positive finite frequency"
        }
    }
    set uart_enabled 0
    foreach uart {0 1} {
        if {![dict get $config CONFIG.PCW_UART${uart}_PERIPHERAL_ENABLE]} { continue }
        set uart_enabled 1
        foreach suffix [list UART${uart}_IO GRP_FULL_ENABLE BAUD_RATE] {
            set key CONFIG.PCW_UART${uart}_$suffix
            if {![dict exists $config $key] || [string trim [dict get $config $key]] eq ""} {
                error "BOARD_PS_CONFIG_INCOMPLETE: missing $key"
            }
        }
        # This design exports FIXED_IO, not an EMIO UART interface.
        if {![regexp {^MIO [0-9]+ \.\. [0-9]+$} [dict get $config CONFIG.PCW_UART${uart}_UART${uart}_IO]]} {
            error "BOARD_PS_CONFIG_INVALID: UART$uart requires an explicit MIO route"
        }
        set full [dict get $config CONFIG.PCW_UART${uart}_GRP_FULL_ENABLE]
        if {![string is boolean -strict $full] || $full} {
            error "BOARD_PS_CONFIG_INVALID: UART$uart must explicitly disable full modem signals"
        }
        set baud [dict get $config CONFIG.PCW_UART${uart}_BAUD_RATE]
        if {![string is integer -strict $baud] || $baud <= 0} {
            error "BOARD_PS_CONFIG_INVALID: UART$uart requires a positive baud rate"
        }
    }
    if {!$uart_enabled} { error {BOARD_PS_CONFIG_INVALID: enable a PS UART for console output} }
}

proc aic::write_ps_table {path rows} {
    set handle [open $path w]
    fconfigure $handle -encoding utf-8 -translation lf
    try {
        foreach row $rows {
            set fields {}
            foreach field $row { lappend fields [format {"%s"} [string map [list \" \"\"] $field]] }
            puts $handle [join $fields "\t"]
        }
    } finally { close $handle }
}

proc aic::snapshot_ps_config {ps path} {
    set rows [list [list property effective]]
    foreach property [lsort [list_property $ps]] {
        if {[string match CONFIG.PCW_* $property]} {
            lappend rows [list $property [get_property $property $ps]]
        }
    }
    aic::write_ps_table $path $rows
    puts "PS_CONFIG_SNAPSHOT: [expr {[llength $rows] - 1}] properties; $path"
}

proc aic::ps_values_equal {expected actual} {
    if {$expected eq $actual} { return 1 }
    if {[string is boolean -strict $expected] && [string is boolean -strict $actual]} {
        return [expr {!!$expected == !!$actual}]
    }
    if {[string is double -strict $expected] && [string is double -strict $actual]} {
        # PS7 readback can round decimal MHz values to single precision.
        return [expr {abs(double($expected) - double($actual)) <= max(1e-6, abs(double($expected)) * 1e-6)}]
    }
    return 0
}

proc aic::apply_ps_config {ps config} {
    dict for {property value} $config {
        if {![regexp {^CONFIG\.PCW_[A-Z0-9_]+$} $property]} {
            error "Not a PS7 configuration property: $property"
        }
    }
    if {[dict size $config]} { set_property -dict $config $ps }
}

proc aic::audit_ps_config {ps requested path} {
    set effective {}
    set mismatches {}
    dict for {property expected} $requested {
        if {[catch {get_property $property $ps} actual]} {
            dict set mismatches $property [dict create expected $expected readback_error $actual]
        } else {
            dict set effective $property $actual
            if {![aic::ps_values_equal $expected $actual]} {
                dict set mismatches $property [dict create expected $expected actual $actual]
            }
        }
    }
    set audit [dict create requested $requested effective $effective mismatches $mismatches]
    set handle [open $path w]
    puts $handle {# PS7 property readback after BD validation; not hardware verification.}
    puts $handle [list set ps_config_audit $audit]
    close $handle
    set rows [list [list property requested effective status]]
    dict for {property expected} $requested {
        set actual {}
        if {[dict exists $effective $property]} { set actual [dict get $effective $property] }
        set status [expr {[dict exists $mismatches $property] ? "MISMATCH" : "MATCH"}]
        lappend rows [list $property $expected $actual $status]
    }
    aic::write_ps_table [file rootname $path].tsv $rows
    if {[dict size $mismatches]} {
        error "PS_CONFIG_MISMATCH: [dict keys $mismatches]; see $path"
    }
    puts "PS_CONFIG_AUDIT_PASS: [dict size $requested] properties; $path"
    return [dict size $requested]
}
