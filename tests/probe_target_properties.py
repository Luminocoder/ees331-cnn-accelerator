"""Read JTAG target properties twice; never reset, halt, program or access memory."""
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT/"source"))
import flashlink


def main():
    host = flashlink.InitializationHost()
    workspace = host.work_root/("target-probe-"+uuid.uuid4().hex)
    workspace.mkdir(parents=True)
    log = ROOT/"integration/fix-20261031-tcl/target-properties.log"
    script = workspace/"targets-readonly.tcl"
    script.write_text('''
for {set cycle 0} {$cycle < 3} {incr cycle} {
    connect -url TCP:localhost:3121
    try {
        foreach filter {{name == "APU"} {name =~ "*Cortex-A9*#0"} {name =~ "xc7z020*"}} {
            puts "CYCLE=$cycle FILTER=$filter PROPERTIES=[targets -target-properties -filter $filter]"
        }
        puts "CYCLE=$cycle ALL=[targets -target-properties]"
    } finally {disconnect}
}
exit 0
''', encoding="ascii")
    owned, process = None, None
    env = flashlink.environment(host.vivado)
    binary = host.vivado/"bin/unwrapped/win64.o"
    with (log.parent/"target-probe-server.log").open("wb") as server_log, log.open("wb") as output:
        try:
            if not flashlink.server_available():
                owned = flashlink.vendor_popen([str(binary/"hw_server.exe"), "-sTCP::3121", "-I60"],
                                              cwd=workspace, env=env, creationflags=flashlink.FLAGS,
                                              stdin=subprocess.DEVNULL, stdout=server_log, stderr=subprocess.STDOUT)
                deadline = time.monotonic()+15
                while not flashlink.server_available():
                    if owned.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError("hw_server not ready")
                    time.sleep(.1)
            process = flashlink.vendor_popen([str(binary/"xsdb.exe"), "-n", str(script)],
                                            cwd=workspace, env=env, creationflags=flashlink.FLAGS,
                                            stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
            code = process.wait(timeout=30)
            if code:
                raise RuntimeError("target property probe failed")
        finally:
            flashlink.stop_owned(process)
            flashlink.stop_owned(owned)
    print(log.read_text(encoding="utf-8", errors="replace"))
    print(json.dumps({"workspace": str(workspace), "read_only_jtag": True,
                      "reset_or_program": False, "log": str(log)}))


if __name__ == "__main__":
    main()
