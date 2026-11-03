set script_dir [file dirname [file normalize [info script]]]
set repo_root [file normalize [file join $script_dir ..]]
source [file join $script_dir vivado-common.tcl]
cd $repo_root
set run_dir [file join $repo_root _vivado_synth_check_v2]

set_param general.maxThreads 1
file mkdir $run_dir
create_project ai_chip_synth_check_v2 [file join $run_dir ai_chip_synth_check_v2] -part xc7z020clg400-1 -force
set_property target_language Verilog [current_project]

aic::read_rtl

set_property top mnist_soc_top [current_fileset]
update_compile_order -fileset sources_1

set xdc_path [file join $run_dir mnist_soc_top_ooc.xdc]
set xdc_fd [open $xdc_path w]
puts $xdc_fd {create_clock -name clk -period 20.000 [get_ports clk]}
close $xdc_fd
read_xdc $xdc_path

synth_design -top mnist_soc_top -part xc7z020clg400-1

report_utilization -file [file join $run_dir utilization_post_synth.rpt]
report_timing_summary -file [file join $run_dir timing_post_synth.rpt]
report_drc -file [file join $run_dir drc_post_synth.rpt]
write_checkpoint -force [file join $run_dir mnist_soc_top_post_synth.dcp]

puts "VIVADO_SYNTH_CHECK_DONE: reports in $run_dir"
close_project
