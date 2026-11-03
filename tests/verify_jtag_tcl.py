"""Execute deployment Tcl against fake targets/memory, never XSDB/hardware.

Vendor PS init calls are stubbed; the unchanged DDR probe and control flow run.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent

MOCK = r'''
set ::events {}
set ::messages {}
set ::current cpu
set ::connections 0
set ::pending_queries 0
set ::maps {}
array set ::memory {}
proc puts {args} { lappend ::messages [lindex $args end] }
proc connect {args} { incr ::connections; set ::maps {}; lappend ::events connect }
proc disconnect {} { lappend ::events disconnect }
proc after {args} {}
proc targets {args} {
    if {[lindex $args 0] eq "-set"} { set ::current [lindex $args 1]; return }
    set filter [lindex $args end]
    set id cpu
    if {[string match *xc7z020* $filter]} {set id fpga}
    if {[string match *APU* $filter]} {set id apu}
    if {[string match *is_current* $filter]} {set id $::current}
    set cable 1234-tulA
    if {$::scenario eq "wrong_cable"} {set cable another-cable}
    set item [dict create target_id $id jtag_cable_serial $cable suspended 1]
    if {$::scenario eq "missing_cable"} {dict unset item jtag_cable_serial}
    if {$::scenario eq "properties_pending" && $id eq "apu" && $::connections >= 3 && $::pending_queries < 2} {
        incr ::pending_queries
        dict unset item jtag_cable_serial
    }
    if {$::scenario eq "ambiguous" && $id eq "fpga"} { return [list $item $item] }
    return [list $item]
}
proc stop {} { lappend ::events stop }
proc con {} { lappend ::events con }
proc rst {args} { lappend ::events reset }
proc fpga {args} { lappend ::events fpga }
proc dow {args} { lappend ::events dow }
proc memmap {args} {
    lappend ::events memmap
    set start [lindex $args [expr {[lsearch $args -addr]+1}]]
    set size [lindex $args [expr {[lsearch $args -size]+1}]]
    lappend ::maps [list [expr {$start}] [expr {$start+$size}]]
}
proc mrd {args} {
    set address [expr {[lindex $args end]}]
    if {$address >= 0x43C00000 && $address < 0x43C20000} {
        set allowed 0
        foreach region $::maps {
            if {$address >= [lindex $region 0] && $address < [lindex $region 1]} {set allowed 1}
        }
        if {!$allowed} {error "AXI debug access blocked: connection has no map for $address"}
    }
    if {$address == 0x43C10008} {
        return [expr {$::scenario eq "bad_magic" ? 0 : 0x53454731}]
    }
    if {$address == 0x43C1000C} {
        return [expr {$::scenario eq "old_page_pl" ? 0xDEADBEEF : 0x50414731}]
    }
    if {[info exists ::memory($address)]} {return $::memory($address)}
    return 0
}
proc mwr {address value} {
    set address [expr {$address}]
    set ::memory($address) [expr {$value}]
}
rename source original_source
proc source {path} {
    if {[file tail $path] eq "ps7_init.tcl"} {
        proc ::ps7_init {} {lappend ::events ps7_init}
        proc ::ps7_post_config {} {lappend ::events ps7_post_config}
        return
    }
    uplevel 1 [list original_source $path]
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tk-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.tk_root:
        root = args.tk_root.resolve()
        sys.path[:0] = [str(root/"Lib"), str(root/"DLLs")]
        os.environ["TCL_LIBRARY"] = os.path.relpath(root/"tcl/tcl8.6")
        os.environ["TK_LIBRARY"] = os.path.relpath(root/"tcl/tk8.6")
    import tkinter
    rows = []
    for scenario in ("success", "properties_pending", "missing_cable", "wrong_cable", "ambiguous", "bad_magic", "old_page_pl"):
        with tempfile.TemporaryDirectory(dir=ROOT/"integration") as temporary:
            stage = Path(temporary)
            for source, target in (("initialization/bringup.tcl", "board/bringup.tcl"),
                                   ("initialization/initialize.tcl", "initialize.tcl"),
                                   ("flash_payload/system.bit", "bitstream/system.bit"),
                                   ("flash_payload/app.elf", "arm_firmware/app.elf")):
                output = stage/target
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT/"resources"/source, output)
            (stage/"prepared").mkdir()
            (stage/"prepared/ps7_init.tcl").write_text("# stubbed by this test\n")
            (stage/"prepared/baseline.json").write_text("{}")
            interp = tkinter.Tcl()
            interp.eval(MOCK)
            interp.setvar("scenario", scenario)
            interp.setvar("env(EES331_PREFLIGHT_OK)", "1")
            error = None
            try:
                interp.call("source", str(stage/"initialize.tcl"))
            except tkinter.TclError as exc:
                error = str(exc)
            events = list(interp.splitlist(interp.getvar("events")))
            messages = list(interp.splitlist(interp.getvar("messages")))
            passed = "V5_DEPLOYMENT_PASS" in messages
            if scenario in ("success", "properties_pending"):
                assert error is None and passed, (error, messages)
                assert events.index("reset") < events.index("fpga") < events.index("ps7_init") < events.index("dow")
                assert any("DDR_PROBE_PASS" in message for message in messages)
                assert all(int(interp.getvar("memory(%d)" % (0x01000000+4*i))) == 0 for i in range(1024))
                if scenario == "properties_pending":
                    assert any("V5_TARGET_PROPERTIES_PENDING" in message for message in messages)
            else:
                assert error and not passed, (scenario, error, messages)
                if scenario in ("missing_cable", "wrong_cable", "ambiguous"):
                    assert "reset" not in events and "dow" not in events
            rows.append({"scenario": scenario, "error": error, "events": events, "messages": messages})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"hardware_accessed": False, "xsdb_launched": False,
                                      "vendor_ps_init_stubbed": True, "cases": rows}, indent=2)+"\n", encoding="utf-8")
    print("PASS: 7 Tcl scenarios; delayed properties recovered; invalid cable/targets/MAGIC and old page PL rejected")


if __name__ == "__main__":
    main()
