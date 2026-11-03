set root [file normalize [file join [file dirname [info script]] .. ..]]
source [file join $root board vivado ps-config.tcl]
set checks 0
proc check {condition message} {
    if {!$condition} { error $message }
    incr ::checks
}
proc must_fail {body message} {
    check [catch {uplevel 1 $body}] $message
}
check [aic::ps_values_equal 1 true] {Boolean readback normalization failed}
check [aic::ps_values_equal 0 false] {False readback normalization failed}
check [aic::ps_values_equal 533.333333 533.333313] {Frequency rounding rejected}
check [expr {![aic::ps_values_equal 533.333333 400.0]}] {Wrong frequency accepted}
check [expr {![aic::ps_values_equal 0.25 0.26]}] {Wrong DDR delay accepted}
check [expr {![aic::ps_values_equal {MIO 48 .. 49} {MIO 14 .. 15}]}] {Wrong UART pins accepted}
set properties {}
proc set_property {flag values object} {
    if {$flag ne "-dict" || $object ne "ps0"} { error {Invalid mock call} }
    set ::properties [dict merge $::properties $values]
}
proc get_property {property object} {
    return [dict get $::properties $property]
}
set requested [dict create CONFIG.PCW_UART1_PERIPHERAL_ENABLE 1 CONFIG.PCW_UART1_UART1_IO {MIO 48 .. 49}]
aic::apply_ps_config ps0 $requested
check [expr {$properties eq $requested}] {Candidate PS settings were not applied}
must_fail {aic::apply_ps_config ps0 {NOT_A_PS_PROPERTY 1}} {Non-PS property accepted}
set handle [file tempfile audit_path]
close $handle
check [expr {[aic::audit_ps_config ps0 $requested $audit_path] == 2}] {Readback audit count failed}
source $audit_path
check [expr {[dict get $ps_config_audit requested] eq $requested}] {Audit lost requested settings}
check [expr {[dict size [dict get $ps_config_audit mismatches]] == 0}] {Unexpected audit mismatch}
dict set properties CONFIG.PCW_UART1_PERIPHERAL_ENABLE 0
must_fail {aic::audit_ps_config ps0 $requested $audit_path} {Ignored PS setting accepted}
source $audit_path
check [dict exists $ps_config_audit mismatches CONFIG.PCW_UART1_PERIPHERAL_ENABLE] {Failed audit lost mismatch}
source [file join $root board vivado configs ees331-candidate.tcl]
check [expr {![dict get $board_config confirmed]}] {Candidate bypasses board confirmation}
check [expr {[dict get $board_config part] eq "xc7z020clg484-1"}] {Candidate part changed}
check [expr {[dict get $board_config ps_config CONFIG.PCW_UART1_UART1_IO] eq "MIO 48 .. 49"}] {Candidate UART route changed}
set candidate [dict get $board_config ps_config]
aic::validate_board_ps_config $candidate
check 1 {Complete candidate settings were rejected}
dict for {property value} $candidate {
    set incomplete [dict remove $candidate $property]
    must_fail {aic::validate_board_ps_config $incomplete} "Missing $property was accepted"
}
dict for {property value} [dict create CONFIG.PCW_UIPARAM_DDR_ENABLE 0 \
    CONFIG.PCW_UART1_PERIPHERAL_ENABLE 0 CONFIG.PCW_UART1_UART1_IO EMIO \
    CONFIG.PCW_UART1_GRP_FULL_ENABLE 1 CONFIG.PCW_UART1_BAUD_RATE 0 \
    CONFIG.PCW_CRYSTAL_PERIPHERAL_FREQMHZ -1 CONFIG.PCW_UIPARAM_DDR_FREQ_MHZ Inf \
    CONFIG.PCW_UIPARAM_DDR_TRAIN_WRITE_LEVEL invalid CONFIG.PCW_PRESET_BANK1_VOLTAGE {}] {
    set invalid [dict replace $candidate $property $value]
    must_fail {aic::validate_board_ps_config $invalid} "Invalid $property=$value was accepted"
}
set uart0 [dict replace $candidate CONFIG.PCW_UART1_PERIPHERAL_ENABLE 0 \
    CONFIG.PCW_UART0_PERIPHERAL_ENABLE 1 CONFIG.PCW_UART0_UART0_IO {MIO 50 .. 51} \
    CONFIG.PCW_UART0_GRP_FULL_ENABLE 0 CONFIG.PCW_UART0_BAUD_RATE 115200]
aic::validate_board_ps_config $uart0
check 1 {Complete UART0 route was rejected}
set properties $candidate
proc list_property {object} { return [dict keys $::properties] }
aic::snapshot_ps_config ps0 [file rootname $audit_path]-snapshot.tsv
check [file exists [file rootname $audit_path]-snapshot.tsv] {Effective PS snapshot missing}
set handle [open [file rootname $audit_path]-escaped.tsv w]
close $handle
aic::write_ps_table [file rootname $audit_path]-escaped.tsv [list [list key value] [list sample "a\t\"b\"\nc"]]
set handle [open [file rootname $audit_path]-escaped.tsv r]
set escaped [read $handle]
close $handle
check [expr {[string first {""b""} $escaped] >= 0}] {TSV quote escaping failed}
source [file join $root board vivado configs ees331-rev1-v1.tcl]
check [dict get $board_config confirmed] {Frozen profile is not confirmed}
check [expr {[dict get $board_config id] eq "ees331-rev1-v1"}] {Frozen board ID changed}
check [expr {[dict get $board_config part] eq "xc7z020clg484-1"}] {Frozen part changed}
check [expr {[dict get $board_config ps_config] eq $candidate}] {Frozen PS values differ from reviewed candidate}
check [expr {[dict get $board_config xdc_files] eq {}}] {Unexpected PL constraints in frozen profile}
check [expr {[dict get $board_config provenance] ne {}}] {Frozen provenance missing}
aic::validate_board_ps_config [dict get $board_config ps_config]
check 1 {Frozen PS profile is incomplete}
puts "PS_CONFIG_TESTS_PASS: $checks checks"
