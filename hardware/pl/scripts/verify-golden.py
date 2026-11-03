"""Check frozen vectors against the existing model in a disposable source mirror."""
import argparse
import hashlib
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np


def hex_bytes(path):
    return bytes(int(token, 16) for token in path.read_text().split())


def verify(repo, allow_legacy):
    print(f"PYTHON_VERSION={sys.version.split()[0]} NUMPY_VERSION={np.__version__}", flush=True)
    if not (repo / ".workflow-mirror.json").is_file():
        raise ValueError("Refusing to regenerate golden outside a workflow source mirror")
    model = repo / "ref_model"
    vectors = model / "test_vectors"
    frozen = {name: (vectors / name).read_bytes() for name in ("int8_logits.npy", "predictions.txt")}
    images = np.load(vectors / "images.npy", allow_pickle=False)
    labels = np.loadtxt(vectors / "labels.txt", dtype=np.int64)
    if images.shape != (20, 28, 28) or images.dtype != np.uint8 or labels.shape != (20,):
        raise ValueError("Expected 20 uint8 images and 20 labels")
    if images.tobytes() != hex_bytes(vectors / "img_all.hex"):
        raise ValueError("images.npy and img_all.hex differ")

    missing_layout = not (model / "weights_fp32").is_dir() or not (model / "weights_int8/scales.txt").is_file()
    if missing_layout:
        if not allow_legacy:
            raise ValueError("Model layout incomplete. Ask A to restore weights_fp32/scales; "
                             "--allow-legacy-layout permits mirror-only reconstruction")
        print("LEGACY_LAYOUT_ADAPTER: reconstructing missing inputs in the disposable mirror", flush=True)
        fp32 = model / "weights_fp32"
        fp32.mkdir(exist_ok=True)
        for name in ("Conv1", "Conv2", "FC1", "FC2"):
            for suffix in ("", "_b"):
                target = fp32 / f"{name}{suffix}.npy"
                if not target.exists():
                    shutil.copyfile(model / target.name, target)
        if not (model / "weights_int8/scales.txt").exists():
            generated = repo.parent / "golden-quantize"
            generated.mkdir()
            shutil.copytree(fp32, generated / "weights_fp32")
            subprocess.run([sys.executable, str(model / "quantize_int8_export.py")], cwd=generated, check=True)
            for name in ("Conv1", "Conv2", "FC1", "FC2"):
                filename = f"{name}_int8.txt"
                if hex_bytes(generated / "weights_int8" / filename) != hex_bytes(model / "weights_int8" / filename):
                    raise ValueError(f"Regenerated {filename} differs from frozen weights")
            shutil.copyfile(generated / "weights_int8/scales.txt", model / "weights_int8/scales.txt")

    spec = importlib.util.spec_from_file_location("golden_model", model / "mnist_ref_int8.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    previous_cwd = Path.cwd()
    try:
        import os
        os.chdir(model)
        biases = module.compute_bias32()
    finally:
        os.chdir(previous_cwd)
    expected_bias = b"".join(int(value).to_bytes(4, "big", signed=True)
                             for name in ("Conv1", "Conv2", "FC1", "FC2") for value in biases[name])
    if hex_bytes(model / "weights_int8/bias_layout.txt") != expected_bias:
        raise ValueError("Model bias values differ from baked BRAM bias bytes")
    subprocess.run([sys.executable, str(model / "mnist_ref_int8.py")], cwd=model, check=True)
    for name, expected in frozen.items():
        actual = (vectors / name).read_bytes()
        if actual != expected:
            raise ValueError(f"Generated {name} differs from frozen output")
        print(f"SHA256 {name}: {hashlib.sha256(actual).hexdigest()}")
    logits = np.load(vectors / "int8_logits.npy", allow_pickle=False)
    golden = np.array([int(token, 16) for token in (vectors / "golden_all.hex").read_text().split()], dtype=np.uint32)
    if logits.shape != (20, 10) or logits.dtype != np.int32 or not np.array_equal(logits.view(np.uint32).ravel(), golden):
        raise ValueError("Integer model and RTL golden_all.hex differ")
    if not np.array_equal(np.argmax(logits, axis=1), labels):
        raise ValueError("Frozen 20-image classification check failed")
    print("GOLDEN_REGRESSION_PASS: 20/20, all 200 logits bit-exact")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True, help="Disposable source mirror, never authoritative source")
    parser.add_argument("--allow-legacy-layout", action="store_true")
    args = parser.parse_args()
    verify(args.repo.resolve(), args.allow_legacy_layout)
