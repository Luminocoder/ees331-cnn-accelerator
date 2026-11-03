proc package_accelerator {output_dir part} {
    create_project accelerator_package [file join $output_dir package_project] -part $part
    set_property target_language Verilog [current_project]
    aic::read_rtl
    set_property top mnist_soc_top [current_fileset]
    update_compile_order -fileset sources_1
    set ip_root [file join $output_dir ip_repo mnist_accel]
    ipx::package_project -root_dir $ip_root -vendor user.org -library user \
        -taxonomy /UserIP -import_files -set_current true
    set core [ipx::current_core]
    set_property name mnist_accel $core
    set_property version 1.0 $core
    set_property display_name {MNIST INT8 accelerator} $core
    set_property description {AXI-Lite image input and baked-weight integer CNN inference} $core
    set_property supported_families {zynq Production} $core

    set bus [ipx::get_bus_interfaces s_axi -of_objects $core]
    if {![llength $bus]} { set bus [ipx::add_bus_interface s_axi $core] }
    set_property bus_type_vlnv xilinx.com:interface:aximm:1.0 $bus
    set_property abstraction_type_vlnv xilinx.com:interface:aximm_rtl:1.0 $bus
    set_property interface_mode slave $bus
    foreach logical {AWADDR AWVALID AWREADY WDATA WSTRB WVALID WREADY BRESP BVALID BREADY \
                     ARADDR ARVALID ARREADY RDATA RRESP RVALID RREADY} {
        set mapping [ipx::get_port_maps $logical -of_objects $bus]
        if {![llength $mapping]} { set mapping [ipx::add_port_map $logical $bus] }
        set_property physical_name s_axi_[string tolower $logical] $mapping
    }
    foreach {name value} {PROTOCOL AXI4LITE ADDR_WIDTH 32 DATA_WIDTH 32} {
        set parameter [ipx::get_bus_parameters $name -of_objects $bus]
        if {![llength $parameter]} { set parameter [ipx::add_bus_parameter $name $bus] }
        set_property value $value $parameter
    }
    set memory [ipx::get_memory_maps s_axi -of_objects $core]
    if {![llength $memory]} { set memory [ipx::add_memory_map s_axi $core] }
    set block [ipx::get_address_blocks reg0 -of_objects $memory]
    if {![llength $block]} { set block [ipx::add_address_block reg0 $memory] }
    set_property base_address 0 $block
    set_property range 65536 $block
    set_property width 32 $block
    set_property usage register $block
    set_property slave_memory_map_ref s_axi $bus

    foreach {name bus_type logical physical} {
        clk clock CLK clk
        rst_n reset RST rst_n
    } {
        set interface [ipx::get_bus_interfaces $name -of_objects $core]
        if {![llength $interface]} { set interface [ipx::add_bus_interface $name $core] }
        set_property bus_type_vlnv xilinx.com:signal:${bus_type}:1.0 $interface
        set_property abstraction_type_vlnv xilinx.com:signal:${bus_type}_rtl:1.0 $interface
        set_property interface_mode slave $interface
        set mapping [ipx::get_port_maps $logical -of_objects $interface]
        if {![llength $mapping]} { set mapping [ipx::add_port_map $logical $interface] }
        set_property physical_name $physical $mapping
    }
    foreach {interface_name name value} {
        clk ASSOCIATED_BUSIF s_axi
        clk ASSOCIATED_RESET rst_n
        clk FREQ_HZ 50000000
        rst_n POLARITY ACTIVE_LOW
    } {
        set interface [ipx::get_bus_interfaces $interface_name -of_objects $core]
        set parameter [ipx::get_bus_parameters $name -of_objects $interface]
        if {![llength $parameter]} { set parameter [ipx::add_bus_parameter $name $interface] }
        set_property value $value $parameter
    }
    dict for {name filename} $aic::weights {
        set value [file normalize [file join $aic::root ref_model weights_int8 $filename]]
        foreach parameter [concat [ipx::get_user_parameters $name -of_objects $core] \
                                  [ipx::get_hdl_parameters $name -of_objects $core]] {
            set_property value $value $parameter
        }
    }
    foreach group [ipx::get_file_groups -of_objects $core] {
        foreach source [ipx::get_files -of_objects $group] {
            if {[file extension [get_property name $source]] in {.v .sv}} {
                set_property is_include false $source
            }
        }
    }
    ipx::create_xgui_files $core
    ipx::update_checksums $core
    ipx::check_integrity $core
    ipx::save_core $core
    ipx::unload_core $core
    close_project
    return [file dirname $ip_root]
}
