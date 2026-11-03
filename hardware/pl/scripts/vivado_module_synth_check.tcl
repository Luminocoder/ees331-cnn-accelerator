set script_dir [file dirname [file normalize [info script]]]
set repo_root [file normalize [file join $script_dir ..]]

if {$argc < 1} {
    puts "Usage: vivado_module_synth_check.tcl <top>"
    exit 2
}

set top_name [lindex $argv 0]
set_param general.maxThreads 1

set run_dir [file join $repo_root "_vivado_${top_name}_synth_check"]
file mkdir $run_dir
create_project "ai_chip_${top_name}_synth_check" [file join $run_dir "ai_chip_${top_name}_synth_check"] -part xc7z020clg400-1 -force
set_property target_language Verilog [current_project]

if {$top_name eq "axi_if_v2"} {
    set rtl_files [list rtl/soc/axi_if_v2.v]
} elseif {$top_name eq "cnn_accel_top"} {
    set rtl_files [list \
        rtl/accelerator/buffers.v \
        rtl/accelerator/mac_array.sv \
        rtl/accelerator/controller.v \
        rtl/accelerator/conv_engine.sv \
        rtl/accelerator/fc_engine.sv \
        rtl/accelerator/pool_engine.sv \
        rtl/accelerator/cnn_accel_top.sv \
    ]
} else {
    puts "Unsupported top: $top_name"
    exit 2
}

foreach rtl_file $rtl_files {
    read_verilog -sv [file join $repo_root $rtl_file]
}

set_property top $top_name [current_fileset]
update_compile_order -fileset sources_1
synth_design -top $top_name -part xc7z020clg400-1

report_utilization -file [file join $run_dir utilization_post_synth.rpt]
write_checkpoint -force [file join $run_dir "${top_name}_post_synth.dcp"]

puts "VIVADO_MODULE_SYNTH_CHECK_DONE: $top_name"
close_project
