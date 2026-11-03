# Repair the packaged MNIST accelerator IP and rerun Zynq synthesis.
# This script intentionally uses ASCII-only working paths for Vivado.

proc step {message} {
    puts "\n=== $message ==="
}

proc require_file {path} {
    if {![file isfile $path]} {
        error "Required file does not exist: $path"
    }
}

proc find_ipx_file {file_group relative_name} {
    foreach ip_file [ipx::get_files -of_objects $file_group] {
        set candidate [string map {\\ /} [get_property NAME $ip_file]]
        if {$candidate eq $relative_name || [string match "*/$relative_name" $candidate]} {
            return $ip_file
        }
    }
    return ""
}

set vivado_project "D:/aic/vivado_mnist_zynq/vivado_mnist_zynq.xpr"
set bd_file "D:/aic/vivado_mnist_zynq/vivado_mnist_zynq.srcs/sources_1/bd/system/system.bd"
set ip_repo "D:/aic/ip_repo"
set ip_root "$ip_repo/mnist_accel_ip"
set component_xml "$ip_root/component.xml"
set result_dir "D:/aic/codex_vivado_fix"

set rtl_files [list \
    "src/axi_if_v2.v" \
    "src/buffers.v" \
    "src/controller.v" \
    "src/cnn_accel_top.sv" \
    "src/conv_engine.sv" \
    "src/fc_engine.sv" \
    "src/mac_array.sv" \
    "src/pool_engine.sv" \
    "src/mnist_soc_top.sv" \
]

require_file $vivado_project
require_file $bd_file
require_file $component_xml
foreach relative_name $rtl_files {
    require_file "$ip_root/$relative_name"
}
file mkdir $result_dir

step "Back up and open packaged IP"
set timestamp [clock format [clock seconds] -format "%Y%m%d_%H%M%S"]
set component_backup "$result_dir/component_before_fix_$timestamp.xml"
file copy -force $component_xml $component_backup
puts "Backup: $component_backup"

set opened_core [ipx::open_core $component_xml]
set core [ipx::current_core]
puts "ipx::open_core returned: <$opened_core>"
puts "ipx::current_core returned: <$core>"
if {[llength $core] == 0 && [llength $opened_core] > 0} {
    set core $opened_core
}
if {[llength $core] == 0} {
    error "Could not open a packaged IP core from $component_xml"
}

set core_vlnv [get_property VLNV $core]
puts "Opened core: $core_vlnv"
if {$core_vlnv ne "user.org:user:mnist_accel:1.0"} {
    error "Unexpected packaged IP VLNV: $core_vlnv"
}

step "Make RTL files compile units"
set group_names [list \
    "xilinx_anylanguagesynthesis" \
    "xilinx_anylanguagebehavioralsimulation" \
]

foreach group_name $group_names {
    set file_group [ipx::get_file_groups $group_name -of_objects $core]
    if {[llength $file_group] != 1} {
        error "Missing or ambiguous IP file group: $group_name"
    }

    foreach relative_name $rtl_files {
        set ip_file [find_ipx_file $file_group $relative_name]
        if {$ip_file eq ""} {
            error "IP file group $group_name is missing $relative_name"
        }
        set_property IS_INCLUDE false $ip_file
        if {[get_property IS_INCLUDE $ip_file]} {
            error "Failed to clear IS_INCLUDE for $relative_name in $group_name"
        }
        puts "$group_name: compile $relative_name"
    }
}

step "Repair clock and reset metadata"
set clk_if [ipx::get_bus_interfaces clk -of_objects $core]
if {[llength $clk_if] != 1} {
    error "Expected exactly one clock interface named clk"
}

set assoc_reset [ipx::get_bus_parameters ASSOCIATED_RESET -of_objects $clk_if]
set typo_reset [ipx::get_bus_parameters ASSOCIATED_RESIT -of_objects $clk_if]

if {[llength $assoc_reset] == 0 && [llength $typo_reset] == 1} {
    if {[catch {set_property NAME ASSOCIATED_RESET $typo_reset} rename_error]} {
        puts "Direct parameter rename was unavailable: $rename_error"
        set assoc_reset [ipx::add_bus_parameter ASSOCIATED_RESET $clk_if]
        ipx::remove_bus_parameter $typo_reset
    } else {
        set assoc_reset $typo_reset
    }
} elseif {[llength $assoc_reset] == 0} {
    set assoc_reset [ipx::add_bus_parameter ASSOCIATED_RESET $clk_if]
} elseif {[llength $assoc_reset] > 1} {
    error "Clock interface has multiple ASSOCIATED_RESET parameters"
}

set_property VALUE rst_n $assoc_reset
set remaining_typo [ipx::get_bus_parameters ASSOCIATED_RESIT -of_objects $clk_if]
if {[llength $remaining_typo] != 0} {
    ipx::remove_bus_parameter $remaining_typo
}

set assoc_busif [ipx::get_bus_parameters ASSOCIATED_BUSIF -of_objects $clk_if]
if {[llength $assoc_busif] == 0} {
    set assoc_busif [ipx::add_bus_parameter ASSOCIATED_BUSIF $clk_if]
}
set_property VALUE s_axi $assoc_busif

ipx::update_checksums $core
ipx::check_integrity $core
ipx::save_core $core
ipx::unload_core $core
puts "Saved repaired IP: $component_xml"

step "Open Zynq project and refresh IP catalog"
open_project $vivado_project
set_property IP_REPO_PATHS [list $ip_repo] [current_project]
update_ip_catalog -rebuild

set bd [get_files -quiet $bd_file]
if {[llength $bd] != 1} {
    error "Project does not contain the expected block design: $bd_file"
}
open_bd_design $bd_file

set accel_cell [get_bd_cells -quiet mnist_accel_0]
if {[llength $accel_cell] != 1} {
    error "Block design does not contain mnist_accel_0"
}
set cell_vlnv [get_property VLNV $accel_cell]
puts "Block design accelerator: $cell_vlnv"
if {$cell_vlnv ne "user.org:user:mnist_accel:1.0"} {
    error "Block design uses an unexpected accelerator IP: $cell_vlnv"
}

set accel_ips [get_ips -quiet *mnist_accel*]
if {[llength $accel_ips] > 0} {
    if {[catch {upgrade_ip $accel_ips} upgrade_message]} {
        puts "IP upgrade was not required or not available: $upgrade_message"
    }
}

step "Restore accelerator address assignment"
set address_space [get_bd_addr_spaces -quiet /processing_system7_0/Data]
set slave_segment [get_bd_addr_segs -quiet /mnist_accel_0/s_axi/reg0]
if {[llength $address_space] != 1 || [llength $slave_segment] != 1} {
    error "Could not resolve the PS data address space and accelerator reg0 segment"
}

set mapped_segment ""
set address_normalized 0
set excluded_segments [get_bd_addr_segs -quiet -excluded -of_objects $address_space]
foreach candidate $excluded_segments {
    if {[string match "*SEG_mnist_accel_0_reg0" $candidate]} {
        set mapped_segment $candidate
        break
    }
}

if {$mapped_segment ne ""} {
    puts "Excluded address before normalization: offset [get_property OFFSET $mapped_segment], range [get_property RANGE $mapped_segment]"
    set_property -dict [list OFFSET 0x43C00000 RANGE 64K] $mapped_segment
    puts "Including excluded segment: $mapped_segment"
    include_bd_addr_seg $mapped_segment
    set address_normalized 1
} else {
    set addressed_segments [get_bd_addr_segs -quiet -addressing -of_objects $slave_segment]
    foreach candidate $addressed_segments {
        if {[string match "*SEG_mnist_accel_0_reg0" $candidate]} {
            set mapped_segment $candidate
            puts "Mapped address before normalization: offset [get_property OFFSET $mapped_segment], range [get_property RANGE $mapped_segment]"
            set_property -dict [list OFFSET 0x43C00000 RANGE 64K] $mapped_segment
            set address_normalized 1
            break
        }
    }
}

if {!$address_normalized} {
    set assigned_segments [assign_bd_address -force -offset 0x43C00000 -range 64K \
        -target_address_space $address_space $slave_segment]
    if {[llength $assigned_segments] != 1} {
        error "Could not create the accelerator master address segment"
    }
    set address_normalized 1
}

set address_space [get_bd_addr_spaces -quiet /processing_system7_0/Data]
set excluded_after [get_bd_addr_segs -quiet -excluded -of_objects $address_space]
foreach candidate $excluded_after {
    if {[string match "*SEG_mnist_accel_0_reg0" $candidate]} {
        puts "Excluded address segments: <$excluded_after>"
        error "Accelerator address segment is still excluded from the PS data address space"
    }
}
set slave_segment [get_bd_addr_segs -quiet /mnist_accel_0/s_axi/reg0]
set mapped_segments [get_bd_addr_segs -quiet -addressing -of_objects $slave_segment]
if {[llength $mapped_segments] == 0} {
    puts "Addressed segment query is empty until the block design is saved and reopened."
}
foreach mapped_segment $mapped_segments {
    puts "Mapped segment: $mapped_segment, offset [get_property OFFSET $mapped_segment], range [get_property RANGE $mapped_segment]"
}

validate_bd_design -force
save_bd_design

step "Regenerate block-design output products"
if {[catch {reset_target all $bd} reset_message]} {
    puts "reset_target note: $reset_message"
}
generate_target all $bd

set wrapper_files [make_wrapper -files $bd -top]
foreach wrapper_file $wrapper_files {
    if {[llength [get_files -quiet $wrapper_file]] == 0} {
        add_files -norecurse $wrapper_file
    }
}
set_property TOP system_wrapper [get_filesets sources_1]
update_compile_order -fileset sources_1

step "Disable stale incremental synthesis checkpoint"
set synth_run [get_runs synth_1]
set run_properties [list_property $synth_run]
if {[lsearch -exact $run_properties INCREMENTAL_CHECKPOINT] >= 0} {
    set_property INCREMENTAL_CHECKPOINT "" $synth_run
}
if {[lsearch -exact $run_properties AUTO_INCREMENTAL_CHECKPOINT] >= 0} {
    set_property AUTO_INCREMENTAL_CHECKPOINT 0 $synth_run
}
puts "Incremental checkpoint: <[get_property INCREMENTAL_CHECKPOINT $synth_run]>"
puts "Automatic incremental checkpoint: <[get_property AUTO_INCREMENTAL_CHECKPOINT $synth_run]>"

step "Run synthesis"
reset_run synth_1
launch_runs synth_1 -jobs 2
wait_on_run synth_1

set synth_status [get_property STATUS [get_runs synth_1]]
set synth_progress [get_property PROGRESS [get_runs synth_1]]
puts "SYNTH_STATUS: $synth_status"
puts "SYNTH_PROGRESS: $synth_progress"

if {![string match "*Complete*" $synth_status]} {
    error "Synthesis did not complete successfully: $synth_status"
}

open_run synth_1
report_utilization -file "$result_dir/system_utilization_post_synth.rpt"
report_timing_summary -file "$result_dir/system_timing_post_synth.rpt"
report_drc -file "$result_dir/system_drc_post_synth.rpt"
write_checkpoint -force "$result_dir/system_wrapper_post_synth.dcp"

puts "VIVADO_ZYNQ_SYNTH_FIX_DONE"
puts "Reports: $result_dir"
close_project
