set tb /system_waveform_tb/u_test
set soc $tb/u_dut
set acc $soc/u_accel

proc signals {scope names} {
    set result {}
    foreach name $names {
        set object [get_objects $scope/$name]
        if {[llength $object] != 1} { error "Missing waveform signal: $scope/$name" }
        lappend result $object
    }
    return $result
}

set overview [signals $soc {accel_start accel_busy accel_done irq_done accel_error layer_status}]
set schedule [signals $acc {layer_id conv_start conv_done pool_start pool_done fc_start fc_done c_is_fc2}]
set input [signals $tb {awaddr awvalid awready wdata wstrb wvalid wready bresp bvalid bready}]
set input_mem [signals $acc {fm_a_we fm_a_addr fm_a_wdata}]
set config [signals $acc {layer_cfg c_ifm_size c_ofm_size c_ifm_ch c_ofm_ch c_ifm_len c_ofm_len c_ifm_base c_ofm_base c_w_base c_bias_base c_S_layer}]
set memory [signals $acc {fm_b_we fm_b_addr fm_b_wdata fm_b_rdata w_b_addr w_b_rdata conv_fm_we pool_fm_we fc_fm_we}]
set conv [signals $acc/u_conv {state ox_cnt oy_cnt oc_cnt ic_cnt kx_cnt ky_cnt accum_reg bias_reg}]
set pool [signals $acc/u_pool {state ox_cnt oy_cnt c_cnt max_reg}]
set fc [signals $acc/u_fc {state oc_cnt req_cnt calc_cnt valid_shift accum_reg bias_reg}]
set result_write [signals $acc {fc_out_we fc_out_addr fc_out_wdata}]
set results [signals $soc {result_0 result_1 result_2 result_3 result_4 result_5 result_6 result_7 result_8 result_9}]
set readback [signals $tb {araddr arvalid arready rdata rresp rvalid rready}]
set clock_reset [signals $tb {clk rst_n}]
set result_bus [signals /system_waveform_tb {result_read_valid result_read_ready result_read_address result_read_data}]

set all_signals [concat $clock_reset $overview $schedule $input $input_mem $config $memory $conv $pool $fc $result_write $results $readback $result_bus]
log_wave -quiet $all_signals

proc save_view {name groups} {
    create_wave_config $name
    foreach {label objects} $groups {
        set group [add_wave_group $label]
        foreach object $objects {
            set wave [add_wave -into $group $object]
            if {[regexp {/(result_[0-9]|fc_out_wdata|result_read_data|accum_reg|bias_reg)$} $object]} {
                set_property radix dec $wave
            } elseif {[regexp {/(layer_status|layer_id|state|.*_cnt|c_ifm_len|c_ofm_len)$} $object]} {
                set_property radix unsigned $wave
            }
        }
    }
    save_wave_config $name.wcfg
}

save_view system-overview [list 01_system $overview 02_layers $schedule 03_fc2_write $result_write 04_signed_results $results 05_result_readback $result_bus]
save_view system-detail [list 01_clock_reset $clock_reset 02_system $overview 03_schedule $schedule 04_axi_input $input 05_input_bram $input_mem 06_layer_config $config 07_bram_datapath $memory 08_conv_engine $conv 09_pool_engine $pool 10_fc_engine $fc 11_fc2_write $result_write 12_signed_results $results 13_axi_readback $readback]
save_view system-input [list 01_clock_reset $clock_reset 02_axi_input $input 03_input_bram $input_mem 04_start $overview]
save_view system-fc2-output [list 01_clock_reset $clock_reset 02_system $overview 03_fc2_schedule $schedule 04_fc_datapath $fc 05_memory $memory 06_fc2_write $result_write 07_signed_results $results 08_axi_readback $readback]

# A small, independent value-change trace permits evidence-backed overview images.
open_vcd system-overview.vcd
log_vcd [concat [signals $tb {rst_n}] $overview $schedule $result_write $results $input $input_mem $result_bus]
run all
close_vcd
puts "WAVEFORM_RECORDED time=[current_time] done=[get_value $soc/accel_done]"
quit
