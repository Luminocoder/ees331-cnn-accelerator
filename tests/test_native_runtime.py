"""Regression for frozen-GUI DLL contamination of native Vivado processes."""
import contextlib
import ctypes
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT/"source"))
import flashlink
import ees331_studio as studio


def dll_directory():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetDllDirectoryW.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p]
    kernel.GetDllDirectoryW.restype = ctypes.c_uint32
    buffer = ctypes.create_unicode_buffer(32768)
    kernel.GetDllDirectoryW(len(buffer), buffer)
    return buffer.value


class NativeRuntime(unittest.TestCase):
    def test_vendor_environment_excludes_bundle_tcl_and_path(self):
        bundle = str(ROOT/"_internal")
        inherited = {"PATH": os.pathsep.join([bundle, str(Path(bundle)/"numpy.libs"), "C:/Windows/System32"]),
                     "TCL_LIBRARY": "wrong-tcl", "TK_LIBRARY": "wrong-tk", "TCL_ROOT": "wrong-root",
                     "TCLLIBPATH": "wrong-packages"}
        with patch.object(sys, "_MEIPASS", bundle, create=True), patch.dict(os.environ, inherited):
            env = flashlink.environment(Path("D:/2025.1/Vivado"))
            self.assertEqual(env["TCL_LIBRARY"], "D:/2025.1/Vivado/tps/tcl/tcl8.6")
            self.assertNotIn("TK_LIBRARY", env)
            self.assertNotIn("TCL_ROOT", env)
            self.assertNotIn("_internal", env["PATH"])
            self.assertIn("C:/Windows/System32", env["PATH"])
            self.assertEqual(os.environ["TCL_LIBRARY"], "wrong-tcl")

    def test_process_creation_clears_and_restores_dll_directory_even_on_failure(self):
        # Use the actual Win32 DLL-search API, but never start a process.
        previous = dll_directory()
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
        kernel.SetDllDirectoryW.restype = ctypes.c_int
        contaminated = str(ROOT/"_internal")
        self.assertTrue(kernel.SetDllDirectoryW(contaminated))
        try:
            for failure in (False, True):
                def create(*args, **kwargs):
                    self.assertEqual(dll_directory(), "")
                    if failure:
                        raise OSError("synthetic CreateProcess failure")
                    return "child"
                with patch.object(flashlink.subprocess, "Popen", side_effect=create):
                    if failure:
                        with self.assertRaises(OSError): flashlink.vendor_popen(["unused.exe"])
                    else:
                        self.assertEqual(flashlink.vendor_popen(["unused.exe"]), "child")
                self.assertEqual(dll_directory(), contaminated)
        finally:
            kernel.SetDllDirectoryW(previous or None)

    def test_runtime_requires_real_success_marker_and_has_no_hardware_commands(self):
        for code, output in ((0, b"V5_XSDB_RUNTIME_PASS Tcl=8.6.13\n"), (1, b"version conflict\n"), (0, b"no marker\n")):
            with self.subTest(code=code, output=output), tempfile.TemporaryDirectory() as temporary:
                host = object.__new__(flashlink.InitializationHost)
                host.vivado = Path("D:/2025.1/Vivado")
                host.work_root = Path(temporary)/"work"
                def create(argv, **kwargs):
                    script = Path(argv[-1]).read_text()
                    self.assertNotIn("connect -", script)
                    for command in ("targets ", "rst ", "fpga ", "dow "):
                        self.assertNotIn(command, script)
                    self.assertEqual(dll_directory(), "")
                    kwargs["stdout"].write(output)
                    return SimpleNamespace(returncode=code, wait=lambda **kw: code, poll=lambda: code)
                log = Path(temporary)/"log"
                with patch.object(flashlink.subprocess, "Popen", side_effect=create):
                    if code or b"no marker" in output:
                        with self.assertRaises(RuntimeError): host.check_runtime(log)
                        expected = "XSDB_RUNTIME_FAIL"
                    else:
                        self.assertEqual(host.check_runtime(log)["status"], "XSDB_RUNTIME_PASS")
                        expected = "XSDB_RUNTIME_PASS"
                record = json.loads((log/"xsdb-runtime.json").read_text())
                self.assertEqual(record["status"], expected)
                self.assertFalse(record["hardware_accessed"])
                self.assertTrue(record["dll_directory_restored"])

    def test_deploy_aborts_before_hardware_server_if_runtime_fails(self):
        host = object.__new__(flashlink.InitializationHost)
        with patch.object(host, "check_runtime", side_effect=RuntimeError("runtime failed")), \
                patch.object(flashlink, "server_available", side_effect=AssertionError("Must not contact server")), \
                patch.object(flashlink, "vendor_popen", side_effect=AssertionError("Must not launch server")):
            with self.assertRaisesRegex(RuntimeError, "runtime failed"):
                host.deploy(None, None, None)

    def test_initialization_reopens_uart_before_hello_and_closes_both_handles(self):
        events = []
        class Stream:
            def __init__(self, label): self.label = label
            def read(self, size): return b""
            def close(self): events.append("close-"+self.label)
        capture, ready = Stream("capture"), Stream("ready")
        ports = iter((capture, ready))
        def opened(*args, **kwargs):
            stream = next(ports)
            events.append("open-"+stream.label)
            return stream
        def hello(stream, *args):
            self.assertIs(stream, ready)
            self.assertEqual(events, ["open-capture", "deploy", "close-capture", "open-ready"])
            return studio.mnist_serial.IDENTITY
        def deploy(*args):
            events.append("deploy")
            return {"deployment_marker": True}
        host = SimpleNamespace(deploy=deploy)
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(studio.board_init.flashlink, "initialization_lock", contextlib.nullcontext), \
                patch.object(studio.mnist_serial, "_exchange", side_effect=hello), \
                patch.object(studio.mnist_serial, "set_display", return_value=0):
            report = studio.board_init.initialize(ROOT, "COM99", Path(temporary)/"init",
                                                  host_factory=lambda: host, open_serial=opened)
            self.assertTrue(report["initialization_verified"])
            self.assertTrue(report["uart_reopened_after_jtag"])
            self.assertEqual(events[-1], "close-ready")

    def test_runtime_cli_rejects_hardware_combination(self):
        for extra in (["--port", "COM5"], ["--initialize"], ["--board-test"], ["--selftest"], ["--image", "unused.png"]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                studio.main(["--check-jtag-runtime", *extra])
            self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    def forbidden(*args, **kwargs): raise AssertionError("Real hardware/process access forbidden")
    studio.cv2.VideoCapture = forbidden
    studio.serial_link.open_port = forbidden
    flashlink.subprocess.Popen = forbidden
    unittest.main(verbosity=2)
