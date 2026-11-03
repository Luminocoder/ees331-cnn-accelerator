source [file join [file dirname [info script]] .. .. board vivado hardware-handoff.tcl]
set checks 0
proc check {condition message} {
    if {!$condition} { error $message }
    incr ::checks
}
proc fixture_file {path content} {
    set stream [open $path w]
    puts -nonewline $stream $content
    close $stream
}
set scratch [file tempfile temporary]
close $scratch
file delete $temporary
file mkdir $temporary
set scenario 0
proc reset_fixture {} {
    incr ::scenario
    set ::fixture [file join $::temporary $::scenario]
    set ::run_dir [file join $::fixture impl_1]
    set ::output [file join $::fixture output]
    file mkdir $::run_dir $::output
    set ::calls {}
    set ::registered 0
    set ::progress 100%
    set ::status {write_bitstream Complete!}
    set ::emit_bit 1
    set ::emit_xsa 1
    set ::bit_content MOCK_BIT
    set ::xsa_content MOCK_XSA
}
proc close_design {} { lappend ::calls close_design }
proc launch_runs {name args} {
    check [expr {$name eq "impl_1" && $args eq {-to_step write_bitstream -jobs 2}}] {Wrong bitstream run request}
    lappend ::calls launch_runs
}
proc wait_on_run {name} {
    lappend ::calls wait_on_run
    set ::registered 1
    if {$::emit_bit} { fixture_file [file join $::run_dir system_wrapper.bit] $::bit_content }
}
proc get_runs {name} { return $name }
proc get_property {key object} {
    switch -- $key {
        PROGRESS { return $::progress }
        STATUS { return $::status }
        DIRECTORY { return $::run_dir }
        TOP { return system_wrapper }
        default { error "Unexpected property: $key" }
    }
}
proc current_fileset {} { return sources_1 }
proc open_run {name} { lappend ::calls open_run }
proc write_hw_platform {args} {
    if {!$::registered} { error {Unable to get BIT file from implementation run} }
    check [expr {[lrange $args 0 2] eq {-fixed -include_bit -file}}] {XSA must include bitstream}
    lappend ::calls write_hw_platform
    if {$::emit_xsa} { fixture_file [lindex $args 3] $::xsa_content }
}

reset_fixture
fixture_file [file join $output system.bit] MOCK_STANDALONE_BIT
check [catch {write_hw_platform -fixed -include_bit -file [file join $output system.xsa]}] \
    {Unregistered standalone bitstream unexpectedly exported}
reset_fixture
aic::export_board_handoff $output
check [expr {$calls eq {close_design launch_runs wait_on_run open_run write_hw_platform}}] {Run/export order changed}
set stream [open [file join $output system.bit] r]
set copied [read $stream]
close $stream
check [expr {$copied eq $bit_content}] {Copied bitstream differs from run output}
check [file isfile [file join $output system.xsa]] {XSA was not exported}
foreach {variable value marker} {
    progress 99% {Run impl_1 failed}
    status {write_bitstream ERROR} {Run impl_1 failed}
    emit_bit 0 BOARD_HANDOFF_MISSING_BIT
    bit_content {} BOARD_HANDOFF_MISSING_BIT
    emit_xsa 0 BOARD_HANDOFF_MISSING_XSA
    xsa_content {} BOARD_HANDOFF_MISSING_XSA
} {
    reset_fixture
    set ::$variable $value
    set failed [catch {aic::export_board_handoff $output} message]
    check [expr {$failed && [string first $marker $message] >= 0}] "Invalid handoff accepted: $variable"
}
reset_fixture
fixture_file [file join $output system.bit] KEEP_EXISTING
check [catch {aic::export_board_handoff $output}] {Existing bitstream was overwritten}
puts "HARDWARE_HANDOFF_TESTS_PASS: $checks mock checks; fixtures: $temporary (not hardware validation)"
