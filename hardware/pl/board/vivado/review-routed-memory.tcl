# Read-only netlist evidence for the baked-weight BRAM's unused port A.
proc review_routed_memory {arguments} {
    if {[llength $arguments] != 2} { error "Usage: review-routed-memory.tcl <routed.dcp> <fresh-output>" }
    lassign $arguments checkpoint output
    if {[file exists $output]} { error "Use a fresh output directory: $output" }
    file mkdir $output
    set_param general.maxThreads 2
    open_checkpoint $checkpoint
    set records {}
    set handle [open [file join $output memory-review.rpt] w]
    puts $handle "checkpoint=[file normalize $checkpoint]"
    puts $handle "Evidence only: no BRAM functional test or hardware operation."
    set rams [get_cells -hier -filter {REF_NAME == RAMB36E1 && NAME =~ */u_w_bram/*}]
    if {![llength $rams]} { error "No weight RAMB36E1 cells in checkpoint" }
    foreach ram [lsort $rams] {
        puts $handle "\ncell=$ram"
        set record {}
        foreach key {RAM_EXTENSION_A RAM_EXTENSION_B READ_WIDTH_A READ_WIDTH_B WRITE_WIDTH_A WRITE_WIDTH_B} {
            dict set record $key [get_property $key $ram]
            puts $handle "$key=[dict get $record $key]"
        }
        foreach pin [lsort [get_pins -of_objects $ram -filter {DIRECTION == IN}]] {
            set name [get_property REF_PIN_NAME $pin]
            if {$name ni {ENARDEN ENBWREN {ADDRARDADDR[15]} {ADDRBWRADDR[15]}} &&
                ![string match {WEA*} $name] && ![string match {WEBWE*} $name]} { continue }
            set nets [get_nets -quiet -segments -of_objects $pin]
            set drivers [get_pins -quiet -of_objects $nets -filter {DIRECTION == OUT}]
            set cells [get_cells -quiet -of_objects $drivers]
            set refs {}
            if {[llength $cells]} { set refs [get_property REF_NAME $cells] }
            set net_properties {}
            foreach net $nets {
                set keys [list_property $net]
                foreach key {TYPE IS_GND IS_VCC} {
                    if {[lsearch -exact $keys $key] >= 0} {
                        dict set net_properties $net $key [get_property $key $net]
                    }
                }
            }
            dict set record pins $name [dict create nets $nets drivers $drivers driver_refs $refs net_properties $net_properties]
            puts $handle "${name}: driver_refs=$refs; drivers=$drivers; nets=$nets; net_properties=$net_properties"
        }
        foreach side {A B} {
            set pattern [expr {$side eq "A" ? "DOADO*" : "DOBDO*"}]
            set endpoints {}
            foreach pin [get_pins -of_objects $ram -filter {DIRECTION == OUT}] {
                if {![string match $pattern [get_property REF_PIN_NAME $pin]]} { continue }
                set nets [get_nets -quiet -of_objects $pin]
                foreach sink [get_pins -quiet -of_objects $nets -filter {DIRECTION == IN}] {
                    lappend endpoints $sink
                }
            }
            dict set record data_sinks $side [lsort -unique $endpoints]
            puts $handle "data_sinks_$side=[dict get $record data_sinks $side]"
        }
        dict set records $ram $record
    }
    # PDCN-137 concerns the disabled A port, not the active B-port cascade address.
    dict for {ram record} $records {
        set pins [dict get $record pins]
        foreach name {ENARDEN {ADDRARDADDR[15]} {WEA[0]} {WEA[1]} {WEA[2]} {WEA[3]} \
                      {WEBWE[0]} {WEBWE[1]} {WEBWE[2]} {WEBWE[3]} {WEBWE[4]} {WEBWE[5]} {WEBWE[6]} {WEBWE[7]}} {
            if {[dict get $pins $name driver_refs] ne "GND"} {
                error "Expected disabled/read-only weight RAM pin: $ram/$name"
            }
        }
        set address_nets [dict get $pins {ADDRBWRADDR[15]} net_properties]
        if {![dict size $address_nets]} { error "Missing B-port cascade address: $ram" }
        dict for {net properties} $address_nets {
            if {[dict get $properties TYPE] ne "SIGNAL"} { error "Constant B-port cascade address: $ram/$net" }
        }
    }
    puts $handle "\nunused_port_a_and_dynamic_port_b_verified=[dict size $records]"
    close $handle
    set handle [open [file join $output memory-review.tcl] w]
    puts $handle [list set routed_memory_review $records]
    close $handle
    close_design
    puts "MEMORY_REVIEW_COMPLETE: [dict size $records] weight RAM cells; port A disabled, B address dynamic; netlist evidence only"
}
if {[catch {review_routed_memory $argv} message options]} {
    puts stderr "MEMORY_REVIEW_FAIL: $message"
    puts stderr [dict get $options -errorinfo]
    exit 1
}
exit 0
