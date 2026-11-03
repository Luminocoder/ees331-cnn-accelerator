"""serial_link.py —— 串口访问层（优先 pyserial，缺失时用 ctypes 直连 Win32）

背景：这台机器上的 C:\\Python314 装有 cv2/numpy 但没有 pyserial，而界面程序
必须能在"什么都没装"的实验室机器上跑起来。所以：

  1. 装了 pyserial  -> 用 pyserial（成熟、跨平台）；
  2. 没装           -> 用 kernel32 的 CreateFile/SetCommState/ReadFile/WriteFile，
                        纯标准库，零安装。

两条路径暴露同一个最小接口，正好满足 mnist_serial.py 的需要：
    stream.read(n) -> bytes      （读不到立刻返回 b""，不阻塞）
    stream.write(b) -> int       （返回实际写出的字节数）
    stream.close()
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time

DEFAULT_BAUD = 115200

try:                                    # 优先 pyserial
    import serial as _pyserial
    import serial.tools.list_ports as _list_ports
    HAVE_PYSERIAL = True
except Exception:                       # noqa: BLE001
    _pyserial = None
    _list_ports = None
    HAVE_PYSERIAL = False


def backend_name():
    return "pyserial" if HAVE_PYSERIAL else "ctypes-win32"


def list_ports():
    """返回 [('COM7', '描述'), ...]"""
    if HAVE_PYSERIAL:
        return [(p.device, p.description or "") for p in _list_ports.comports()]
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DEVICEMAP\SERIALCOMM")
        out = []
        i = 0
        while True:
            try:
                name, value, _ = winreg.EnumValue(key, i)
            except OSError:
                break
            out.append((str(value), str(name)))
            i += 1
        winreg.CloseKey(key)
        return sorted(out)
    except Exception:                   # noqa: BLE001
        return []


# --------------------------------------------------------------------------
# ctypes / Win32 实现
# --------------------------------------------------------------------------
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
MAXDWORD = 0xFFFFFFFF

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                             wt.DWORD, wt.DWORD, wt.HANDLE]
_k32.CreateFileW.restype = wt.HANDLE
_k32.CloseHandle.argtypes = [wt.HANDLE]
_k32.CloseHandle.restype = wt.BOOL
_k32.SetCommState.argtypes = [wt.HANDLE, ctypes.c_void_p]
_k32.SetCommState.restype = wt.BOOL
_k32.GetCommState.argtypes = [wt.HANDLE, ctypes.c_void_p]
_k32.GetCommState.restype = wt.BOOL
_k32.SetCommTimeouts.argtypes = [wt.HANDLE, ctypes.c_void_p]
_k32.SetCommTimeouts.restype = wt.BOOL
_k32.SetupComm.argtypes = [wt.HANDLE, wt.DWORD, wt.DWORD]
_k32.SetupComm.restype = wt.BOOL
_k32.PurgeComm.argtypes = [wt.HANDLE, wt.DWORD]
_k32.PurgeComm.restype = wt.BOOL
_k32.ReadFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD,
                          ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
_k32.ReadFile.restype = wt.BOOL
_k32.WriteFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD,
                           ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
_k32.WriteFile.restype = wt.BOOL

PURGE_TXCLEAR = 0x0004
PURGE_RXCLEAR = 0x0008

PARITY = {"N": 0, "O": 1, "E": 2, "M": 3, "S": 4}
STOPBITS = {1: 0, 2: 2}      # ONESTOPBIT=0, TWOSTOPBITS=2


class Win32Serial(object):
    """最小串口实现：115200 8N1、无流控、DTR/RTS 保持无效（与 v3.1 固件一致）。"""

    def __init__(self, port, baudrate=DEFAULT_BAUD, timeout=0):
        self.port = port
        self.baudrate = int(baudrate)
        self._closed = False
        path = port if port.startswith("\\\\.\\") else "\\\\.\\" + port.replace("/", "\\")
        handle = _k32.CreateFileW(path, GENERIC_READ | GENERIC_WRITE,
                                  0, None, OPEN_EXISTING, 0, None)
        if handle == INVALID_HANDLE_VALUE or handle is None:
            err = ctypes.get_last_error()
            raise OSError("无法打开 %s (Win32 错误 %d)" % (port, err))
        self._h = handle
        try:
            _k32.SetupComm(self._h, 65536, 65536)
            self._apply_dcb()
            self._apply_timeouts()
            _k32.PurgeComm(self._h, PURGE_RXCLEAR | PURGE_TXCLEAR)
        except Exception:
            self.close()
            raise

    # --- 配置 -------------------------------------------------------------
    def _apply_dcb(self):
        buf = ctypes.create_string_buffer(28)          # DCB 结构，x64 下 28 字节
        if not _k32.GetCommState(self._h, buf):
            raise OSError("GetCommState 失败 (%d)" % ctypes.get_last_error())
        # DCBlength / BaudRate / fFlags（只留 fBinary=1，其余流控与 DTR/RTS 全关）
        ctypes.memmove(ctypes.addressof(buf), ctypes.byref(ctypes.c_uint32(28)), 4)
        ctypes.memmove(ctypes.addressof(buf) + 4,
                       ctypes.byref(ctypes.c_uint32(self.baudrate)), 4)
        ctypes.memmove(ctypes.addressof(buf) + 8, ctypes.byref(ctypes.c_uint32(1)), 4)
        # wReserved=XonLim=XoffLim=0；ByteSize=8；Parity=NONE；StopBits=ONESTOPBIT
        ctypes.memmove(ctypes.addressof(buf) + 18, b"\x08\x00\x00", 3)
        if not _k32.SetCommState(self._h, buf):
            raise OSError("SetCommState 失败 (%d)" % ctypes.get_last_error())

    def _apply_timeouts(self):
        # ReadIntervalTimeout=MAXDWORD 且乘法因子/常数为 0 => 读永不阻塞，立即返回
        timeouts = ctypes.create_string_buffer(20)
        ctypes.memmove(ctypes.addressof(timeouts),
                       ctypes.byref(ctypes.c_uint32(MAXDWORD)), 4)
        ctypes.memmove(ctypes.addressof(timeouts) + 16,
                       ctypes.byref(ctypes.c_uint32(1000)), 4)   # 写超时 1 s
        if not _k32.SetCommTimeouts(self._h, timeouts):
            raise OSError("SetCommTimeouts 失败 (%d)" % ctypes.get_last_error())

    # --- 读写 -------------------------------------------------------------
    def read(self, size):
        if self._closed or size <= 0:
            return b""
        buf = ctypes.create_string_buffer(int(size))
        got = wt.DWORD(0)
        ok = _k32.ReadFile(self._h, buf, int(size), ctypes.byref(got), None)
        if not ok:
            err = ctypes.get_last_error()
            if err == 0:
                return b""
            raise OSError("ReadFile 失败 (%d)" % err)
        return buf.raw[:got.value]

    def write(self, data):
        data = bytes(data)
        if self._closed or not data:
            return 0
        buf = ctypes.create_string_buffer(data, len(data))
        sent = wt.DWORD(0)
        ok = _k32.WriteFile(self._h, buf, len(data), ctypes.byref(sent), None)
        if not ok:
            raise OSError("WriteFile 失败 (%d)" % ctypes.get_last_error())
        return int(sent.value)

    def flush(self):
        return None

    @property
    def in_waiting(self):
        return 0

    def reset_buffers(self):
        if not self._closed:
            _k32.PurgeComm(self._h, PURGE_RXCLEAR | PURGE_TXCLEAR)

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            _k32.CloseHandle(self._h)
        except Exception:               # noqa: BLE001
            pass


def open_port(port, baudrate=DEFAULT_BAUD, timeout=0):
    """统一入口：返回 read/write 兼容的串口对象。"""
    if not port:
        raise ValueError("未指定串口")
    if HAVE_PYSERIAL:
        return _pyserial.Serial(port=port, baudrate=baudrate, bytesize=8,
                                parity="N", stopbits=1, timeout=timeout,
                                xonxoff=False, rtscts=False, dsrdtr=False)
    return Win32Serial(port, baudrate=baudrate, timeout=timeout)


def drain(stream, seconds=0.05):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not stream.read(4096):
            break
