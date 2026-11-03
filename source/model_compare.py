"""PC-only MLP comparison. These weights cannot replace the FPGA INT8 CNN."""
import hashlib
from pathlib import Path

import numpy as np
from PIL import Image  # Required: do not silently select the peer's nearest-neighbour fallback.

import canvas_preprocess

MODELS = {"mlp_old": "mnist_weights_v1_98p18.npz", "mlp_new": "mnist_weights.npz",
          "mlp_big": "mnist_weights_big.npz"}
_CACHE = {}


def load(path):
    path = Path(path).resolve()
    if path not in _CACHE:
        with np.load(path, allow_pickle=False) as archive:
            if set(archive.files) != {"arr_%d" % i for i in range(6)}:
                raise ValueError("MLP requires arr_0..arr_5")
            weights = [archive["arr_%d" % i].astype(np.float32) for i in range(3)]
            biases = [archive["arr_%d" % (i+3)].astype(np.float32) for i in range(3)]
        size = 784
        for w, b in zip(weights, biases):
            if w.ndim != 2 or w.shape[0] != size or b.shape != (1, w.shape[1]):
                raise ValueError("Incompatible MLP matrix/bias layout")
            if not np.isfinite(w).all() or not np.isfinite(b).all():
                raise ValueError("Non-finite MLP weights")
            size = w.shape[1]
        if size != 10:
            raise ValueError("MLP must output ten classes")
        _CACHE[path] = weights, biases, hashlib.sha256(path.read_bytes()).hexdigest()
    return _CACHE[path]


def predict(canvas, path):
    if canvas.shape != (28, 28) or canvas.dtype != np.uint8:
        raise ValueError("Comparison requires the saved raw uint8 28x28 input")
    weights, biases, sha = load(path)
    a = canvas.reshape(1, 784).astype(np.float32)/255.0
    for w, b in zip(weights[:-1], biases[:-1]):
        a = np.maximum(0, a @ w + b)
    logits = (a @ weights[-1] + biases[-1])[0]
    exp = np.exp(logits-logits.max())
    distribution = exp/exp.sum()
    return {"digit": int(np.argmax(logits)), "logits": logits.tolist(),
            "softmax": distribution.tolist(), "calibrated_probability": False,
            "weights_sha256": sha, "execution": "PC float32 MLP; not FPGA"}


def compare(item, resource_root):
    root = Path(resource_root)/"mlp"
    canonical = {key: predict(item["canvas"], root/name) for key, name in MODELS.items()}
    # Reuse the peer's preprocessing on the detected foreground. The black padding
    # establishes its documented dark-border convention; no whole-photo resize.
    padded = np.pad(item["meta"]["foreground"], 4, constant_values=0)
    vector = canvas_preprocess.prepare_canvas_digit(padded)
    peer_canvas = np.rint(np.clip(vector.reshape(28, 28), 0, 1)*255).astype(np.uint8)
    report = {"canonical_input_sha256": hashlib.sha256(item["canvas"].tobytes()).hexdigest(),
              "canonical": canonical,
              "peer_preprocessing": {"algorithm": "peer-canvas-preprocess-v2; detected foreground + 4px black border",
                                     "input_sha256": hashlib.sha256(peer_canvas.tobytes()).hexdigest(),
                                     "mlp_new": predict(peer_canvas, root/MODELS["mlp_new"]),
                                     "mlp_big": predict(peer_canvas, root/MODELS["mlp_big"])},
              "writes_hardware_display": False}
    return report, peer_canvas
