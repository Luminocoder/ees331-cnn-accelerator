"""Replayable success/failure records using the existing raw-uint8 contract."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import uuid

import cv2

import mnist_camera_input as samples


def write_json(path, data):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False)+"\n",
                         encoding="utf-8")
    temporary.replace(path)


def model_fingerprint(resource_root):
    root = Path(resource_root)
    files = {}
    for pattern in ("weights_int8/*", "weights_fp32/*_b.npy"):
        for path in sorted(root.glob(pattern)):
            if path.is_file():
                files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not files:
        raise FileNotFoundError("模型文件未找到，不能记录模型版本")
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


class SampleRun:
    def __init__(self, root, frame, context):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.path = Path(root) / (stamp+"-"+uuid.uuid4().hex[:8])
        self.path.mkdir(parents=True, exist_ok=False)
        samples.write_png(self.path / "frame.png", frame)
        self.data = {"schema": "ees331-vision-run-v1", "status": "processing",
                     "created_utc": datetime.now(timezone.utc).isoformat(),
                     "frame_shape": list(frame.shape),
                     "frame_png_sha256": hashlib.sha256((self.path/"frame.png").read_bytes()).hexdigest(),
                     "context": context, "samples": [], "results": [], "ground_truth": None}
        self.save()

    def save(self):
        write_json(self.path / "run.json", self.data)

    def add_items(self, items):
        for index, item in enumerate(items):
            meta = {k: v for k, v in item["meta"].items() if k != "foreground"}
            folder = "digit_%d" % index
            samples.save_sample(item["canvas"], self.path / folder, meta,
                                item["meta"].get("foreground"))
            variants = []
            for route in item.get("variants", []):
                target = folder if route["fit"] == 20 else folder+"/fit_%d" % route["fit"]
                if route["fit"] != 20:
                    samples.save_sample(route["canvas"], self.path/target, dict(meta, fit=route["fit"]))
                variants.append({"fit": route["fit"], "folder": target,
                                 "pixels_sha256": hashlib.sha256(route["canvas"].tobytes()).hexdigest()})
            self.data["samples"].append({"folder": folder, "pixels_sha256": meta["pixels_sha256"],
                                         "variants": variants})
        self.save()

    def finish(self, results=None, error=None):
        self.data["status"] = "error" if error is not None else "completed"
        self.data["results"] = results or []
        self.data["error"] = str(error) if error is not None else None
        self.save()


def annotate(run_directory, text):
    path = Path(run_directory) / "run.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    expected = 2 if data["context"]["mode"] == "two" else 1
    if len(text) != expected or any(c not in "0123456789" for c in text):
        raise ValueError("请输入 %d 位正确数字" % expected)
    data["ground_truth"] = text
    write_json(path, data)
