set observed {}
foreach name {clk rst_n awvalid awready wvalid wready bvalid bready rvalid rready
              fm_a_we fm_a_addr fm_a_wdata accel_soft_rst accel_start fm_write_count
              watch_clock_only_outputs} {
    set signal [get_objects /axi_if_v2_tb/$name]
    if {[llength $signal] != 1} { error "Missing AXI reset waveform signal: $name" }
    lappend observed $signal
}
open_vcd axi-reset-boundaries.vcd
log_vcd $observed
run all
close_vcd
quit
