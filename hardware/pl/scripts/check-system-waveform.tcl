set folder [file normalize [lindex $argv 0]]
open_wave_database $folder/system-full.wdb
foreach name {system-overview system-detail system-input system-fc2-output} {
    open_wave_config $folder/$name.wcfg
    set count 0
    foreach wave [get_waves -r *] {
        if {[get_property CLASS $wave] ne "wave_design_object"} { continue }
        set path [get_property DESIGN_OBJECT $wave]
        if {$path eq ""} {
            regsub {\[[^]]+\]$} [get_property FULL_NAME $wave] {} path
        }
        if {![string match /* $path]} { continue }
        if {[llength [get_objects $path]] != 1} { error "Unresolved waveform object: $path" }
        if {[regexp {/(result_[0-9]|fc_out_wdata|result_read_data|accum_reg|bias_reg)$} $path]} {
            if {[get_property RADIX $wave] ne "dec"} { error "Signed radix missing: $path" }
        }
        incr count
    }
    if {$count < 20} { error "Too few recorded objects in $name: $count" }
    puts "REOPEN_CHECK $name signals=$count"
}
puts "SYSTEM_WAVEFORM_REOPEN_PASS: four configs; resolved WDB signals; signed results"
exit
