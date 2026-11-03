#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""EES-331 v5.1: original input/CNN core with GUI and DISPLAY/two-digit interfaces.

Default: capture/open image -> automatic localization -> uint8 28x28 -> inference.
An optional dragged ROI restricts search; it is never required. Hardware actions
are explicit. --offline forbids hardware connection/initialization from the UI.
"""

import argparse
import os
import queue
from pathlib import Path
import secrets
import sys
import threading
import time
import traceback
import json
import uuid

import cv2
import numpy as np

if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    HERE = sys._MEIPASS            # PyInstaller：数据都在这里
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import digit_finder as DF                                     # noqa: E402
import mnist_camera_input as MCI                               # noqa: E402
import mnist_serial                                            # noqa: E402
import offline_ref                                             # noqa: E402
import serial_link                                             # noqa: E402
import vision_pipeline as VP
import sample_log
import board_init
import inference_pipeline as IP
from preview_detection import PreviewDetector
from batch_capture import BatchSession, PagePlayer
import display_preview
from display_transport import AcknowledgedPagePlayer

APP_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
RESOURCE_ROOT = offline_ref.BASE

# --------------------------------------------------------------------------
# 配色：照最初那份 C# 界面（深蓝底 + 青色强调）
# --------------------------------------------------------------------------
BG = "#1b212c"
PANEL = "#232a37"
PANEL2 = "#1e2531"
EDGE = "#323c4d"
TEXT = "#dde5f2"
MUTED = "#8795a8"
CYAN = "#35c4e8"
GREEN = "#28c76f"
ORANGE = "#ff9f43"
RED = "#ea5455"
YELLOW = "#f6c343"
SECONDARY = "#3d4a5e"

CAM_WARMUP = 20
CAM_FLAT_STD = 8.0
CAM_ORDER = 4

VERSION = "v5.6 卷积流水线优化候选版（待上板验收）2026-11-01"


# --------------------------------------------------------------------------
# 图像 -> tk.PhotoImage（不用 PIL：Tk 8.6 能直接吃 PNG 字节）
# --------------------------------------------------------------------------
def to_photo(image, max_w, max_h, zoom=1.0):
    import tkinter as tk
    h, w = image.shape[:2]
    scale = min(max_w / float(w), max_h / float(h)) * zoom
    scale = max(0.01, scale)
    out = cv2.resize(image, (max(1, int(w * scale)), max(1, int(h * scale))),
                     interpolation=cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR)
    ok, buf = cv2.imencode(".png", out)
    if not ok:
        raise RuntimeError("PNG 编码失败")
    return tk.PhotoImage(data=buf.tobytes()), scale


def softmax_conf(logits, scale=1000.0):
    """把 10 路 INT32 logit 变成 0~1 的可读置信度。

    不能用 |logit| / sum|logit|：负 logit 会把分母撑大，明明认对了也只有
    22.7%（实测）。这里用带温度的真实 softmax —— logit 量级约 2e4，
    除以 1000 再 softmax，既不会溢出也保留了"领先多少"的信息。
    """
    best = max(logits)
    exps = [np.exp((v - best) / scale) for v in logits]
    return float(exps[list(logits).index(best)] / sum(exps))


_PREP_ZH = {
    "No usable digit contrast": "框里明暗差太小，把框框紧一点、让数字写黑一点",
    "select a closer ROI": "框里对比度不够，请把框框紧",
    "Empty/overfilled foreground": "框里内容太少或太满，请把框框紧",
    "No digit-sized component found": "框里没找到够大的笔画，把框框到数字上",
    "Multiple large components": "框里不止一个数字，请只框一个",
    "crop a single connected digit": "框里有好几个笔画，请只框一个数字",
    "Digit touches ROI boundary": "数字贴到框边了，把框稍微放大一点、留点边",
    "leave background around it": "数字贴边，框留点背景",
}


def _translate(msg):
    for key, zh in _PREP_ZH.items():
        if key in msg:
            return zh
    return msg


def _dump_debug(frame, roi, exc):
    """识别失败时把框选区域存到 exe 旁的 _debug/，方便下次定位真问题。

    只写失败现场，识别成功不写。存的是 PNG + 一行错误说明，用户可直接发回来。
    """
    try:
        if getattr(sys, "frozen", False):
            base = Path(sys.executable).resolve().parent
        else:
            base = Path(HERE)
        d = base / "_debug"
        d.mkdir(exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        x, y, w, h = roi
        crop = frame[max(0, y):y + h, max(0, x):x + w]
        png = d / ("fail_%s.png" % stamp)
        cv2.imwrite(str(png), crop)
        with (d / ("fail_%s.txt" % stamp)).open("w", encoding="utf-8") as f:
            f.write("error=%s\nroi=%r\nframe_shape=%r\n" % (exc, roi, frame.shape))
        return str(png)
    except Exception:                                          # noqa: BLE001
        return None


def _robust_prepare(frame, roi):
    """鲁棒版手动框选预处理 —— 专治「框住了还是识别不了」。

    比 v3.2 官方 prepare_digit 宽容的地方（官方版是为"干净紧框"设计的，实拍会误杀）：
      1. 极性不用"边界中值"判（框紧时边界就是数字本身，中值会判反）。
         改用 Otsu 把像素分亮/暗两堆，面积大的那堆是背景（纸），据此判黑字/白字。
      2. 不再因为"框里不止一块墨迹"就拒绝（手影/纸边/别的笔划都会被当成第二块），
         总是取面积最大的连通域当数字。
      3. 数字贴框边也不报错，照常把数字本身裁出来。

    输出仍是官方同款 28×28 白墨黑底 uint8 张量，喂给 CNN 的结果 bit-exact 不变。
    """
    x, y, w, h = roi
    if frame.ndim == 3 and frame.shape[2] == 3:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    elif frame.ndim == 2:
        gray = frame
    else:
        raise ValueError("只支持灰度或 BGR 图像")

    crop = gray[y:y + h, x:x + w].copy()
    if int(crop.max()) - int(crop.min()) < 8:
        raise ValueError("框里明暗差太小，把框框紧一点、让数字写黑一点")

    # 极性：Otsu 分亮/暗两堆，面积大的一堆是背景
    t, _ = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    dark = crop[crop <= t]
    light = crop[crop > t]
    if dark.size == 0 or light.size == 0:
        polarity = "dark"
    elif light.size >= dark.size:
        polarity = "dark"       # 亮的是背景 → 黑字白纸
    else:
        polarity = "light"      # 暗的是背景 → 白字黑底

    ink = 255 - crop if polarity == "dark" else crop
    thr, mask = cv2.threshold(ink, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    fraction = float(cv2.countNonZero(mask)) / mask.size
    if fraction < 0.0005:
        raise ValueError("框里没找到笔画，把框框到数字上")
    if fraction > 0.85:
        raise ValueError("框里几乎全是墨迹，把框放大一点、留点背景")

    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if count <= 1:
        raise ValueError("框里没找到笔画，把框框到数字上")
    comps = sorted(range(1, count),
                   key=lambda i: int(stats[i, cv2.CC_STAT_AREA]), reverse=True)
    chosen = comps[0]
    area = int(stats[chosen, cv2.CC_STAT_AREA])
    if area < max(8, mask.size * 0.0001):
        raise ValueError("框里没找到够大的笔画，把框框到数字上")

    cx, cy, cw, ch, _ = map(int, stats[chosen])
    foreground = np.where(labels == chosen, ink, 0).astype(np.uint8)
    digit = foreground[cy:cy + ch, cx:cx + cw]

    scale = 20.0 / max(cw, ch)
    sw, sh = max(1, round(cw * scale)), max(1, round(ch * scale))
    interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    resized = cv2.resize(digit, (sw, sh), interpolation=interp)
    canvas = np.zeros((28, 28), np.uint8)
    left, top = (28 - sw) // 2, (28 - sh) // 2
    canvas[top:top + sh, left:left + sw] = resized

    m = cv2.moments(canvas)
    if m["m00"] <= 0:
        raise ValueError("数字在缩放时丢了，把框框大一点")
    dx = round(13.5 - m["m10"] / m["m00"])
    dy = round(13.5 - m["m01"] / m["m00"])
    ys, xs = np.nonzero(canvas)
    dx = max(-int(xs.min()), min(dx, 27 - int(xs.max())))
    dy = max(-int(ys.min()), min(dy, 27 - int(ys.max())))
    canvas = cv2.warpAffine(canvas, np.float32([[1, 0, dx], [0, 1, dy]]),
                            (28, 28), flags=cv2.INTER_NEAREST, borderValue=0)

    meta = {"polarity": polarity, "bbox": roi, "foreground": foreground,
            "foreground_fraction": fraction, "otsu_threshold": thr,
            "digit_bbox_in_roi": [cx, cy, cw, ch]}
    return canvas, foreground, meta


def manual_prepare(frame, roi, two_half=None):
    """手动框选：roi 就是数字的位置，只做预处理，不做自动定位。"""
    if roi is None:
        raise ValueError("请先在画面上拖一个框，框住要识别的数字")
    x, y, w, h = (int(v) for v in roi)
    if w < 8 or h < 8:
        raise ValueError("框太小了，把数字框大一点")
    if two_half == "left":
        roi = (x, y, max(1, w // 2), h)
    elif two_half == "right":
        roi = (x + w // 2, y, max(1, w - w // 2), h)
    x, y, w, h = roi
    if x < 0 or y < 0 or x + w > frame.shape[1] or y + h > frame.shape[0]:
        raise ValueError("框超出画面了，请重新拖一个框")
    canvas, _foreground, meta = _robust_prepare(frame, (x, y, w, h))
    return canvas, meta


def gray_to_photo(pixels, size, nearest=True):
    import tkinter as tk
    interp = cv2.INTER_NEAREST if nearest else cv2.INTER_LINEAR
    rgb = cv2.cvtColor(cv2.resize(pixels, (size, size), interpolation=interp),
                       cv2.COLOR_GRAY2BGR)
    ok, buf = cv2.imencode(".png", rgb)
    if not ok:
        raise RuntimeError("PNG 编码失败")
    return tk.PhotoImage(data=buf.tobytes())


# --------------------------------------------------------------------------
# 相机
# --------------------------------------------------------------------------
class Camera(object):
    def __init__(self):
        self.cap = None
        self.index = None
        self.std = None
        self.last = None
        self.frame_id = 0
        self.captured_at = None
        self.read_ok = False
        self.eye = "auto"
        self.raw_last = None
        self.actual_shape = None
        self.last_error = None

    def _select_frame(self, frame):
        self.raw_last = frame.copy()
        self.actual_shape = list(frame.shape)
        eye = self.eye
        if eye == "auto":
            eye = "first" if frame.shape[:2] == (800, 2560) else "full"
        if eye == "full":
            return frame
        if frame.shape[:2] != (800, 2560):
            raise ValueError("选半仅适用于2560×800拼接帧，当前尺寸为 %s" % (frame.shape,))
        return MCI.select_eye(frame, eye)

    def open(self, preferred=0):
        """在 preferred 和 #0~#3 里挑一个真能出画面的。返回 (index, std, frame)。"""
        order = [preferred] + [i for i in range(CAM_ORDER) if i != preferred]
        fallback = None
        for idx in order:
            cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
            if not cap.isOpened():
                cap.release()
                continue
            try:
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 2560)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 800)
            except Exception:                                  # noqa: BLE001
                pass
            frame = None
            for _ in range(CAM_WARMUP):        # 取最后一帧，不是第一帧
                ok, got = cap.read()
                if ok and got is not None:
                    frame = got
                time.sleep(0.03)
            if frame is None:
                cap.release()
                continue
            try:
                selected = self._select_frame(frame)
            except (ValueError, cv2.error) as exc:
                self.last_error = str(exc)
                cap.release()
                continue
            std = float(frame.std())
            if std >= CAM_FLAT_STD:
                if fallback is not None:
                    fallback[0].release()
                self.cap, self.index, self.std, self.last = cap, idx, std, selected
                self.captured_at, self.read_ok = time.monotonic(), True
                self.last_error = None
                self.frame_id += 1
                return idx, std, selected
            if fallback is None:
                fallback = (cap, idx, std, frame)
            else:
                cap.release()
        if fallback is not None:
            self.cap, self.index, self.std, self.last = fallback
            self.last = self._select_frame(self.last)
            self.captured_at, self.read_ok = time.monotonic(), True
            self.last_error = None
            self.frame_id += 1
            return fallback[1], fallback[2], self.last
        return None, None, None

    def read(self):
        if self.cap is None:
            return None
        try:
            ok, frame = self.cap.read()
            if ok and frame is not None:
                frame = self._select_frame(frame)
                self.last = frame
                self.frame_id += 1
                self.captured_at, self.read_ok = time.monotonic(), True
                self.last_error = None
                return frame
            self.last_error = "相机未返回新画面"
        except (ValueError, cv2.error, OSError, RuntimeError) as exc:
            self.last_error = str(exc)
        self.read_ok = False
        self.last = None
        self.raw_last = None
        return None

    def close(self):
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:                                  # noqa: BLE001
                pass
        self.cap = None
        self.last = None
        self.read_ok = False


# --------------------------------------------------------------------------
# 板子 / 离线
# --------------------------------------------------------------------------
def serial_operation(method):
    def locked(self, *args, **kwargs):
        with self.serial_lock:
            return method(self, *args, **kwargs)
    return locked


class Board(object):
    def __init__(self):
        self.stream = None
        self.port = None
        self.mode = "offline"
        self.last_latency = None
        self.serial_lock = threading.RLock()
        self.connection_id = 0
        self.page_capable = None
        self.display_deferred = False

    @serial_operation
    def connect(self, port):
        self.close()
        stream = serial_link.open_port(port, timeout=0)
        serial_link.drain(stream, 0.10)
        self.stream, self.port, self.mode = stream, port, "board"

    @serial_operation
    def hello(self):
        """发一次 HELLO，确认对面真的是那块板。"""
        if self.mode != "board":
            raise RuntimeError("还没连串口")
        seq = secrets.randbits(32)
        req = mnist_serial.encode_packet(mnist_serial.HELLO, seq, mnist_serial.IDENTITY)
        reply = mnist_serial._exchange(self.stream, req,                       # noqa: SLF001
                                       mnist_serial.HELLO_ACK, seq, 2.0)
        if reply != mnist_serial.IDENTITY:
            raise mnist_serial.ProtocolError("对面的应答不是 MNIST-U8-28x28")

    @serial_operation
    def infer(self, pixels):
        """pixels: 28x28 uint8（0..255）。返回 (digit, logits, where, ms)。"""
        t0 = time.monotonic()
        if self.mode == "board":
            res = mnist_serial.send_image(self.stream, pixels.tobytes(order="C"),
                                          secrets.randbits(32), 3.0)
            ms = (time.monotonic() - t0) * 1000.0
            self.last_latency = ms
            return res["digit"], res["logits"], "板子", ms
        digit, logits = offline_ref.infer(pixels)
        ms = (time.monotonic() - t0) * 1000.0
        return digit, logits, "PC金标准", ms

    @serial_operation
    def set_display(self, value):
        if self.mode != "board":
            return None
        self.set_capture_display(False)
        shown = mnist_serial.set_display(self.stream, int(value), secrets.randbits(32), 2.0)
        if shown != int(value):
            raise mnist_serial.ProtocolError("DISPLAY ACK mismatch: requested %s, got %s" % (value, shown))
        return shown

    @serial_operation
    def display_capabilities(self):
        if self.mode != 'board':
            raise RuntimeError('板卡串口未连接')
        if self.page_capable is None:
            try:
                mnist_serial.get_display_caps(self.stream, secrets.randbits(32))
                self.page_capable = True
            except mnist_serial.ProtocolError as exc:
                if not str(exc).startswith('Board error:'):
                    raise
                self.page_capable = False
        return self.page_capable

    @serial_operation
    def set_capture_display(self, deferred):
        if self.mode != 'board':
            return False
        if deferred and not self.display_capabilities():
            return False
        if self.display_deferred != bool(deferred):
            mnist_serial.set_display_mode(self.stream, deferred, secrets.randbits(32))
            self.display_deferred = bool(deferred)
        return True

    @serial_operation
    def set_display_page(self, text, connection_id, still_current=None):
        if still_current is not None and not still_current():
            raise RuntimeError('分页任务已取消')
        if self.mode != 'board' or self.connection_id != connection_id:
            raise RuntimeError('板卡连接已变化，已取消本次分页')
        if not self.display_capabilities():
            raise RuntimeError('当前固件/位流不支持八位分页，请使用v5.3重新初始化板卡')
        self.set_capture_display(True)
        return mnist_serial.set_display_page(self.stream, text, secrets.randbits(32))

    @serial_operation
    def close(self):
        if self.stream is not None:
            try:
                if self.display_deferred:
                    mnist_serial.set_display_mode(self.stream, False, secrets.randbits(32), 0.5)
            except Exception:
                pass
            try:
                self.stream.close()
            except Exception:                                  # noqa: BLE001
                pass
        self.stream = None
        self.mode = "offline"
        self.connection_id += 1
        self.page_capable = None
        self.display_deferred = False


# --------------------------------------------------------------------------
# 主界面
# --------------------------------------------------------------------------
class Studio(object):
    def __init__(self, root, args):
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.root = root
        self.args = args
        self.cam = Camera()
        self.cam.eye = args.camera_eye
        self.board = Board()
        self.preview_job = None
        self.preview_detector = PreviewDetector()
        self.busy = False
        self.frame = None           # 当前 BGR 画面
        self.roi = None             # (x,y,w,h) 手动框选的识别框，None = 还没框
        self.drag_from = None
        self.last_canvas = None
        self.last_meta = None
        self.photo_cache = []
        self.input_revision = 0
        self.last_run_path = None
        self.detected_boxes = []
        self.source_info = {"kind": "none"}
        self.capture_raw = None
        self.batch = BatchSession(APP_ROOT/'logs/batches')
        self.page_player = PagePlayer(root)
        self.board_page_player = AcknowledgedPagePlayer(root)
        self.auto_job = None
        self.batch_files = []
        self.batch_finish_requested = False

        root.title("EES-331 边缘视觉推理工作台 · %s" % VERSION)
        root.configure(bg=BG)
        root.geometry("1380x860")
        root.minsize(1180, 720)

        st = ttk.Style()
        try:
            st.theme_use("clam")
        except Exception:                                      # noqa: BLE001
            pass
        st.configure("TCombobox", fieldbackground=PANEL2, background=PANEL,
                     foreground=TEXT, arrowcolor=TEXT)
        st.map("TCombobox", fieldbackground=[("readonly", PANEL2)],
               foreground=[("readonly", TEXT)])

        self._build_header()
        self._build_batch_controls()
        body = tk.Frame(root, bg=BG)
        body.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        self._build_left(body)
        self._build_middle(body)
        self._build_right(body)
        self._build_log()

        self._queue = queue.Queue()
        self.root.after(30, self._poll_queue)

        self._refresh_ports()
        if args.port:
            self.var_port.set(args.port)
        self.set_status("离线就绪；连接板卡请点『连接』或『初始化并连接』", MUTED)

    # ---------------- 布局 ----------------
    def _panel(self, parent, title, sub=""):
        outer = self.tk.Frame(parent, bg=PANEL, highlightbackground=EDGE,
                              highlightthickness=1)
        head = self.tk.Frame(outer, bg=PANEL)
        head.pack(fill="x", padx=10, pady=(8, 4))
        self.tk.Label(head, text=title, bg=PANEL, fg=CYAN,
                      font=("Microsoft YaHei UI", 11, "bold")).pack(side="left")
        if sub:
            self.tk.Label(head, text=sub, bg=PANEL, fg=MUTED,
                          font=("Microsoft YaHei UI", 8)).pack(side="left", padx=8)
        return outer

    def _build_header(self):
        tk = self.tk
        head = tk.Frame(self.root, bg=PANEL2, height=64)
        head.pack(fill="x", pady=(0, 8))
        left = tk.Frame(head, bg=PANEL2)
        left.pack(side="left", padx=14, pady=10)
        tk.Label(left, text="EES-331  端侧视觉感知 — 异构计算协同推理系统",
                 bg=PANEL2, fg=TEXT, font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w")
        tk.Label(left, text="Zynq-7020 On-Chip INT8 CNN SoC  |  Bit-Exact Verification",
                 bg=PANEL2, fg=MUTED, font=("Consolas", 9)).pack(anchor="w")

        right = tk.Frame(head, bg=PANEL2)
        right.pack(side="right", padx=14, pady=10)
        tk.Label(right, text="串口", bg=PANEL2, fg=MUTED,
                 font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(0, 4))
        self.var_port = tk.StringVar()
        self.cmb_port = self.ttk.Combobox(right, textvariable=self.var_port, width=9,
                                     state="readonly")
        self.cmb_port.pack(side="left")
        self.btn_connect = self._btn(right, "连接", self.do_connect, CYAN)
        self.btn_connect.pack(side="left", padx=6)
        self._btn(right, "初始化并连接", self.do_initialize, ORANGE).pack(side="left", padx=(0, 6))
        self._btn(right, "连接检查", self.do_probe, GREEN).pack(side="left", padx=(0, 6))
        self.lbl_link = tk.Label(right, text="未连接", bg=PANEL2, fg=RED,
                                 font=("Microsoft YaHei UI", 9, "bold"))
        self.lbl_link.pack(side="left", padx=6)

    def _btn(self, parent, text, cmd, color=CYAN, width=None):
        """color 是主色；副按钮传 SECONDARY，否则深底深字会看不见。"""
        if color == SECONDARY:
            bg, fg = SECONDARY, TEXT
        else:
            bg, fg = color, "#0d1117"
        b = self.tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg,
                           activebackground=bg, activeforeground=fg,
                           relief="flat", bd=0, cursor="hand2",
                           highlightthickness=0,
                           font=("Microsoft YaHei UI", 9, "bold"), padx=12, pady=5)
        if width:
            b.configure(width=width)
        return b

    def _build_batch_controls(self):
        tk = self.tk
        bar = tk.Frame(self.root, bg=PANEL2)
        bar.pack(fill='x', padx=10, pady=(0, 4))
        for text, action, color in [('开始批次', self.start_batch, GREEN),
                ('结束并显示', self.finish_batch, CYAN), ('重播', self.replay_batch, SECONDARY),
                ('停止翻页', self.stop_batch_pages, SECONDARY), ('清空', self.clear_batch, SECONDARY)]:
            self._btn(bar, text, action, color).pack(side='left', padx=(0, 5))
        self.var_auto_capture = tk.BooleanVar(value=False)
        tk.Checkbutton(bar, text='自动连拍', variable=self.var_auto_capture,
                       command=self._auto_capture_changed, bg=PANEL2, fg=TEXT,
                       selectcolor=PANEL, activebackground=PANEL2).pack(side='left')
        self.var_capture_seconds = tk.StringVar(value='3')
        tk.Spinbox(bar, from_=2, to=60, width=3, textvariable=self.var_capture_seconds,
                   bg=PANEL, fg=TEXT, buttonbackground=PANEL).pack(side='left')
        tk.Label(bar, text='秒/次（拍前换卡）', bg=PANEL2, fg=MUTED,
                 font=('Microsoft YaHei UI', 8)).pack(side='left', padx=(3, 5))
        self.lbl_batch_state = tk.Label(bar, text='单次模式；点“开始批次”启用连续采集',
            bg=PANEL2, fg=MUTED, font=('Microsoft YaHei UI', 8), anchor='e')
        self.lbl_batch_state.pack(side='right', padx=6)
        perf = tk.Frame(self.root, bg=PANEL2)
        perf.pack(fill='x', padx=10, pady=(0,4))
        self._btn(perf, 'ARM / PL 性能对比', self.do_benchmark, SECONDARY).pack(side='left', padx=(0,5))
        self._btn(perf, '停止性能测试', self.stop_benchmark, SECONDARY).pack(side='left')
        tk.Label(perf, text='无需相机 · 固定20张 · 逐分数校验 · 板端计时 · 自动导出',
                 bg=PANEL2, fg=MUTED, font=('Microsoft YaHei UI',8)).pack(side='left',padx=8)

    def stop_benchmark(self):
        if getattr(self,'benchmark_cancel',None) is not None:
            self.benchmark_cancel.set()
            self.set_status('正在停止性能测试并恢复板端配置…', ORANGE)

    def do_benchmark(self):
        if self.busy: return
        if self.args.offline or self.board.mode != 'board':
            self.set_status('请先用本版“初始化并连接”，再运行性能对比；无需相机', YELLOW)
            return
        if self.batch.collecting:
            self.set_status('请先结束当前拍摄批次，再运行性能对比', YELLOW)
            return
        self.stop_batch_pages(); self.stop_preview()
        self.busy=True; self.benchmark_cancel=threading.Event()
        self.set_status('正在进行无相机ARM/PL对比：先校验，再预热和计时…', ORANGE)
        def work():
            import arm_benchmark
            try:
                with self.board.serial_lock:
                    report,path=arm_benchmark.run_on_stream(self.board.stream,RESOURCE_ROOT,APP_ROOT/'logs/benchmark',
                        progress=lambda s:self._queue.put(('benchmark_progress',s)),cancel=self.benchmark_cancel.is_set)
                self._queue.put(('benchmark_done',(report,str(path))))
            except Exception as exc: self._queue.put(('benchmark_done',({'status':'FAILED','error':str(exc)},None)))
        threading.Thread(target=work,daemon=True).start()

    def _build_left(self, parent):
        tk = self.tk
        p = self._panel(parent, "① 相机取景", "每个绿框放一个完整数字，四周留白")
        p.pack(side="left", fill="both", expand=False, padx=(0, 8), pady=6)
        bar = tk.Frame(p, bg=PANEL)
        bar.pack(fill="x", padx=10, pady=(0, 6))
        self.btn_preview = self._btn(bar, "▶ 开始预览", self.toggle_preview, GREEN)
        self.btn_preview.pack(side="left", padx=(0, 5))
        self.btn_capture = self._btn(bar, "拍下并识别", self.grab_and_run, CYAN)
        self.btn_capture.pack(side="left", padx=5)
        self._btn(bar, "打开图片…", self.open_file, SECONDARY).pack(side="left", padx=5)
        bar2 = tk.Frame(p, bg=PANEL)
        bar2.pack(fill="x", padx=10, pady=(0, 6))
        self._btn(bar2, "清除辅助框", self.clear_roi, SECONDARY).pack(side="left", padx=(0, 5))
        self._btn(bar2, "标注真值", self.annotate_last, SECONDARY).pack(side="left", padx=(0, 5))
        tk.Label(bar2, text="模式", bg=PANEL, fg=MUTED,
                 font=("Microsoft YaHei UI", 8)).pack(side="left", padx=(6, 3))
        self.var_mode = tk.StringVar(value="single")
        for text, val in (("一位", "single"), ("两位", "two")):
            tk.Radiobutton(bar2, text=text, value=val, variable=self.var_mode,
                           bg=PANEL, fg=TEXT, selectcolor=PANEL2, activebackground=PANEL,
                           activeforeground=CYAN, font=("Microsoft YaHei UI", 8),
                            bd=0, highlightthickness=0, command=self._input_changed).pack(side="left")

        bar3 = tk.Frame(p, bg=PANEL)
        bar3.pack(fill="x", padx=10, pady=(0, 6))
        self.var_strategy = tk.StringVar(value="单尺度")
        self.var_compare = tk.BooleanVar(value=False)
        self.var_guided = tk.BooleanVar(value=self.args.capture_mode == "guided")
        tk.Checkbutton(bar3, text="固定取景框（演示）", variable=self.var_guided,
                       command=self._input_changed, bg=PANEL, fg=TEXT, selectcolor=PANEL2,
                       activebackground=PANEL, activeforeground=TEXT).pack(side="left", padx=(0, 6))
        tk.Label(bar3, text="原版去阴影 / 笔画归一化 / 单尺度 CNN", bg=PANEL,
                 fg=MUTED, font=("Microsoft YaHei UI", 7)).pack(side="left")

        self.cv_cam = tk.Canvas(p, bg="#12161e", highlightthickness=1,
                                highlightbackground=EDGE, width=460, height=410)
        self.cv_cam.pack(padx=10, pady=(0, 6))
        self.cv_cam.bind("<ButtonPress-1>", self._roi_start)
        self.cv_cam.bind("<B1-Motion>", self._roi_move)
        self.cv_cam.bind("<ButtonRelease-1>", self._roi_end)
        self.lbl_src = tk.Label(p, text="还没画面", bg=PANEL, fg=MUTED,
                                font=("Consolas", 8), anchor="w", justify="left")
        self.lbl_src.pack(fill="x", padx=10, pady=(0, 8))

    def _build_middle(self, parent):
        tk = self.tk
        p = self._panel(parent, "② 预处理链", "每一步都看得见")
        p.pack(side="left", fill="both", expand=False, padx=(0, 8), pady=6)
        row = tk.Frame(p, bg=PANEL)
        row.pack(padx=10, pady=(0, 6))
        self.cv_gray = tk.Canvas(row, bg="#12161e", highlightthickness=1,
                                 highlightbackground=EDGE, width=150, height=150)
        self.cv_gray.pack(side="left", padx=(0, 8))
        self.cv_mask = tk.Canvas(row, bg="#12161e", highlightthickness=1,
                                 highlightbackground=EDGE, width=150, height=150)
        self.cv_mask.pack(side="left")
        tk.Label(p, text="③ 原始灰度裁剪                  ④ 去阴影 / 笔画归一化",
                 bg=PANEL, fg=MUTED, font=("Consolas", 7)).pack(padx=10, anchor="w")

        tk.Label(p, text="⑤ 原版 28×28 输入（单尺度）", bg=PANEL,
                 fg=CYAN, font=("Microsoft YaHei UI", 9, "bold")).pack(padx=10, pady=(8, 4),
                                                                      anchor="w")
        self.cv_tensor = tk.Canvas(p, bg="#12161e", highlightthickness=1,
                                   highlightbackground=EDGE, width=196, height=196)
        self.cv_tensor.pack(padx=10)
        self.lbl_meta = tk.Label(p, text="—", bg=PANEL, fg=MUTED, justify="left",
                                 anchor="w", font=("Consolas", 8))
        self.lbl_meta.pack(fill="x", padx=10, pady=(6, 10))

    def _build_right(self, parent):
        tk = self.tk
        p = self._panel(parent, "⑥ CNN识别结果", "原版单尺度 CNN；10路原始 INT32 分数")
        p.pack(side="left", fill="both", expand=True, pady=6)
        top = tk.Frame(p, bg=PANEL)
        top.pack(fill="x", padx=12, pady=(4, 0))
        self.lbl_digit = tk.Label(top, text="—", bg=PANEL, fg=TEXT,
                                  font=("Consolas", 60, "bold"))
        self.lbl_digit.pack(side="left", padx=(0, 14))
        info = tk.Frame(top, bg=PANEL)
        info.pack(side="left", anchor="n", pady=8)
        self.lbl_conf = tk.Label(info, text="分数间隔 —", bg=PANEL, fg=MUTED,
                                 font=("Microsoft YaHei UI", 11), anchor="w")
        self.lbl_conf.pack(anchor="w")
        self.lbl_where = tk.Label(info, text="—", bg=PANEL, fg=MUTED,
                                  font=("Consolas", 9), anchor="w")
        self.lbl_where.pack(anchor="w", pady=(3, 0))
        self.lbl_sync = tk.Label(info, text="数码管：未连接", bg=PANEL, fg=MUTED,
                                font=("Microsoft YaHei UI", 9), anchor="w", wraplength=320, justify="left")
        self.lbl_sync.pack(anchor="w", pady=(3, 0))
        self.lbl_compare = tk.Label(p, text="识别核心：原版单尺度 CNN", bg=PANEL, fg=MUTED,
                                   font=("Microsoft YaHei UI", 9), anchor="w", wraplength=460, justify="left")
        self.lbl_compare.pack(fill="x", padx=12, pady=4)

        self.lbl_page = tk.Label(p, text='批次显示预览：每页3项，每项两位，间隔一位熄灭',
            bg=PANEL, fg=MUTED, anchor='w', font=('Microsoft YaHei UI', 8))
        self.lbl_page.pack(fill='x', padx=12)
        self.cv_display = tk.Canvas(p, width=288, height=62, bg='#12161e', highlightthickness=0)
        self.cv_display.pack(anchor='w', padx=12, pady=4)
        display_preview.draw(self.cv_display, '        ')
        batch_result = tk.Frame(p, bg=PANEL2)
        batch_result.pack(fill='x', padx=12)
        self.txt_batch = tk.Text(batch_result, height=3, width=30, bg=PANEL2, fg=TEXT, relief='flat',
                                 font=('Consolas', 11), wrap='word', state='disabled')
        self.txt_batch.pack(side='left', fill='both', expand=True)
        scrollbar = tk.Scrollbar(batch_result, orient='vertical', command=self.txt_batch.yview)
        scrollbar.pack(side='right', fill='y')
        self.txt_batch.configure(yscrollcommand=scrollbar.set)

        self.cv_bars = tk.Canvas(p, bg=PANEL2, highlightthickness=1,
                                 highlightbackground=EDGE)
        self.cv_bars.pack(fill="both", expand=True, padx=12, pady=10)
        self.cv_bars.bind("<Configure>", lambda e: self._draw_bars())

    def _build_log(self):
        tk = self.tk
        wrap = tk.Frame(self.root, bg=BG)
        wrap.pack(fill="x", padx=10, pady=(0, 8))
        self.lbl_status = tk.Label(wrap, text="就绪", bg=BG, fg=MUTED, anchor="w",
                                   font=("Microsoft YaHei UI", 9))
        self.lbl_status.pack(fill="x")
        self.txt = tk.Text(wrap, height=6, bg=PANEL2, fg=MUTED, bd=0,
                           font=("Consolas", 9), wrap="word")
        self.txt.pack(fill="x", pady=(3, 0))
        self.txt.configure(state="disabled")

    # ---------------- 小工具 ----------------
    def log(self, msg):
        stamp = time.strftime("%H:%M:%S")
        self.txt.configure(state="normal")
        self.txt.insert("end", "[%s] %s\n" % (stamp, msg))
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def set_status(self, text, color=MUTED):
        self.lbl_status.configure(text=text, fg=color)

    def _refresh_ports(self):
        ports = [p for p, _ in serial_link.list_ports()]
        self.cmb_port["values"] = ports
        if not self.var_port.get() or self.var_port.get() not in ports:
            self.var_port.set(self.args.port or board_init.detect_port() or "")

    # ---------------- 连续采集 / 分页 ----------------
    def start_batch(self):
        if self.busy or self.batch.collecting:
            self.set_status('请先完成当前操作或结束当前批次', YELLOW)
            return
        self.stop_batch_pages()
        self.batch.start()
        self.batch_finish_requested = False
        self.btn_capture.configure(text='拍照加入本批')
        display_preview.draw(self.cv_display, '        ')
        self.lbl_page.configure(text='采集中；结束后按每页3项、每页5秒展示')
        self._refresh_batch_view()
        self.set_status('批次已开始：可逐张拍摄，也可打开多张同模式图片；一位、两位可混合加入', GREEN)
        self.log('批次开始：%s' % self.batch.path)
        self._schedule_auto_capture()

    def _refresh_batch_view(self):
        values = self.batch.values
        errors = sum(e['status']=='error' for e in self.batch.data['entries'])
        pages = len(self.batch.data.get('pages', []))
        state = '采集中' if self.batch.collecting else '已结束'
        self.lbl_batch_state.configure(text='%s：%d项 / 失败%d / %d页%s' %
            (state, len(values), errors, pages, '（超过一页，仍可继续）' if pages>1 and self.batch.collecting else ''),
            fg=YELLOW if pages>1 and self.batch.collecting else CYAN)
        self.txt_batch.configure(state='normal')
        self.txt_batch.delete('1.0', 'end')
        self.txt_batch.insert('end', self.batch.data.get('full_text') or '尚无成功识别的结果')
        self.txt_batch.configure(state='disabled')
        self.txt_batch.see('end')

    def finish_batch(self):
        if not self.batch.collecting:
            self.set_status('先开始批次并加入照片', YELLOW)
            return
        self._cancel_auto_capture()
        if self.busy or self.batch_files:
            self.batch_finish_requested = True
            self.set_status('已停止自动连拍，正在处理的照片和导入队列完成后汇总', ORANGE)
            return
        self.batch.finish()
        self.batch_finish_requested = False
        self.btn_capture.configure(text='拍下并识别')
        self._refresh_batch_view()
        self.log('批次已保存：%s' % (self.batch.path/'batch.json'))
        self.replay_batch()

    def replay_batch(self):
        if self.busy or self.batch.collecting:
            self.set_status('请先结束当前批次', YELLOW)
            return
        pages = self.batch.data.get('pages', [])
        if not pages:
            self.set_status('当前批次没有成功结果可显示', YELLOW)
            return
        self.stop_batch_pages()
        if self.board.mode == 'board':
            self._play_board_pages(pages)
            return
        else:
            self.set_status('批次电脑预览：每页最多3项，停留5秒后翻页', GREEN)
        def on_page(index, text, count):
            display_preview.draw(self.cv_display, text)
            self.lbl_page.configure(text='电脑预览  第%d/%d页  ·  5秒/页  ·  空格位全部熄灭' % (index+1, count))
            self.batch.record_display('playing', index, text)
        def on_done():
            self.batch.record_display('completed')
            self.lbl_page.configure(text='电脑预览完成：共%d页，保留末页；可点“重播”' % len(pages))
        self.page_player.play(pages, on_page, on_done)

    def _submit_display(self, work, complete):
        def worker():
            try:
                result, error = work(), None
            except Exception as exc:
                result, error = None, exc
            self._queue.put(('page_callback', (complete, result, error)))
        threading.Thread(target=worker, daemon=True).start()

    def _play_board_pages(self, pages):
        session = self.batch
        connection_id = self.board.connection_id
        generation = self.board_page_player.generation + 1
        self.set_status('正在发送八位页面，收到板卡ACK后开始每页5秒计时', ORANGE)
        def requested(index, text, count):
            self.lbl_page.configure(text='等待板卡回读  第%d/%d页' % (index+1, count))
            session.record_display('requested', index, text, transport='uart')
        def accepted(index, text, count):
            display_preview.draw(self.cv_display, text)
            self.lbl_page.configure(text='板卡已回读  第%d/%d页 · 5秒/页 · 实物位序请目视核对' % (index+1, count))
            self.lbl_sync.configure(text='八位页面ACK一致：%s' % text, fg=GREEN)
            self.set_status('板卡分页播放中，电脑保留全部结果', GREEN)
            session.record_display('playing', index, text, transport='uart', ack_verified=True)
        def done():
            session.record_display('completed', transport='uart', ack_verified=True)
            self.lbl_page.configure(text='板卡分页发送完成：共%d页，保留末页；可重播' % len(pages))
        def failed(index, text, error):
            session.record_display('failed', index, text, str(error), transport='uart')
            self.lbl_sync.configure(text='板卡分页未确认', fg=RED)
            self.lbl_page.configure(text='分页停止：第%d页未确认；完整结果已保留' % (index+1))
            self.set_status('板卡分页失败：%s' % error, RED)
            self.log('板卡分页失败：%s' % error)
        self.board_page_player.play(pages,
            lambda text: self.board.set_display_page(text, connection_id,
                lambda: self.board_page_player.generation == generation), self._submit_display,
            requested, accepted, done, failed)

    def stop_batch_pages(self):
        was_playing = self.page_player.playing or self.board_page_player.playing
        self.page_player.stop()
        self.board_page_player.stop()
        if was_playing and self.batch.path is not None:
            self.batch.record_display('stopped')
            self.lbl_page.configure(text='已停止翻页；完整结果保留，可重播')

    def clear_batch(self):
        if self.busy:
            self.set_status('请等本张识别完成后清空', YELLOW)
            return
        self._cancel_auto_capture()
        self.stop_batch_pages()
        if self.board.mode == 'board':
            connection_id = self.board.connection_id
            generation = self.board_page_player.generation
            def cleared(result, error):
                if generation != self.board_page_player.generation:
                    return
                self.lbl_sync.configure(text='板卡全灭页ACK一致' if error is None else '板卡清空未确认：%s' % error,
                                        fg=GREEN if error is None else RED)
            self._submit_display(lambda: self.board.set_display_page('        ', connection_id,
                lambda: self.board_page_player.generation == generation), cleared)
        self.batch.cancel()
        self.batch = BatchSession(APP_ROOT/'logs/batches')
        self.batch_files = []
        self.batch_finish_requested = False
        self.btn_capture.configure(text='拍下并识别')
        self._refresh_batch_view()
        self.lbl_batch_state.configure(text='单次模式；原批次记录仍保存在logs/batches')
        self.lbl_page.configure(text='批次预览已清空')
        display_preview.draw(self.cv_display, '        ')

    def _cancel_auto_capture(self):
        if self.auto_job is not None:
            self.root.after_cancel(self.auto_job)
            self.auto_job = None

    def _auto_capture_changed(self):
        self._cancel_auto_capture()
        self._schedule_auto_capture()

    def _schedule_auto_capture(self):
        self._cancel_auto_capture()
        if (not self.batch.collecting or self.batch_finish_requested or self.busy
                or not self.var_auto_capture.get() or self.cam.cap is None):
            return
        try:
            seconds = float(self.var_capture_seconds.get())
            if not 2 <= seconds <= 60:
                raise ValueError()
        except ValueError:
            self.set_status('自动连拍间隔请输入2～60秒', YELLOW)
            return
        self.auto_job = self.root.after(round(seconds*1000), self._auto_capture)

    def _auto_capture(self):
        self.auto_job = None
        if (self.batch.collecting and not self.batch_finish_requested and not self.busy
                and self.var_auto_capture.get() and self.cam.cap is not None):
            self.grab_and_run()

    def _after_batch_capture(self):
        self._refresh_batch_view()
        if self.batch_files:
            self.root.after(0, self._next_batch_file)
        elif self.batch_finish_requested:
            self.finish_batch()
        else:
            self._schedule_auto_capture()

    # ---------------- 串口 ----------------
    def do_connect(self):
        if self.busy:
            return
        self.stop_batch_pages()
        if self.args.offline:
            self.set_status("本次以 --offline 启动，未打开硬件；重新正常启动后可连接", YELLOW)
            return
        port = self.var_port.get()
        if not port:
            self.set_status("没有可用串口", RED)
            return
        try:
            self.board.connect(port)
        except Exception as exc:                               # noqa: BLE001
            self.board.close()
            self.lbl_link.configure(text="串口打不开", fg=RED)
            self.set_status("打不开 %s：%s" % (port, exc), RED)
            self.log("连接 %s 失败：%s（板子没插/被别的程序占着/驱动没装）" % (port, exc))
            return
        self.lbl_link.configure(text="已连 %s" % port, fg=ORANGE)
        self.set_status("%s 已打开，做一次 HELLO 确认对面是板子…" % port, ORANGE)
        try:
            self.board.hello()
        except Exception as exc:                               # noqa: BLE001
            self.board.close()
            self.lbl_link.configure(text="%s 无应答" % port, fg=RED)
            self.set_status("%s 打开了但没有应答：%s" % (port, exc), RED)
            self.log("HELLO 无应答：若尚未初始化，请点『初始化并连接』；若初始化失败，先检查本次初始化日志。"
                     "『连接检查』仅检查HELLO；离线模式可用于验证PC算法链路。")
            return
        self.lbl_link.configure(text="板子在线", fg=GREEN)
        self.set_status("板子在线（HELLO_ACK 正确），后面识别都走硬件", GREEN)
        self.log("HELLO_ACK ok，串口后端 = %s" % serial_link.backend_name())

    def do_initialize(self):
        if self.busy:
            return
        self.stop_batch_pages()
        if self.args.offline:
            self.set_status("本次以 --offline 启动，不执行 JTAG 初始化", YELLOW)
            return
        port = self.var_port.get()
        if not port:
            self.set_status("请先选择板卡串口", YELLOW)
            return
        self.busy = True
        self.board.close()
        self._input_changed()
        self.lbl_link.configure(text="初始化中", fg=ORANGE)
        self.set_status("正在经 JTAG 初始化 PS/PL/ARM，完成后仅连接就绪…", ORANGE)
        log_dir = APP_ROOT / "logs" / ("initialize-"+uuid.uuid4().hex[:12])
        def work():
            try:
                board_init.initialize(APP_ROOT, port, log_dir)
                self.board.connect(port)
                self.board.hello()
                self._queue.put(("initialized", (port, str(log_dir))))
            except Exception as exc:
                self.board.close()
                self._queue.put(("init_error", (str(exc), str(log_dir))))
        threading.Thread(target=work, daemon=True).start()

    def do_probe(self):
        if self.busy:
            return
        self.stop_batch_pages()
        self._refresh_ports()
        if self.board.mode != "board":
            self.do_connect()
            return
        try:
            self.board.hello()
        except Exception as exc:                               # noqa: BLE001
            self.lbl_link.configure(text="无应答", fg=RED)
            self.set_status("自检失败：%s" % exc, RED)
            return
        self.set_status("连接检查通过：HELLO_ACK ok；逐分数验收需单独执行 --board-test", GREEN)
        self.log("HELLO协议检查通过；未证明权重或200路分数一致")

    # ---------------- 相机 ----------------
    def toggle_preview(self):
        if self.busy:
            return
        if self.preview_job is not None:
            self.stop_preview()
            return
        self.set_status("正在开相机（会在 #0~#3 里挑真能出画面的那个）…", ORANGE)
        self.root.update_idletasks()
        idx, std, frame = self.cam.open(0)
        if self.cam.cap is None:
            self.set_status("没有可用相机：试过 #0~#3 都打不开", RED)
            self.log("相机全打不开。可以改用『打开图片…』。")
            return
        self.btn_preview.configure(text="■ 停止预览")
        self.frame = frame
        self.capture_raw = None
        self.roi = None
        self._input_changed()
        self.source_info = {"kind": "camera", "camera_index": idx,
                            "frame_shape": list(frame.shape), "raw_shape": self.cam.actual_shape,
                            "eye_selection": self.cam.eye, "physical_eye_order_verified": False}
        self.lbl_src.configure(text="相机 #%d   预热 %d 帧后 std=%.1f" % (idx, CAM_WARMUP, std))
        if std < CAM_FLAT_STD:
            self.log("相机 #%d 预热后画面几乎恒定（std=%.1f）：镜头被挡/太暗/虚拟设备。"
                     "识别多半会失败。" % (idx, std))
            self.set_status("⚠ 画面几乎没有内容（std=%.1f）" % std, YELLOW)
        else:
            self.set_status("相机 #%d 已打开" % idx, GREEN)
        self._tick()
        self._schedule_auto_capture()

    def _tick(self):
        frame = self.cam.read()
        if frame is not None:
            self.frame = frame
            self.detected_boxes = self.preview_detector.poll()
            self.preview_detector.submit(frame, self.var_mode.get(), self.roi,
                                         'guided' if self.var_guided.get() else 'auto')
            self._paint_cam()
        else:
            self.frame = None
            self.cv_cam.delete("all")
            self.set_status("相机本次读取失败；已停止使用缓存旧帧，请重新预览", RED)
            self.stop_preview()
            return
        self.preview_job = self.root.after(60, self._tick)

    def stop_preview(self):
        self._cancel_auto_capture()
        if self.preview_job is not None:
            self.root.after_cancel(self.preview_job)
            self.preview_job = None
        self.preview_detector.invalidate()
        self.detected_boxes = []
        self.cam.close()
        self.btn_preview.configure(text="▶ 开始预览")
        self._paint_cam()

    def _paint_cam(self):
        if self.frame is None:
            return
        cw = max(80, self.cv_cam.winfo_width() - 2)
        ch = max(80, self.cv_cam.winfo_height() - 2)
        photo, scale = to_photo(self.frame, cw, ch)
        self.photo_cache = [photo]
        self._cam_scale = scale
        self._cam_off = ((cw - photo.width()) // 2, (ch - photo.height()) // 2)
        self.cv_cam.delete("all")
        self.cv_cam.create_image(self._cam_off[0], self._cam_off[1], anchor="nw",
                                 image=photo)
        if self.lbl_src.cget("text") == "还没画面":
            self.lbl_src.configure(text="画面 %dx%d" % (self.frame.shape[1],
                                                        self.frame.shape[0]))
        if self.roi:
            x, y, w, h = self.roi
            x0 = self._cam_off[0] + x * scale
            y0 = self._cam_off[1] + y * scale
            self.cv_cam.create_rectangle(x0, y0, x0 + w * scale, y0 + h * scale,
                                         outline=GREEN, width=2)
            self.cv_cam.create_text(x0 + 4, y0 + 10, anchor="w", fill=GREEN,
                                    text="识别框 %dx%d（框住数字）" % (w, h),
                                    font=("Consolas", 8))
        for x, y, w, h in self.detected_boxes:
            x0, y0 = self._cam_off[0]+x*scale, self._cam_off[1]+y*scale
            self.cv_cam.create_rectangle(x0, y0, x0+w*scale, y0+h*scale,
                                         outline=CYAN, width=2)
        if self.var_guided.get():
            for index, (x,y,w,h) in enumerate(VP.guided_capture.regions(self.frame.shape,self.var_mode.get(),self.roi)):
                gx,gy=self._cam_off[0]+x*scale,self._cam_off[1]+y*scale
                self.cv_cam.create_rectangle(gx,gy,gx+w*scale,gy+h*scale,outline=GREEN,width=2)
                label="放一个数字" if self.var_mode.get()=="single" else ("左边一位" if index==0 else "右边一位")
                self.cv_cam.create_text(gx+4,gy+12,text=label,anchor="w",fill=GREEN,
                                       font=("Microsoft YaHei UI",8,"bold"))

    # ---------------- ROI 拖框 ----------------
    def _canvas_to_image(self, cx, cy):
        if self.frame is None or not hasattr(self, "_cam_scale"):
            return None
        x = (cx - self._cam_off[0]) / self._cam_scale
        y = (cy - self._cam_off[1]) / self._cam_scale
        return int(max(0, min(x, self.frame.shape[1] - 1))), \
            int(max(0, min(y, self.frame.shape[0] - 1)))

    def _roi_start(self, event):
        if self.busy:
            return
        self.drag_from = self._canvas_to_image(event.x, event.y)
        self._roi_anchor = self.drag_from

    def _roi_move(self, event):
        if self.drag_from is None:
            return
        cur = self._canvas_to_image(event.x, event.y)
        if cur is None:
            return
        x0, y0 = self.drag_from
        x1, y1 = cur
        self.cv_cam.delete("drag")
        a = self._cam_off[0] + min(x0, x1) * self._cam_scale
        b = self._cam_off[1] + min(y0, y1) * self._cam_scale
        c = self._cam_off[0] + max(x0, x1) * self._cam_scale
        d = self._cam_off[1] + max(y0, y1) * self._cam_scale
        self.cv_cam.create_rectangle(a, b, c, d, outline=ORANGE, width=1, dash=(4, 3),
                                     tags="drag")

    def _roi_end(self, event):
        anchor = getattr(self, "_roi_anchor", None)
        cur = self._canvas_to_image(event.x, event.y)
        self.drag_from = None
        self._roi_anchor = None
        self.cv_cam.delete("drag")
        if anchor is None or cur is None:
            return
        x0, y0 = anchor
        x1, y1 = cur
        if abs(x1 - x0) < 12 or abs(y1 - y0) < 12:
            return
        self.roi = (min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
        self._input_changed()
        self.log("辅助搜索范围设为 %s；范围内仍自动找数字" % (self.roi,))
        self._paint_cam()

    def clear_roi(self):
        if self.busy:
            return
        self.roi = None
        self._input_changed()
        self.log("已清除辅助框；演示模式使用固定绿框，不需手动标记")
        self._paint_cam()

    def _input_changed(self):
        self.input_revision += 1
        self.preview_detector.invalidate()
        self.detected_boxes = []
        self.last_run_path = None
        self.last_canvas = self.last_meta = None
        self._last_bars = None
        self.lbl_digit.configure(text="—", fg=MUTED)
        self.lbl_conf.configure(text="等待新结果")
        self.lbl_where.configure(text="—")
        self.lbl_sync.configure(text="数码管：等待新结果", fg=MUTED)
        self.lbl_compare.configure(text="识别核心：原版单尺度 CNN", fg=MUTED)
        for canvas in (self.cv_gray, self.cv_mask, self.cv_tensor, self.cv_bars):
            canvas.delete("all")
        self._paint_cam()

    def annotate_last(self):
        from tkinter import simpledialog
        if not self.last_run_path:
            self.set_status("先拍摄或打开图片，产生一条样本记录", YELLOW)
            return
        text = simpledialog.askstring("记录正确答案", "请输入本次照片的正确数字（一位或两位）：", parent=self.root)
        if text is None:
            return
        try:
            sample_log.annotate(self.last_run_path, text.strip())
            self.log("真值已保存：%s；%s" % (text.strip(), self.last_run_path))
        except Exception as exc:
            self.set_status(str(exc), RED)

    # ---------------- 识别 ----------------
    def open_file(self):
        if self.busy:
            return
        from tkinter import filedialog
        if self.batch.collecting:
            paths = filedialog.askopenfilenames(title='加入本批：按文件名排序，全部使用当前一位/两位模式',
                filetypes=[('图片', '*.png *.jpg *.jpeg *.bmp *.webp'), ('全部', '*.*')])
            if paths:
                mode = self.var_mode.get()
                self.batch_files = [(path, mode) for path in sorted(paths, key=lambda p: Path(p).name.casefold())]
                self._next_batch_file()
            return
        path = filedialog.askopenfilename(
            title="选一张有手写数字的图片",
            filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp *.webp"), ("全部", "*.*")])
        if not path:
            return
        self._load_image(path)

    def _next_batch_file(self):
        if not self.batch.collecting or self.busy or not self.batch_files:
            return
        path, mode = self.batch_files.pop(0)
        self.var_mode.set(mode)
        self._load_image(path)

    def _load_image(self, path):
        try:
            data = np.frombuffer(Path(path).read_bytes(), dtype=np.uint8)
            img = cv2.imdecode(data, cv2.IMREAD_COLOR)
            if img is None:
                raise ValueError('这张图读不出来：%s' % path)
        except (OSError, ValueError, cv2.error) as exc:
            self.set_status(str(exc), RED)
            if self.batch.collecting:
                ticket = self.batch.reserve(self.var_mode.get())
                self.batch.failure(ticket, exc)
                self._after_batch_capture()
            return
        self.stop_preview()
        self.frame = img
        self.roi = None
        self._input_changed()
        self.source_info = {"kind": "file", "filename": os.path.basename(path)}
        self.capture_raw = None
        self.lbl_src.configure(text="文件  %s  %dx%d" % (os.path.basename(path),
                                                        img.shape[1], img.shape[0]))
        self._paint_cam()
        self.run_recognition()

    def grab_and_run(self):
        if self.busy:
            return
        self._cancel_auto_capture()
        if self.batch.collecting and self.cam.cap is None and self.source_info.get('kind') == 'camera':
            self.set_status('请先重新开始预览，批次只接收新相机帧', YELLOW)
            return
        if self.cam.cap is not None:
            frame = self.cam.read()
            if frame is None:
                self.frame = None
                self.stop_preview()
                self.cv_cam.delete("all")
                self.set_status("拍摄失败：没有读取到新帧，请重新开启预览", RED)
                if self.batch.collecting:
                    ticket = self.batch.reserve(self.var_mode.get())
                    self.batch.failure(ticket, '拍摄失败：未读取到新帧')
                    self._after_batch_capture()
                return
            self.frame = frame.copy()
            self.capture_raw = self.cam.raw_last.copy() if self.cam.raw_last is not None else frame.copy()
            self.source_info = {"kind": "camera", "camera_index": self.cam.index,
                                "frame_id": self.cam.frame_id, "captured_monotonic": self.cam.captured_at,
                                "eye_selection": self.cam.eye, "raw_shape": self.cam.actual_shape,
                                "physical_eye_order_verified": False}
            if not self.batch.collecting:
                self.stop_preview()
        if self.frame is None:
            frame = self.cam.read()
            if frame is None:
                self.set_status("还没有画面：先点『开始预览』或『打开图片…』", YELLOW)
                return
            self.frame = frame
        self._paint_cam()
        self.run_recognition()

    def run_recognition(self):
        if self.busy:
            return
        self.stop_batch_pages()
        if self.frame is None:
            self.set_status("没有输入图像", YELLOW)
            return
        self.busy = True
        self.last_bbox = None
        self.set_status("正在识别…", ORANGE)
        frame = self.frame.copy()
        roi = self.roi
        mode = self.var_mode.get()
        revision = self.input_revision
        context = {"mode": mode, "roi_xywh": list(roi) if roi else None,
                   "source": dict(self.source_info), "input_revision": revision,
                   "channel": self.board.mode, "port": self.board.port,
                   "strategy": "single",
                   "capture_mode": "guided" if self.var_guided.get() else "auto",
                   "compare_models": False}
        if self.batch.collecting:
            context['batch_ticket'] = self.batch.reserve(mode)
            context['display_deferred'] = True
        raw = self.capture_raw.copy() if self.capture_raw is not None else None
        threading.Thread(target=self._work, args=(frame, roi, mode, revision, context, raw), daemon=True).start()

    def _work(self, frame, roi, mode, revision, context, raw=None):
        run = None
        try:
            run = sample_log.SampleRun(APP_ROOT / "logs" / "vision", frame, context)
            if raw is not None:
                MCI.write_png(run.path / "raw-camera.png", raw)
            run.data["context"]["pc_reference_model_fingerprint"] = sample_log.model_fingerprint(RESOURCE_ROOT)
            run.data["context"]["hardware_model_attested"] = False
            run.save()
            items = VP.prepare(frame, mode=mode, roi=roi, capture_mode=context["capture_mode"])
            run.add_items(items)
            results = IP.recognize(self.board, items, run, RESOURCE_ROOT, context["strategy"], context["compare_models"],
                                   update_display=not context.get('display_deferred', False))
            run.finish(IP.serializable(results))
            self._queue.put(("recognized", (results, frame, revision, str(run.path), context.get('batch_ticket'))))
        except Exception as exc:                               # noqa: BLE001
            if run is not None:
                try:
                    run.finish(error=exc)
                except Exception as save_error:
                    exc = RuntimeError("%s；保存失败记录也失败：%s" % (exc, save_error))
            self._queue.put(("err", (exc, str(run.path) if run else None, context.get('batch_ticket'))))

    def _poll_queue(self):
        """worker 线程只往队列里放结果，界面只由主线程碰。

        直接在线程里调 root.after 会炸 "main thread is not in main loop"
        —— 而且线程里碰 Tk 控件本来就是未定义行为。
        """
        try:
            while True:
                kind, a, *rest = self._queue.get_nowait()
                if kind == 'page_callback':
                    callback, result, error = a
                    callback(result, error)
                    continue
                if kind == 'benchmark_progress':
                    self.set_status(a, ORANGE)
                    continue
                batch_ticket = None
                if kind == 'benchmark_done':
                    report,path=a
                    self.benchmark_cancel=None
                    if report.get('valid_for_hardware_report'):
                        s=report['statistics']
                        msg='ARM %.3f ms；ARM+PL %.3f ms；加速比 %.3f×'%(s['cpu']['mean_ms'],s['pl']['mean_ms'],report['speedup'])
                        if report['speedup'] < 1:
                            msg += '（ARM+PL耗时为ARM的%.2f倍）'%(1/report['speedup'])
                        self.set_status(msg,GREEN if report['speedup']>=1 else YELLOW)
                        self.log(msg+'；固定20张×5轮，逐分数一致；日志 '+str(path))
                    else:
                        self.set_status('未生成有效加速比：'+report.get('error',report.get('restore_error',report['status'])),YELLOW)
                        self.log('性能测试 '+report['status']+'；日志 '+str(path))
                elif kind == "recognized":
                    results, frame, revision, log_path = a[:4]
                    batch_ticket = a[4] if len(a)>4 else None
                    if batch_ticket:
                        self.batch.success(batch_ticket, ''.join(str(r['digit']) for r in results), log_path)
                    if revision != self.input_revision:
                        self.log("输入已变化，旧结果仅归档：%s" % log_path)
                    else:
                        self.last_run_path = log_path
                        if self.preview_job is None:
                            self.detected_boxes = [r["meta"]["bbox"] for r in results]
                        if len(results) == 2:
                            self._show_two(results, frame)
                        else:
                            r = results[0]
                            self._show_one(r["canvas"], r["meta"], r["digit"], r["display_scores"], r["where"], r["ms"], frame)
                        self._show_extra(results)
                        self._paint_cam()
                        self.log("可回放样本：%s" % log_path)
                elif kind == "initialized":
                    port, log_path = a
                    self.lbl_link.configure(text="板子在线", fg=GREEN)
                    self.set_status("初始化和 HELLO 通过，等待拍摄；未运行20张验收", GREEN)
                    self.log("初始化就绪 %s；日志 %s" % (port, log_path))
                elif kind == "init_error":
                    self.lbl_link.configure(text="初始化失败", fg=RED)
                    self.set_status(a[0], RED)
                    self.log(a[0] if "LOG=" in a[0] else "%s；日志 %s" % a)
                elif kind == "one":
                    self._show_one(*a)
                elif kind == "two":
                    self._show_two(a, rest[0])
                elif kind == "err":
                    self._show_error(*a[:2])
                    batch_ticket = a[2] if len(a)>2 else None
                    if batch_ticket:
                        self.batch.failure(batch_ticket, a[0], a[1])
                self.busy = False
                if batch_ticket:
                    self._after_batch_capture()
        except queue.Empty:
            pass
        except Exception:                                      # noqa: BLE001
            self.busy = False
            self._cancel_auto_capture()
            self._show_error(sys.exc_info()[1])
        finally:
            self.root.after(30, self._poll_queue)

    def _show_error(self, exc, path=None):
        msg = str(exc)
        self.set_status("没识别出来：%s" % msg, RED)
        self.log("识别失败：%s" % msg)
        if path:
            self.last_run_path = path
            self.log("失败现场：%s" % path)
        self.lbl_digit.configure(text="—", fg=MUTED)
        self.lbl_conf.configure(text="分数间隔 —")
        self.lbl_where.configure(text="—")
        self.lbl_sync.configure(text="数码管：未确认同步", fg=RED)
        self.lbl_compare.configure(text="识别核心：原版单尺度 CNN", fg=MUTED)
        self.cv_tensor.delete("all")
        self.cv_mask.delete("all")
        self.cv_gray.delete("all")
        self._last_bars = None
        self.cv_bars.delete("all")
        self.detected_boxes = []
        if isinstance(exc, (KeyError, AttributeError, TypeError)):
            self.log(traceback.format_exc())

    def _paint_stages(self, frame, meta, canvas):
        gray = DF._to_gray(frame)                              # noqa: SLF001
        x, y, w, h = meta["bbox"]
        crop = gray[max(0, y):y + h, max(0, x):x + w]
        if crop.size:
            self._ph_gray = gray_to_photo(crop, 148, nearest=False)
            self.cv_gray.delete("all")
            self.cv_gray.create_image(74, 74, anchor="center", image=self._ph_gray)

        fg = meta.get("foreground")
        if fg is not None and fg.size:
            self._ph_mask = gray_to_photo(fg, 148)
            self.cv_mask.delete("all")
            self.cv_mask.create_image(74, 74, anchor="center", image=self._ph_mask)

        self._ph_tensor = gray_to_photo(canvas, 194)
        self.cv_tensor.delete("all")
        self.cv_tensor.create_image(98, 98, anchor="center", image=self._ph_tensor)

        self.lbl_meta.configure(
            text="极性 %s   墨迹 %.0f%%\n识别框 %d,%d %dx%d   Otsu 阈值 %d"
                 % (meta.get("polarity", "?"),
                    meta.get("foreground_fraction", 0.0) * 100,
                    x, y, w, h, meta.get("otsu_threshold", 0)))

    def _show_one(self, canvas, meta, digit, logits, where, ms, frame):
        self.last_canvas, self.last_meta = canvas, meta
        self.last_bbox = meta["bbox"]
        assessment = VP.assess_logits(logits)
        gap = logits[digit] - max(v for i, v in enumerate(logits) if i != digit)
        self._paint_stages(frame, meta, canvas)
        self.lbl_digit.configure(text=str(digit), fg=CYAN)
        score_name = "融合分数" if meta.get("score_kind") == "fusion_score_x10000" else "原始INT32"
        self.lbl_conf.configure(text="%s 领先%d；非正确概率" % (score_name, gap),
                                fg=YELLOW if assessment["needs_review"] else TEXT)
        self.lbl_where.configure(text="%s   %.0f ms" % (where, ms), fg=MUTED)
        self._last_bars = logits
        self._draw_bars()
        if assessment["needs_review"] or meta.get("warnings"):
            self.set_status("预测 %d，建议复核或重拍（%s，%.0f ms）" % (digit, where, ms), YELLOW)
        else:
            self.set_status("预测 %d（%s，%.0f ms）" % (digit, where, ms), GREEN)
        self.log("识别结果 %d   极性=%s 领先=%d 通道=%s 耗时=%.0fms"
                 % (digit, meta.get("polarity", "?"), gap, where, ms))

    def _show_two(self, results, frame):
        self._last_bars = None
        lb = results[0]["meta"]
        rb = results[1]["meta"]
        bbox = (lb["bbox"][0], lb["bbox"][1],
                (rb["bbox"][0] + rb["bbox"][2]) - lb["bbox"][0],
                max(lb["bbox"][3], rb["bbox"][3]))
        combo = {"bbox": bbox,
                 "polarity": lb.get("polarity", "?"),
                 "foreground": lb.get("foreground"),
                 "foreground_fraction": (lb.get("foreground_fraction", 0.0)
                                         + rb.get("foreground_fraction", 0.0)) / 2.0,
                 "otsu_threshold": lb.get("otsu_threshold", 0)}
        self._paint_stages(frame, combo, results[0]["canvas"])
        text = "".join(str(r["digit"]) for r in results)
        self.lbl_digit.configure(text=text, fg=CYAN)
        self.lbl_conf.configure(text="自动分离左右两个数字；28×28预览为左侧", fg=TEXT)
        self.lbl_where.configure(text="%s   %.0f ms" % (results[0]["where"],
                                                        sum(r["ms"] for r in results)),
                                 fg=MUTED)
        self.cv_bars.delete("all")
        self.cv_bars.create_text(12, 18, anchor="w", fill=TEXT,
                                 text="左：%d    右：%d" % (results[0]["digit"],
                                                          results[1]["digit"]),
                                 font=("Consolas", 12, "bold"))
        review = any(VP.assess_logits(r["display_scores"])["needs_review"] or r["meta"].get("warnings") for r in results)
        self.set_status("预测两位数 %s%s" % (text, "，建议复核或重拍" if review else ""), YELLOW if review else GREEN)
        self.log("两位数结果 %s" % text)

    def _show_extra(self, results):
        display = results[0]["display"]
        if display.get('deferred'):
            self.lbl_sync.configure(text='本张加入批次；结束后汇总显示', fg=CYAN)
        elif display["verified"]:
            self.lbl_sync.configure(text="数码管ACK=%d，与CNN结果一致" % display["ack"], fg=GREEN)
            self.log("DISPLAY写入%d，ACK=%d；实物显示仍需目视核对" % (display["requested"], display["ack"]))
        else:
            self.lbl_sync.configure(text="离线：未写数码管", fg=MUTED)
        self.lbl_compare.configure(text="识别核心：原版去阴影 / 笔画归一化 / CNN", fg=MUTED)
        self.log("策略=%s；各尺度预测=%s" % (results[0]["strategy"],
                  [[route["digit"] for route in r["raw_routes"]] for r in results]))

    def _draw_bars(self):
        logits = getattr(self, "_last_bars", None)
        cv = self.cv_bars
        cv.delete("all")
        w = cv.winfo_width()
        h = cv.winfo_height()
        if w < 60 or h < 60:
            return
        if not logits:
            cv.create_text(w // 2, h // 2, fill=MUTED,
                           text="还没有结果", font=("Microsoft YaHei UI", 10))
            return
        best = max(range(10), key=lambda i: logits[i])
        vmax = max(abs(v) for v in logits) or 1
        row_h = min(30, (h - 30) // 10)
        label_w = 46
        value_w = 108
        bar_max = max(40, w - label_w - value_w - 24)
        for i in range(10):
            y = 14 + i * row_h
            cv.create_text(10, y + row_h / 2, anchor="w", fill=MUTED,
                           text="[%d]" % i, font=("Consolas", 9))
            bw = int(bar_max * abs(logits[i]) / vmax)
            color = GREEN if i == best else ("#3d4a5e" if logits[i] < 0 else "#2f6f8f")
            cv.create_rectangle(label_w, y + 3, label_w + max(1, bw), y + row_h - 5,
                                fill=color, outline="")
            cv.create_text(w - 8, y + row_h / 2, anchor="e",
                           fill=TEXT if i == best else MUTED,
                           text="%d" % logits[i], font=("Consolas", 9))
        cv.create_text(label_w, 6, anchor="w", fill=CYAN,
                       text="argmax = %d" % best, font=("Microsoft YaHei UI", 8, "bold"))

    def on_close(self):
        if self.busy:
            self.set_status("操作进行中，请完成后关闭，避免中断初始化或样本保存", YELLOW)
            return
        self.stop_preview()
        self.stop_batch_pages()
        self.batch.cancel()
        self.board.close()
        self.root.destroy()


def main(argv=None):
    parser = argparse.ArgumentParser(description="EES-331 边缘视觉推理工作台")
    parser.add_argument("--port", help="串口，例如 COM7")
    parser.add_argument("--offline", action="store_true", help="不接板子，用 PC 金标准")
    parser.add_argument("--selftest", action="store_true", help="跑离线自检后退出")
    parser.add_argument("--image", help="不开界面，直接识别一张图片（用于脚本/验收）")
    parser.add_argument('--batch-manifest', type=Path, help='离线图片批次JSON清单，支持一位/两位混合；生成5秒分页计划')
    parser.add_argument("--roi", type=int, nargs=4, metavar=("X", "Y", "W", "H"),
                        help="可选辅助搜索范围；默认全图自动定位")
    parser.add_argument("--mode", choices=("single", "two"), default="single")
    parser.add_argument("--capture-mode", choices=("guided", "auto"), default="guided",
                        help="默认固定取景框，复用原版识别；auto为全图自动候选")
    parser.add_argument("--initialize", "--flash", dest="initialize", action="store_true", help="JTAG初始化新PS/PL/ARM载荷；需要 --port")
    parser.add_argument("--check-initializer", action="store_true", help="仅校验本版初始化脚本、bit/ELF/PS载荷及ELF布局，不访问硬件")
    parser.add_argument("--check-jtag-runtime", action="store_true", help="仅启动Vivado XSDB验证运行库，不连接JTAG或串口")
    parser.add_argument("--output-dir", type=Path, help="拍摄 / 回放记录根目录")
    parser.add_argument("--label", help="保存人工真值，不参与识别决策")
    parser.add_argument("--prepare-only", action="store_true", help="只定位并保存输入，不进行推理")
    parser.add_argument("--strategy", choices=("single",), default="single", help="原版单尺度CNN")
    parser.add_argument("--no-compare", action="store_true", default=True, help=argparse.SUPPRESS)
    parser.add_argument("--board-test", action="store_true", help="显式执行板卡20张/200分数及DISPLAY验收；需要--port")
    parser.add_argument('--benchmark',action='store_true',help='无相机ARM/PL性能对比，需明确--port；先用本版单独初始化')
    parser.add_argument("--camera-eye", choices=("auto", "first", "second", "full"), default="auto",
                        help="自动模式遇到2560×800拼接帧使用第一半；物理左右尚未确认")
    args = parser.parse_args(argv)
    if args.benchmark:
        if not args.port or any((args.offline,args.initialize,args.selftest,args.image,args.batch_manifest,
                                args.board_test,args.check_initializer,args.check_jtag_runtime,args.prepare_only,args.label)):
            parser.error('--benchmark需明确--port且单独执行，不能与离线/初始化/图片等动作混用')
        import arm_benchmark
        return arm_benchmark.run_cli(Board(),args.port,RESOURCE_ROOT,args.output_dir or APP_ROOT/'logs/benchmark')
    if args.check_jtag_runtime and (args.port or args.initialize or args.image or args.selftest or args.board_test
                                   or args.check_initializer or args.prepare_only or args.label):
        parser.error("--check-jtag-runtime 单独执行，不连接板卡")
    if args.board_test and (not args.port or args.offline or args.initialize or args.selftest or args.image or args.check_initializer):
        parser.error("--board-test 需要明确串口，且单独执行；不与初始化/离线/图片混用")
    if args.selftest and (args.initialize or args.image or args.check_initializer or args.port):
        parser.error("--selftest 仅运行离线原始张量自检，不能与初始化、图片或串口混用")
    if args.batch_manifest:
        if (args.port or args.initialize or args.image or args.prepare_only or args.selftest
                or args.board_test or args.check_initializer or args.check_jtag_runtime or args.label):
            parser.error('--batch-manifest只允许离线批次，不能混用其他动作或串口参数')
        from batch_cli import run_manifest
        try:
            session = run_manifest(args.batch_manifest, args.output_dir or APP_ROOT/'logs',
                                   Board(), RESOURCE_ROOT, args.capture_mode)
            print('BATCH_LOG=%s' % (session.path/'batch.json'))
            print(json.dumps(dict(values=session.values, pages=session.data['pages'],
                page_seconds=5, hardware_accessed=False), ensure_ascii=False))
            return 1 if any(e['status']=='error' for e in session.data['entries']) else 0
        except Exception as exc:
            print('批次失败：%s' % exc, file=sys.stderr)
            return 1
    if args.check_initializer and (args.initialize or args.image or args.port):
        parser.error("--check-initializer 仅校验本版初始化载荷，不能与硬件或图片命令混用")
    if args.offline and (args.port or args.initialize):
        parser.error("--offline 不接受串口或初始化命令")
    if args.prepare_only and (not args.image or args.port or args.initialize):
        parser.error("--prepare-only 需要 --image，且不能访问板卡")
    if args.initialize and not args.port:
        parser.error("--initialize 需要明确指定 --port COMx")
    if args.label is not None and (not args.image or len(args.label) != (2 if args.mode == "two" else 1)
                                   or any(c not in "0123456789" for c in args.label)):
        parser.error("--label 需配合图片，填写与一位/两位模式一致的数字真值")
    if args.check_jtag_runtime:
        log_dir = APP_ROOT/"logs"/("runtime-"+uuid.uuid4().hex[:12])
        try:
            result = board_init.flashlink.InitializationHost().check_runtime(log_dir)
            print(json.dumps(result, ensure_ascii=False))
            print("LOG=%s" % log_dir)
            return 0
        except Exception as exc:
            print("%s；LOG=%s" % (exc, log_dir), file=sys.stderr)
            return 1
    if args.check_initializer:
        print("INITIALIZER_HASH_PASS_NO_HARDWARE", board_init.preflight(APP_ROOT))
        return 0
    if args.initialize:
        log_dir = APP_ROOT / "logs" / ("initialize-"+uuid.uuid4().hex[:12])
        try:
            board_init.initialize(APP_ROOT, args.port, log_dir)
        except Exception as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print("初始化完成；未运行20张验收。LOG=%s" % log_dir)
        if not args.image:
            return 0

    if args.board_test:
        import board_acceptance
        return board_acceptance.run(Board(), args.port, RESOURCE_ROOT,
                                    args.output_dir or APP_ROOT/"logs/board-acceptance")

    if args.selftest:
        return selftest()
    if args.image:
        return run_image(args)

    import tkinter as tk
    if getattr(sys, "frozen", False) and sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)
    root = tk.Tk()
    app = Studio(root, args)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
    return 0


def run_image(args):
    """无界面识别一张图。既方便脚本化验收，也方便你确认"到底是算法问题还是相机问题"。"""
    # 用 Path.read_bytes 而不是 np.fromfile：冻结后的 exe 里 np.fromfile
    # 走 ANSI 路径，遇到中文文件名会直接 FileNotFoundError（实测）。
    data = np.frombuffer(Path(args.image).read_bytes(), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        print("读不出这张图：%s" % args.image)
        return 2
    print("输入 %s  %dx%d" % (args.image, img.shape[1], img.shape[0]))
    roi = tuple(args.roi) if args.roi else None

    board = Board()
    run = None
    try:
        context = {"mode": args.mode, "roi_xywh": list(roi) if roi else None,
                   "source": {"kind": "file", "filename": Path(args.image).name},
                   "requested_channel": "board" if args.port else "offline",
                   "pc_reference_model_fingerprint": sample_log.model_fingerprint(RESOURCE_ROOT),
                   "hardware_model_attested": False,
                   "strategy": getattr(args, "strategy", "single"),
                   "capture_mode": getattr(args, "capture_mode", "auto"),
                   "compare_models": False}
        run = sample_log.SampleRun(args.output_dir or (APP_ROOT/"logs"/"vision"), img, context)
        if args.label is not None:
            run.data["ground_truth"] = args.label
            run.save()
        items = VP.prepare(img, mode=args.mode, roi=roi, capture_mode=context["capture_mode"])
        run.add_items(items)
        if args.port:
            board.connect(args.port)
            board.hello()
            print("板子 %s HELLO_ACK ok" % args.port)
        results = []
        if not args.prepare_only:
            results = IP.serializable(IP.recognize(board, items, run, RESOURCE_ROOT,
                                      context["strategy"], context["compare_models"]))
            for result in results:
                print(json.dumps(result, ensure_ascii=False))
        run.finish(results)
        if args.label is not None:
            sample_log.annotate(run.path, args.label)
        print("LOG=%s" % run.path)
        return 0
    except Exception as exc:                                   # noqa: BLE001
        print("没识别出来：%s" % exc)
        if run is not None:
            run.finish(error=exc)
            print("LOG=%s" % run.path)
        return 1
    finally:
        board.close()


def selftest():
    """只验证冻结的原始28×28张量及200路分数，不执行定位或硬件操作。"""
    import glob
    base = os.path.join(RESOURCE_ROOT, "samples", "_frozen")
    labels_path = os.path.join(base, "labels.txt")
    if not os.path.isfile(labels_path):
        print("自检需要 samples/_frozen，找不到：%s" % base)
        return 2
    labels = [int(v) for v in open(labels_path, encoding="utf-8").read().split()]
    ok = 0
    expected = [[int(v) for v in line.split()] for line in
                Path(base, "expected_scores.txt").read_text().splitlines() if line.strip()]
    matched_logits = 0
    for i, lab in enumerate(labels):
        rows = open(os.path.join(base, "img_%02d.txt" % i), encoding="utf-8").read().split("\n")
        arr = np.array([[int(v, 16) for v in ln.split()] for ln in rows if ln.strip()],
                       dtype=np.uint8)
        pred, logits = offline_ref.infer(arr)
        ok += int(pred == lab)
        matched_logits += sum(a == b for a, b in zip(logits, expected[i]))
        print("  img_%02d 真值=%d 预测=%d %s" % (i, lab, pred, "OK" if pred == lab else "BAD"))
    print("冻结原始张量分类 %d/%d；分数 %d/%d；未访问相机/串口/JTAG；不含预处理验收"
          % (ok, len(labels), matched_logits, len(labels)*10))
    return 0 if ok == len(labels) and matched_logits == len(labels)*10 else 1


if __name__ == "__main__":
    raise SystemExit(main())
