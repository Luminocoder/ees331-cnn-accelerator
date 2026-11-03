"""Explicit HELLO/DISPLAY0 probe; never JTAG, initialization or IMAGE inference."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT/"source"))
import ees331_studio as studio


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--skip-drain", action="store_true")
    args = parser.parse_args()
    if args.skip_drain:
        studio.serial_link.drain = lambda *a, **kw: None
    original = studio.serial_link.open_port
    traffic = []
    class Recorded:
        def __init__(self, stream): self.stream = stream
        def read(self, size):
            data = self.stream.read(size)
            if data: traffic.append({"direction": "rx", "hex": data.hex()})
            return data
        def write(self, data):
            count = self.stream.write(data)
            traffic.append({"direction": "tx", "hex": bytes(data[:count]).hex()})
            return count
        def close(self): self.stream.close()
    studio.serial_link.open_port = lambda *a, **kw: Recorded(original(*a, **kw))
    report = {"port": args.port, "backend": studio.serial_link.backend_name(),
              "jtag_accessed": False, "inference_run": False, "skip_drain": args.skip_drain}
    board = studio.Board()
    try:
        board.connect(args.port)
        board.hello()
        report["hello_verified"] = True
        report["display_ack"] = board.set_display(0)
        report["status"] = "UART_READINESS_PASS"
        code = 0
    except Exception as exc:
        report.update(status="UART_READINESS_FAIL", error=str(exc))
        code = 1
    finally:
        board.close()
    report["traffic"] = traffic
    path = ROOT/"integration/fix-20261031-tcl"/("uart-probe-%d.json" % time.time_ns())
    path.write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
