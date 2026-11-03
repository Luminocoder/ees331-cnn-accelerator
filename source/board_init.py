"""Payload hashes + guarded JTAG + HELLO/DISPLAY; never implicit inference."""
import hashlib
import json
from pathlib import Path
import re
import secrets
import struct
import sys
import time

import flashlink
import mnist_serial
import sample_log
import serial_link


def detect_port():
    """Only auto-select the validated FTDI UART, never a Bluetooth COM port."""
    try:
        import winreg
        available = {p.upper() for p, _ in serial_link.list_ports()}
        matches = []
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Enum\FTDIBUS") as bus:
            for index in range(winreg.QueryInfoKey(bus)[0]):
                name = winreg.EnumKey(bus, index)
                if name.upper() != "VID_0403+PID_6010+1234-TULB":
                    continue
                with winreg.OpenKey(bus, name) as device:
                    for child in range(winreg.QueryInfoKey(device)[0]):
                        with winreg.OpenKey(device, winreg.EnumKey(device, child)+r"\Device Parameters") as key:
                            port = str(winreg.QueryValueEx(key, "PortName")[0]).upper()
                            if port in available:
                                matches.append(port)
        return matches[0] if len(matches) == 1 else None
    except OSError:
        return None


def resource_root(application_root):
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)/"resources"
    return Path(application_root)/"resources"


def preflight(application_root):
    root = resource_root(application_root)
    manifest = json.loads((root/"deployment-manifest.json").read_text(encoding="utf-8"))
    expected = {"flash_payload/system.bit", "flash_payload/app.elf", "flash_payload/ps7_init.tcl",
                "initialization/bringup.tcl", "initialization/initialize.tcl"}
    if set(manifest["files"]) != expected:
        raise ValueError("Unexpected deployment payload layout")
    for name, item in manifest["files"].items():
        data = (root/name).read_bytes()
        if len(data) != item["bytes"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError("初始化载荷哈希不符："+name)
    data = (root/"flash_payload/app.elf").read_bytes()
    if data[:7] != b"\x7fELF\x01\x01\x01" or len(data) < 52:
        raise ValueError("Expected little-endian ELF32")
    header = struct.unpack_from("<16sHHIIIIIHHHHHH", data)
    if header[1:3] != (2, 40) or not 0x100000 <= header[4] < 0x200000 or header[9] != 32:
        raise ValueError("Unexpected ARM ELF entry or program-header format")
    loads = []
    for index in range(header[10]):
        ph = struct.unpack_from("<8I", data, header[5]+index*header[9])
        kind, offset, virtual, physical, size, memory, _, _ = ph
        if kind == 1:
            if not (0x100000 <= physical <= virtual < 0x200000 and physical+memory <= 0x200000
                    and size <= memory and offset+size <= len(data)):
                raise ValueError("ELF load overlaps unvalidated memory")
            loads.append({"address": physical, "memory_bytes": memory})
    if not loads:
        raise ValueError("ELF has no loadable segments")
    return {"resource_root": str(root), "files": manifest["files"], "elf_loads": loads,
            "inference_tests_run": False, "hardware_accessed": False,
            "fpga_model": "existing INT8 CNN; peer MLP is PC-only"}


def initialize(application_root, port, log_directory, host_factory=flashlink.InitializationHost,
               open_serial=serial_link.open_port):
    if not re.fullmatch(r"COM[1-9][0-9]*", port or "", re.IGNORECASE):
        raise ValueError("请先选择板卡串口COMx")
    checked = preflight(application_root)
    log_directory = Path(log_directory).resolve()
    log_directory.mkdir(parents=True, exist_ok=False)
    report = {"status": "INITIALIZING", "port": port, "preflight": checked,
              "initialization_verified": False, "inference_tests_run": False,
              "flash_or_sd_written": False, "hardware_model_attested": False}
    sample_log.write_json(log_directory/"results.json", report)
    try:
        with flashlink.initialization_lock():
            stream = open_serial(port, timeout=0)
            try:
                host = host_factory()
                report["deployment"] = host.deploy(Path(checked["resource_root"]), checked, log_directory)
                if report["deployment"].get("deployment_marker") is not True:
                    raise RuntimeError("JTAG部署未确认完成")
                boot = bytearray()
                while len(boot) < 65536:
                    chunk = stream.read(min(4096, 65536-len(boot)))
                    if not chunk:
                        break
                    boot.extend(chunk)
                (log_directory/"startup-uart.bin").write_bytes(boot)
                # Keep boot capture, then reacquire UART after the PS/PL reset.
                # A post-reset UART can receive the banner before its first
                # request succeeds. Reopen, then require a bounded real HELLO ACK.
                stream.close()
                stream = None
                stream = open_serial(port, timeout=0)
                report["uart_reopened_after_jtag"] = True
                # v4.4 prints a SEG banner, not the old UART_READY markers.
                sequence = secrets.randbits(32)
                started = time.monotonic()
                report["hello_timeout_seconds"] = 10.0
                # Send exactly one request. Short retries can leave late ACKs
                # queued and collide with the next sequence or DISPLAY command.
                reply = mnist_serial._exchange(stream, mnist_serial.encode_packet(mnist_serial.HELLO, sequence, mnist_serial.IDENTITY),
                                              mnist_serial.HELLO_ACK, sequence, 10.0)
                report["hello_response_seconds"] = time.monotonic()-started
                if reply != mnist_serial.IDENTITY:
                    raise RuntimeError("HELLO identity mismatch after initialization")
                shown = mnist_serial.set_display(stream, 0, secrets.randbits(32), 3.0)
                if shown != 0:
                    raise RuntimeError("DISPLAY readiness check did not acknowledge zero")
                report.update(status="ONECLICK_INITIALIZATION_PASS", initialization_verified=True,
                              hello_verified=True, display_ack_verified=True)
            finally:
                if stream is not None:
                    stream.close()
    except Exception as exc:
        report.update(status="INITIALIZATION_FAIL", error=str(exc))
        sample_log.write_json(log_directory/"results.json", report)
        raise RuntimeError("初始化失败：%s；LOG=%s" % (exc, log_directory)) from exc
    sample_log.write_json(log_directory/"results.json", report)
    return report
