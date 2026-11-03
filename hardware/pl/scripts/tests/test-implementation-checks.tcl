source [file join [file dirname [info script]] .. .. board vivado implementation-checks.tcl]
set checks 0
proc check {condition message} {
    if {!$condition} { error $message }
    incr ::checks
}
set scratch [file tempfile temporary]
close $scratch
file delete $temporary
file mkdir $temporary
set properties {}
set issues {}
set capped 0
proc get_drc_checks {rule} { return $rule }
proc set_property {key value object} { dict set ::properties $object $key $value }
proc report_drc {args} { set ::report_arguments $args }
proc get_drc_violations {args} {
    if {[lindex $args end] eq "CHECK-3*"} {
        if {$::capped} { return CHECK-3#1 }
        return {}
    }
    set pattern [lindex $args end]
    if {$pattern eq "-quiet"} { return [dict keys $::issues] }
    return [lsearch -all -inline -glob [dict keys $::issues] $pattern]
}
proc get_property {key object} {
    if {$key eq "NAME"} { return $object }
    return [dict get $::issues $object]
}
dict set issues DPIP-1#1 Warning
set audit [aic::report_board_drc $temporary post-synth]
check [expr {[dict get $audit counts Warning] == 1}] {Warning count missing}
check [expr {[dict get $audit decks] eq "default"}] {Synthesis DRC deck changed}
check [expr {[lsearch -exact $report_arguments -no_waivers] >= 0}] {DRC waivers were not ignored}
dict for {rule fields} $properties {
    check [expr {[dict get $fields MAX_MESSAGES] == 100000}] {DRC message limit not expanded}
    check [expr {[dict get $fields MAX_NAMES] == 100000}] {DRC name limit not expanded}
    check [expr {![dict exists $fields SEVERITY] && ![dict exists $fields IS_ENABLED]}] {DRC severity or enable flag changed}
}
set audit [aic::report_board_drc $temporary post-route 1]
check [expr {[dict get $audit decks] eq {default bitstream_checks}}] {Final DRC omits bitstream checks}
source [file join $temporary drc-audit-post-route.tcl]
check [expr {$board_drc_audit eq $audit}] {Native audit lost actual DRC data}
check [expr {[dict get $audit async_bram_controls] == 0}] {Clean BRAM control audit missing}
dict set issues REQP-1839#1 Warning
check [catch {aic::report_board_drc $temporary asynchronous}] {BRAM asynchronous control warning was accepted}
dict unset issues REQP-1839#1
set capped 1
check [catch {aic::report_board_drc $temporary capped}] {Limited DRC report was accepted}
set capped 0
dict set issues BROKEN#1 Error
check [catch {aic::report_board_drc $temporary broken}] {DRC error was accepted}
dict set issues BROKEN#1 {Critical Warning}
check [catch {aic::report_board_drc $temporary critical}] {DRC critical warning was accepted}
file delete -force $temporary
puts "IMPLEMENTATION_CHECKS_PASS: $checks checks"
