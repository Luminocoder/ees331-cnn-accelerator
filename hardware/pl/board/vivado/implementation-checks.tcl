namespace eval aic {}

proc aic::report_board_drc {output stage {include_bitstream_checks 0}} {
    # Expand known limited rules without disabling checks, waivers or severities.
    foreach rule {DPIP-1 DPOP-1 DPOP-2 REQP-1839} {
        set check [get_drc_checks $rule]
        if {[llength $check] != 1} { error "Expected one DRC check: $rule" }
        set_property MAX_MESSAGES 100000 $check
        set_property MAX_NAMES 100000 $check
    }
    set decks {default}
    if {$include_bitstream_checks} { lappend decks bitstream_checks }
    report_drc -ruledecks $decks -no_waivers -file [file join $output drc-$stage.rpt]
    set counts [dict create Error 0 {Critical Warning} 0 Warning 0]
    set violations {}
    foreach violation [get_drc_violations -quiet] {
        set severity [get_property SEVERITY $violation]
        if {$severity ni {Error {Critical Warning} Warning Advisory Info}} {
            error "Unknown DRC severity for $violation: $severity"
        }
        dict incr counts $severity
        dict set violations [get_property NAME $violation] $severity
    }
    set capped [llength [get_drc_violations -quiet CHECK-3*]]
    set async_controls [llength [get_drc_violations -quiet REQP-1839*]]
    set audit [dict create stage $stage decks $decks no_waivers 1 \
        counts $counts capped_rules $capped async_bram_controls $async_controls violations $violations]
    set handle [open [file join $output drc-audit-$stage.tcl] w]
    puts $handle [list set board_drc_audit $audit]
    close $handle
    puts "DRC_AUDIT: $stage; counts=$counts; capped=$capped; no_waivers=1"
    if {$capped} { error "DRC report is limited; inspect CHECK-3 before accepting $stage" }
    if {$async_controls} { error "BRAM async control gate failed for $stage: REQP-1839=$async_controls" }
    if {[dict get $counts Error] || [dict get $counts {Critical Warning}]} {
        error "DRC gate failed for $stage; no severity downgrade or waiver is permitted"
    }
    return $audit
}
