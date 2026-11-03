source [file join [file dirname [info script]] .. board vivado ps-config.tcl]
if {[llength $argv] != 2} { error {Usage: export-ps-profile.tcl config.tcl output.tsv} }
source [lindex $argv 0]
set config [dict get $board_config ps_config]
aic::validate_board_ps_config $config
set rows [list [list property requested]]
dict for {property value} $config { lappend rows [list $property $value] }
aic::write_ps_table [lindex $argv 1] $rows
puts "PS_PROFILE_EXPORT_PASS: [dict size $config] properties; confirmed=[dict get $board_config confirmed]; no hardware"
