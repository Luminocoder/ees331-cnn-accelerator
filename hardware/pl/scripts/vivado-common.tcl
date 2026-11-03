namespace eval aic {
    variable root [file normalize [file join [file dirname [info script]] ..]]
    variable rtl [list rtl/accelerator/buffers.v rtl/accelerator/mac_array.sv \
        rtl/accelerator/controller.v rtl/accelerator/conv_engine.sv \
        rtl/accelerator/fc_engine.sv rtl/accelerator/pool_engine.sv \
        rtl/accelerator/cnn_accel_top.sv rtl/soc/axi_if_v2.v rtl/soc/mnist_soc_top.sv]
    variable weights [dict create W_CONV1_FILE Conv1_int8.txt W_CONV2_FILE Conv2_int8.txt \
        W_FC1_FILE FC1_int8.txt W_FC2_FILE FC2_int8.txt W_BIAS_FILE bias_layout.txt]
}

proc aic::require_file {path} {
    if {![file isfile $path]} { error "Required file not found: $path" }
    return $path
}

proc aic::read_rtl {} {
    variable root
    variable rtl
    foreach path $rtl { read_verilog -sv [require_file [file join $root $path]] }
    variable weights
    dict for {parameter filename} $weights {
        require_file [file join $root ref_model weights_int8 $filename]
    }
}
