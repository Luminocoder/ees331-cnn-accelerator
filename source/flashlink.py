"""JTAG host ported from old oneclick.cs; guarded bringup + v4.4 bit/ELF.

No Flash/SD writes. Only subprocesses owned by this run are stopped.
"""
from contextlib import contextmanager
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid

FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_DLL_SEARCH_LOCK = threading.RLock()


def find_vivado(explicit=None):
    choices = [explicit, os.environ.get("EES331_VIVADO"), os.environ.get("XILINX_VIVADO"),
               "D:/2025.1/Vivado", "C:/Xilinx/Vivado/2025.1", "C:/AMDDesignTools/2025.1/Vivado"]
    for value in choices:
        if not value:
            continue
        root = Path(value).resolve()
        if all((root/"bin/unwrapped/win64.o"/name).is_file() for name in ("xsdb.exe", "hw_server.exe")):
            if not str(root).isascii():
                raise ValueError("Vivado安装目录须为ASCII路径")
            return root
        if explicit:
            break
    raise FileNotFoundError("找不到Vivado XSDB/hw_server；可设置EES331_VIVADO为Vivado目录")


def environment(vivado):
    root = vivado.as_posix()
    env = os.environ.copy()
    env.update(MYVIVADO=root, TCL_LIBRARY=root+"/tps/tcl/tcl8.6",
               TCLLIBPATH="{"+root+"/scripts/xsdb} {"+root+"/tps/tcl}",
               HW_SERVER_FTDIMGR_DISABLE="1", HW_SERVER_DJTG_DISABLE="1", EES331_PREFLIGHT_OK="1")
    # PyInstaller's Tcl/Tk runtime settings belong to our GUI, not XSDB.
    env.pop("TK_LIBRARY", None)
    env.pop("TCL_ROOT", None)
    bundle = getattr(sys, "_MEIPASS", None)
    path = env.get("PATH", "").split(os.pathsep)
    if bundle:
        bundle = os.path.normcase(os.path.abspath(bundle))
        def outside_bundle(part):
            candidate = os.path.normcase(os.path.abspath(part.strip('"')))
            try:
                return os.path.commonpath((candidate, bundle)) != bundle
            except ValueError:  # Another drive.
                return True
        path = [part for part in path if part and outside_bundle(part)]
    env["PATH"] = os.pathsep.join([str(vivado/"lib/win64.o"), *path])
    return env


@contextmanager
def vendor_dll_search():
    """Clear the inherited PyInstaller DLL directory only during CreateProcess.

    PATH alone cannot override SetDllDirectoryW, which PyInstaller sets to
    _MEIPASS. Without this, XSDB loads our Tcl 8.6.15 instead of Vivado's 8.6.13.
    Restore the actual previous directory even when process creation fails.
    """
    if sys.platform != "win32":
        yield {"dll_search_isolated": False}
        return
    with _DLL_SEARCH_LOCK:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetDllDirectoryW.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p]
        kernel.GetDllDirectoryW.restype = ctypes.c_uint32
        kernel.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
        kernel.SetDllDirectoryW.restype = ctypes.c_int
        buffer = ctypes.create_unicode_buffer(32768)
        length = kernel.GetDllDirectoryW(len(buffer), buffer)
        if length >= len(buffer):
            raise OSError("DLL search directory exceeds Windows path limit")
        previous = buffer.value
        if not kernel.SetDllDirectoryW(None):
            raise ctypes.WinError(ctypes.get_last_error())
        state = {"dll_search_isolated": True, "previous_dll_directory": previous,
                 "dll_directory_restored": False}
        try:
            yield state
        finally:
            if not kernel.SetDllDirectoryW(previous or None):
                raise ctypes.WinError(ctypes.get_last_error())
            state["dll_directory_restored"] = True


def vendor_popen(*args, **kwargs):
    with vendor_dll_search():
        return subprocess.Popen(*args, **kwargs)


@contextmanager
def initialization_lock():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.ReleaseMutex.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateMutexW(None, False, r"Local\EES331-OneClick-1234-tulA")
    if not handle:
        raise OSError("Cannot create initialization mutex")
    owned = False
    try:
        owned = kernel.WaitForSingleObject(handle, 0) in (0, 0x80)
        if not owned:
            raise RuntimeError("另一初始化程序正在运行，请等待结束")
        yield
    finally:
        if owned:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


def server_available():
    try:
        with socket.create_connection(("127.0.0.1", 3121), timeout=0.3):
            return True
    except OSError:
        return False


def stop_owned(process):
    if process is not None and process.poll() is None:
        subprocess.run([str(Path(os.environ.get("SystemRoot", "C:/Windows"))/"System32/taskkill.exe"),
                        "/PID", str(process.pid), "/T", "/F"], capture_output=True,
                       timeout=10, creationflags=FLAGS)
        process.wait(timeout=5)


class InitializationHost:
    def __init__(self, vivado=None, work_root=None):
        self.vivado = find_vivado(vivado)
        self.work_root = Path(work_root or os.environ.get("EES331_WORK_ROOT") or
                              (self.vivado.anchor+"aic/ees331-v5-runs")).resolve()
        if not str(self.work_root).isascii() or " " in str(self.work_root):
            raise ValueError("EES331_WORK_ROOT须为不含空格的ASCII目录")

    def check_runtime(self, log_directory):
        """Run XSDB/Tcl without connect/targets/reset/UART or a hardware server."""
        log_directory = Path(log_directory)
        workspace = self.work_root/("runtime-"+uuid.uuid4().hex)
        workspace.mkdir(parents=True, exist_ok=False)
        script = workspace/"runtime-check.tcl"
        script.write_text('if {[llength [info commands connect]] != 1} {error "XSDB connect command missing"}\n'
                          'puts "V5_XSDB_RUNTIME_PASS Tcl=[info patchlevel]"\nexit 0\n', encoding="ascii")
        log_directory.mkdir(parents=True, exist_ok=True)
        report = {"status": "CHECKING", "hardware_accessed": False,
                  "vivado": str(self.vivado), "workspace": str(workspace),
                  "frozen_parent": bool(getattr(sys, "frozen", False))}
        process = None
        try:
            with (log_directory/"xsdb-runtime.log").open("wb") as output:
                with vendor_dll_search() as isolation:
                    process = subprocess.Popen([str(self.vivado/"bin/unwrapped/win64.o/xsdb.exe"), "-n", str(script)],
                                               cwd=workspace, env=environment(self.vivado), creationflags=FLAGS,
                                               stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
                report.update(isolation)
                process.wait(timeout=30)
            text = (log_directory/"xsdb-runtime.log").read_text(encoding="utf-8", errors="replace")
            if process.returncode != 0 or "V5_XSDB_RUNTIME_PASS Tcl=" not in text:
                raise RuntimeError("XSDB运行库启动失败；请检查xsdb-runtime.log（尚未执行JTAG）")
            report.update(status="XSDB_RUNTIME_PASS", exit_code=process.returncode,
                          marker=next(line.strip() for line in text.splitlines() if "V5_XSDB_RUNTIME_PASS Tcl=" in line))
            return report
        except Exception as exc:
            report.update(status="XSDB_RUNTIME_FAIL", error=str(exc))
            raise
        finally:
            stop_owned(process)
            (log_directory/"xsdb-runtime.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")

    def deploy(self, resources, manifest, log_directory):
        runtime = self.check_runtime(log_directory)
        workspace = self.work_root/("run-"+uuid.uuid4().hex)
        workspace.mkdir(parents=True, exist_ok=False)
        mapping = {"flash_payload/system.bit": "bitstream/system.bit",
                   "flash_payload/app.elf": "arm_firmware/app.elf",
                   "flash_payload/ps7_init.tcl": "prepared/ps7_init.tcl",
                   "initialization/bringup.tcl": "board/bringup.tcl",
                   "initialization/initialize.tcl": "initialize.tcl"}
        for source, target in mapping.items():
            output = workspace/target
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(resources/source, output)
            if hashlib.sha256(output.read_bytes()).hexdigest() != manifest["files"][source]["sha256"]:
                raise RuntimeError("Staged deployment hash mismatch: "+target)
        (workspace/"prepared/baseline.json").write_text(json.dumps(manifest), encoding="ascii")
        (log_directory/"workspace.txt").write_text(str(workspace)+"\n", encoding="utf-8")
        env = environment(self.vivado)
        binary = self.vivado/"bin/unwrapped/win64.o"
        owned_server = None
        process = None
        with (log_directory/"hw-server.log").open("wb") as server_log:
            try:
                if not server_available():
                    owned_server = vendor_popen([str(binary/"hw_server.exe"), "-sTCP::3121", "-I60",
                                                     "-L"+str(workspace/"hw-server-internal.log")],
                                                    cwd=workspace, env=env, creationflags=FLAGS,
                                                    stdin=subprocess.DEVNULL, stdout=server_log, stderr=subprocess.STDOUT)
                    deadline = time.monotonic()+15
                    while not server_available():
                        if owned_server.poll() is not None or time.monotonic() >= deadline:
                            raise RuntimeError("hw_server未就绪，请查看hw-server.log")
                        time.sleep(0.1)
                with (log_directory/"deployment.log").open("wb") as output:
                    process = vendor_popen([str(binary/"xsdb.exe"), "-n", str(workspace/"initialize.tcl")],
                                               cwd=workspace, env=env, creationflags=FLAGS,
                                               stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
                    process.wait(timeout=300)
                text = (log_directory/"deployment.log").read_text(encoding="utf-8", errors="replace")
                if process.returncode != 0 or "V5_DEPLOYMENT_PASS" not in text:
                    raise RuntimeError("JTAG初始化失败，检查deployment.log；未标记成功")
                return {"workspace": str(workspace), "vivado": str(self.vivado), "runtime": runtime,
                        "owned_hw_server": owned_server is not None, "deployment_marker": True}
            finally:
                stop_owned(process)
                stop_owned(owned_server)
