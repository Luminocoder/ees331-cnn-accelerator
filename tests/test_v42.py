"""Offline regression with hardware APIs forbidden. Run with Python -B."""
import argparse
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "source"))
import board_init
import ees331_studio as studio
import mnist_camera_input as samples
import sample_log
import vision_pipeline as pipeline


def image(index):
    path = ROOT/"resources"/"samples"/"_frozen"/("img_%02d.txt" % index)
    return np.array([[int(x, 16) for x in row.split()] for row in path.read_text().splitlines()
                     if row.strip()], dtype=np.uint8)


def paper(index=0, light=False):
    scene = np.full((240, 320), 255, np.uint8)
    scene[64:176, 64:176] = cv2.resize(255-image(index), (112, 112))
    return 255-scene if light else scene


class Regression(unittest.TestCase):
    def test_model_frozen_200_logits(self):
        expected = np.loadtxt(ROOT/"resources/samples/_frozen/expected_scores.txt", dtype=np.int64)
        for i in range(20):
            _, logits = studio.offline_ref.infer(image(i))
            self.assertEqual(logits, expected[i].tolist())

    def test_auto_without_roi_and_offset_roi(self):
        scene = paper()
        a = pipeline.prepare(scene)[0]
        b = pipeline.prepare(scene, roi=(50, 50, 150, 150))[0]
        self.assertEqual(a["canvas"].shape, (28, 28))
        self.assertEqual(a["canvas"].dtype, np.uint8)
        self.assertEqual(studio.offline_ref.infer(b["canvas"])[0], 5)
        self.assertLess(abs(a["meta"]["bbox"][0]-b["meta"]["bbox"][0]), 4)

    def test_larger_solid_distractor_not_selected(self):
        scene = paper()
        scene[40:170, 235:295] = 0
        item = pipeline.prepare(scene)[0]
        self.assertLess(item["meta"]["bbox"][0], 200)
        self.assertEqual(studio.offline_ref.infer(item["canvas"])[0], 5)

    def test_blank_and_solid_rejected(self):
        for light in (0, 255):
            with self.assertRaises(ValueError):
                pipeline.prepare(np.full((240, 320), light, np.uint8))
        scene = np.full((240, 320), 255, np.uint8)
        scene[40:170, 120:200] = 0
        with self.assertRaises(ValueError):
            pipeline.prepare(scene)

    def test_no_middle_split_and_multiple_rejected(self):
        scene = np.full((240, 600), 255, np.uint8)
        scene[60:172, 40:152] = cv2.resize(255-image(0), (112, 112))
        scene[60:172, 190:302] = cv2.resize(255-image(1), (112, 112))
        results = pipeline.prepare(scene, mode="two")
        self.assertEqual([studio.offline_ref.infer(r["canvas"])[0] for r in results], [5, 0])
        self.assertLess(results[1]["meta"]["bbox"][0], scene.shape[1]//2)
        with self.assertRaises(ValueError):
            pipeline.prepare(scene)

    def test_real_read_failure_never_returns_cached_frame(self):
        cam = studio.Camera()
        class Capture:
            def __init__(self): self.first = True
            def read(self):
                if self.first:
                    self.first = False
                    return True, paper()
                return False, None
        cam.cap = Capture()
        self.assertIsNotNone(cam.read())
        self.assertIsNone(cam.read())
        self.assertFalse(cam.read_ok)

    def test_camera_read_exception_or_changed_shape_fails_closed(self):
        for failure in (ValueError("device frame invalid"), None):
            cam = studio.Camera()
            cam.eye = "first"
            cam.last = paper()
            class Capture:
                def read(self):
                    if failure is not None:
                        raise failure
                    return True, paper()
            cam.cap = Capture()
            self.assertIsNone(cam.read())
            self.assertIsNone(cam.last)
            self.assertFalse(cam.read_ok)
            self.assertTrue(cam.last_error)

    def test_camera_rejected_device_keeps_valid_fallback_open(self):
        class Capture:
            def __init__(self, frame): self.frame, self.released = frame, False
            def isOpened(self): return True
            def set(self, *args): return True
            def read(self):
                if self.released: raise RuntimeError("released capture")
                return True, self.frame
            def release(self): self.released = True
        flat = Capture(np.full((800, 2560, 3), 77, np.uint8))
        wrong_shape = Capture(paper())
        cam = studio.Camera()
        cam.eye = "first"
        with patch.object(cv2, "VideoCapture", side_effect=[flat, wrong_shape]), \
                patch.object(studio, "CAM_ORDER", 2), patch.object(studio, "CAM_WARMUP", 1), \
                patch.object(studio.time, "sleep"):
            index, _, frame = cam.open()
        self.assertEqual(index, 0)
        self.assertFalse(flat.released)
        self.assertTrue(wrong_shape.released)
        self.assertEqual(frame.shape, (800, 1280, 3))
        cam.close()

    def test_cli_selftest_cannot_trigger_initialization(self):
        for args in (["--selftest", "--initialize", "--port", "COM99"],
                     ["--check-initializer", "--image", "unused.png"]):
            with patch.object(board_init, "initialize", side_effect=AssertionError("hardware forbidden")), \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                studio.main(args)
            self.assertEqual(error.exception.code, 2)

    def test_samples_replay_and_annotation(self):
        scene = paper()
        items = pipeline.prepare(scene)
        with tempfile.TemporaryDirectory() as temp:
            run = sample_log.SampleRun(temp, scene, {"mode": "single"})
            run.add_items(items)
            run.finish([{"digit": 5, "where": "PC金标准"}])
            sample_log.annotate(run.path, "5")
            actual = samples.load_sample(run.path/"digit_0")
            self.assertEqual(actual, items[0]["canvas"].tobytes())
            self.assertEqual(json.loads((run.path/"run.json").read_text(encoding="utf-8"))["ground_truth"], "5")
            with self.assertRaises(ValueError): sample_log.annotate(run.path, "55")

    def test_initializer_reuse_no_inference(self):
        board_init.preflight(ROOT)
        calls = []
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)/"init"
            class Host:
                def deploy(self, resource, manifest, log):
                    calls.append(manifest)
                    return {"deployment_marker": True}
            stream = SimpleNamespace(read=lambda size: b"", close=lambda: None)
            with patch.object(studio.mnist_serial, "_exchange", return_value=studio.mnist_serial.IDENTITY), \
                    patch.object(studio.mnist_serial, "set_display", return_value=0), \
                    patch.object(studio.mnist_serial, "send_image", side_effect=AssertionError("implicit inference")):
                result = board_init.initialize(ROOT, "COM99", target, host_factory=Host,
                                               open_serial=lambda *a, **kw: stream)
            self.assertTrue(result["initialization_verified"])
            self.assertFalse(result["inference_tests_run"])
            self.assertEqual(len(calls), 1)

    def test_initializer_rejects_fake_success(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)/"init"
            class Host:
                def deploy(self, *args): return {"deployment_marker": False}
            stream = SimpleNamespace(read=lambda size: b"", close=lambda: None)
            with self.assertRaises(RuntimeError):
                board_init.initialize(ROOT, "COM99", target, host_factory=Host,
                                      open_serial=lambda *a, **kw: stream)
            self.assertEqual(json.loads((target/"results.json").read_text())["status"], "INITIALIZATION_FAIL")

    def test_hardware_error_no_offline_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"输入.png"
            samples.write_png(path, paper())
            args = SimpleNamespace(image=str(path), roi=None, mode="single", port="COM99",
                                   output_dir=Path(temp)/"logs", label=None, prepare_only=False)
            with patch.object(studio.Board, "connect", side_effect=RuntimeError("simulated disconnected board")), \
                    patch.object(studio.offline_ref, "infer", side_effect=AssertionError("unexpected fallback")), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(studio.run_image(args), 1)

    def test_model_identity_changes_with_weights(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"weights_int8/weights.txt"
            path.parent.mkdir()
            path.write_text("01")
            before = sample_log.model_fingerprint(temp)
            path.write_text("02")
            self.assertNotEqual(before, sample_log.model_fingerprint(temp))

    def test_margin_is_not_calibrated_probability(self):
        assessment = pipeline.assess_logits([0]*10)
        self.assertTrue(assessment["needs_review"])
        self.assertFalse(assessment["calibrated_probability"])

    def test_stereo_auto_selects_first_half_without_duplicate_digit(self):
        cam = studio.Camera()
        frame = np.zeros((800, 2560, 3), np.uint8)
        frame[:, :1280] = 77
        frame[:, 1280:] = 155
        eye = cam._select_frame(frame)
        self.assertEqual(eye.shape, (800, 1280, 3))
        self.assertEqual(int(eye[0, 0, 0]), 77)
        self.assertEqual(cam.raw_last.shape, frame.shape)

    def test_clipped_input_not_silently_classified(self):
        with self.assertRaises(ValueError): pipeline.prepare(paper()[:, 85:180])


def evaluate(output):
    output.parent.mkdir(parents=True, exist_ok=True)
    labels = [int(x) for x in (ROOT/"resources/samples/_frozen/labels.txt").read_text().split()]
    summary, rows = {}, []
    for scenario in ("white", "inverted", "dim_gradient", "solid_distractor"):
        correct = rejected = 0
        for index, label in enumerate(labels):
            scene = paper(index, light=scenario == "inverted")
            if scenario == "dim_gradient":
                gain = np.linspace(0.60, 0.90, scene.shape[1])[None, :]
                scene = (scene.astype(np.float32)*gain).astype(np.uint8)
            if scenario == "solid_distractor": scene[40:170, 235:295] = 0
            row = {"scenario": scenario, "sample": index, "label": label}
            try:
                item = pipeline.prepare(scene)[0]
                digit, logits = studio.offline_ref.infer(item["canvas"])
                row.update(pred=digit, bbox=item["meta"]["bbox"], pixels_sha256=item["meta"]["pixels_sha256"])
                correct += digit == label
            except ValueError as exc:
                rejected += 1
                row["error"] = str(exc)
            rows.append(row)
        summary[scenario] = {"correct": correct, "rejected": rejected, "total": len(labels)}
    sample_log.write_json(output, {"hardware_accessed": False, "synthetic_only": True,
                                   "summary": summary, "samples": rows})
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    def forbidden(*a, **kw): raise AssertionError("Hardware access forbidden in offline regression")
    cv2.VideoCapture = forbidden
    studio.serial_link.open_port = forbidden
    # Fake deployment tests must not contend with a real initialization's mutex.
    board_init.flashlink.initialization_lock = contextlib.nullcontext
    if len(sys.argv) > 1 and sys.argv[1] == "--evaluate":
        evaluate(Path(sys.argv[2]))
    else:
        unittest.main(verbosity=2)
