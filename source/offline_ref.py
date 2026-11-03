"""offline_ref.py —— 离线 INT8 金标准（不接板子也能验收双位切分是否正确）

权重与 S 标定和硬件完全一致（ADR-0002 整数管线）：
    x_int8 = uint8 >> 1,  中间层 clamp((acc+bias) >>> S, 0, 127),  FC2 不移位
所以本模块给出的 10 路 INT32 与板子 RESULT 寄存器逐位相同 —— 这是 v3.1 已经
用 200/200 个 logits 验证过的事实（见 修复说明.txt）。

用途：在实验室外/没插板子时，验证"切两半 → 各自 784 张量 → 分类"整条链路
是否真的认得出两位数。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

import mnist_ref_int8 as ref

if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    # PyInstaller onefile：源码与 --add-data 的东西都被解包到 _MEIPASS，
    # 权重目录跟 offline_ref 同级，所以 BASE 指过去即可。
    BASE = Path(sys._MEIPASS)
else:
    BASE = Path(__file__).resolve().parent
if (BASE / "resources").is_dir():
    BASE = BASE / "resources"
elif (BASE.parent / "resources").is_dir():
    BASE = BASE.parent / "resources"
LAYERS = ("Conv1", "Conv2", "FC1", "FC2")
_CACHE = {}


def load_model(base=None):
    """加载 int8 权重 / bias32 / S 标定（缓存，只读一次）。"""
    base = Path(base) if base is not None else BASE
    key = str(base.resolve())
    if key in _CACHE:
        return _CACHE[key]

    wdir = base / "weights_int8"
    fdir = base / "weights_fp32"
    weights = {}
    for name in LAYERS:
        path = wdir / ("%s_int8.txt" % name)
        if not path.exists():
            raise FileNotFoundError("缺少量化权重: %s" % path)
        weights[name] = ref.read_signed(str(path))

    scales = {}
    for line in (wdir / "scales.txt").read_text(encoding="utf-8").splitlines():
        if line.strip():
            name, value = line.split()
            scales[name] = float(value)

    scale_x = 1.0 / 127.0
    bias32 = {}
    for name in LAYERS:
        path = fdir / ("%s_b.npy" % name)
        if not path.exists():
            raise FileNotFoundError("缺少 FP32 bias: %s" % path)
        bias32[name] = np.round(np.load(path) / (scale_x * scales[name])).astype(np.int64)

    model = {
        "weights": weights,
        "bias32": bias32,
        "S": (ref.S_conv1, ref.S_conv2, ref.S_fc1),
        "base": key,
    }
    _CACHE[key] = model
    return model


def infer(pixels, base=None):
    """pixels: 28x28 uint8（0..255，ARM 内才 >>1） -> (digit, logits[10] int64)"""
    pixels = np.asarray(pixels)
    if pixels.shape != (28, 28):
        raise ValueError("需要 28x28 张量，实际 %r" % (pixels.shape,))
    if pixels.dtype != np.uint8:
        raise ValueError("需要 uint8 张量，实际 %s" % pixels.dtype)
    model = load_model(base)
    logits = ref.int_forward(pixels, model["weights"], model["bias32"], *model["S"])
    logits = [int(v) for v in logits]
    return int(np.argmax(logits)), logits
