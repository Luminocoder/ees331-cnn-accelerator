"""Integration regressions. Real cameras/ports/process launches are forbidden."""
import ast
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT/"source"), str(ROOT/"tests")]
import numpy as np
import board_acceptance
import board_init
import ees331_studio as studio
import flashlink
import inference_pipeline as inference
import model_compare
import sample_log
import test_v42 as fixtures
import vision_pipeline


class FakeBoard:
    def __init__(self):
        self.mode, self.writes, self.images = "board", [], []
    def connect(self, port): self.mode = "board"
    def hello(self): return None
    def infer(self, canvas):
        self.images.append(canvas.tobytes())
        digit, logits = studio.offline_ref.infer(canvas)
        return digit, logits, "板子", 0.0
    def set_display(self, value):
        self.writes.append(value)
        return value
    def close(self): self.mode = "offline"


class Integration(unittest.TestCase):
    def test_normalization_matches_original_v29_oracle(self):
        import legacy_locator
        captured=[]
        original=fixtures.cv2.distanceTransform
        def capture(mask,*args):
            captured.append(mask.copy())
            return original(mask,*args)
        for index in (0,2,4):
            captured.clear()
            with patch.object(fixtures.cv2,"distanceTransform",side_effect=capture):
                canvas,_,_=fixtures.samples.prepare_digit(fixtures.paper(index))
            actual,_,_=legacy_locator.normalize_component(captured[0])
            self.assertTrue(np.array_equal(actual,canvas))
        self.assertEqual(hashlib.sha256((ROOT/"source/mnist_camera_input.py").read_bytes()).hexdigest(),
                         "194d9cd84619312830d5c3429cb122e97bdd3f7aae43fac1aa4c90c69ab02d7e")

    def test_retired_models_cannot_enter_live_inference(self):
        for strategy,compare in (("fused",False),("single",True)):
            board=FakeBoard()
            with tempfile.TemporaryDirectory() as temporary:
                run=sample_log.SampleRun(temporary,fixtures.paper(),{"mode":"single"})
                with self.assertRaises(ValueError):
                    inference.recognize(board,[],run,ROOT/"resources",strategy=strategy,compare_models=compare)
                self.assertEqual(board.images,[])
                self.assertEqual(board.writes,[])

    def test_two_digits_two_images_one_combined_display_and_saved_inputs(self):
        scene = np.full((240, 600), 255, np.uint8)
        scene[60:172, 40:152] = fixtures.cv2.resize(255-fixtures.image(0), (112, 112))
        scene[60:172, 190:302] = fixtures.cv2.resize(255-fixtures.image(1), (112, 112))
        items = vision_pipeline.prepare(scene, mode="two")
        board = FakeBoard()
        with tempfile.TemporaryDirectory() as temporary:
            run = sample_log.SampleRun(temporary, scene, {"mode": "two"})
            run.add_items(items)
            results = inference.recognize(board, items, run, ROOT/"resources")
            self.assertEqual([r["digit"] for r in results], [5, 0])
            self.assertEqual(board.writes, [50])
            self.assertEqual(len(board.images), 2)
            for part in run.data["samples"]:
                for route in part["variants"]:
                    payload = fixtures.samples.load_sample(run.path/route["folder"])
                    self.assertEqual(hashlib.sha256(payload).hexdigest(), route["pixels_sha256"])
            self.assertNotIn("model_comparison", results[0])
            self.assertTrue(results[0]["display"]["verified"])

    def test_default_is_original_single_scale_without_comparison(self):
        scene=fixtures.paper(2)
        board=FakeBoard()
        with tempfile.TemporaryDirectory() as temporary:
            run=sample_log.SampleRun(temporary,scene,{"mode":"single"})
            items=vision_pipeline.prepare(scene)
            run.add_items(items)
            result=inference.recognize(board,items,run,ROOT/"resources")[0]
            self.assertEqual(len(board.images),1)
            self.assertEqual(result["strategy"],"single")
            self.assertNotIn("model_comparison",result)
            self.assertEqual(result["display_scores"],result["raw_routes"][0]["logits"])

    def test_wrong_display_ack_fails(self):
        board = studio.Board()
        board.mode = "board"
        with patch.object(studio.mnist_serial, "set_display", return_value=7):
            with self.assertRaises(studio.mnist_serial.ProtocolError): board.set_display(4)

    def test_display_crc_and_packet_contract(self):
        packet = studio.mnist_serial.decode_packet(studio.mnist_serial.display_packet(49, 123))
        self.assertEqual(packet.kind, 3)
        self.assertEqual(packet.sequence, 123)
        self.assertEqual(studio.mnist_serial.DISP.unpack(packet.payload), (49,))
        with self.assertRaises(ValueError): studio.mnist_serial.display_packet(100, 123)

    def test_init_requires_hello_and_display_not_only_deploy(self):
        stream = SimpleNamespace(read=lambda size: b"", close=lambda: None)
        host = SimpleNamespace(deploy=lambda *a: {"deployment_marker": True})
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(studio.mnist_serial, "_exchange", return_value=studio.mnist_serial.IDENTITY), \
                patch.object(studio.mnist_serial, "set_display", return_value=9):
            with self.assertRaises(RuntimeError):
                board_init.initialize(ROOT, "COM99", Path(temp)/"init", host_factory=lambda: host,
                                      open_serial=lambda *a, **kw: stream)
            report = json.loads((Path(temp)/"init/results.json").read_text(encoding="utf-8"))
            self.assertFalse(report["initialization_verified"])

    def test_board_acceptance_simulation_and_mismatch_detection(self):
        for corrupt in (False, True):
            board = FakeBoard()
            if corrupt:
                original = board.infer
                def changed(canvas):
                    digit, logits, channel, ms = original(canvas)
                    logits[0] += 1
                    return digit, logits, channel, ms
                board.infer = changed
            with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(board_acceptance.run(board, "COM99", ROOT/"resources", temp), int(corrupt))
                reports = list(Path(temp).glob("*/results.json"))
                report = json.loads(reports[0].read_text(encoding="utf-8"))
                self.assertEqual(report["matched_logits"], 9 if corrupt else 200)
                self.assertFalse(report["initialization_executed"])

    def test_original_guarded_bringup_is_unchanged(self):
        text = (ROOT/"resources/initialization/bringup.tcl").read_bytes()
        self.assertEqual(hashlib.sha256(text).hexdigest(), "02e0ad67d097a8b6c6449d6af7d3137c390b35fcdeac7db33bb265392d31e4bf")
        self.assertIn(b"DDR_PROBE_PASS", text)

    def test_nonhardware_modes_reject_board_acceptance(self):
        for options in (["--board-test", "--port", "COM99", "--offline"],
                        ["--board-test", "--port", "COM99", "--initialize"]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                studio.main(options)
            self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    def forbidden(*args, **kwargs): raise AssertionError("Real hardware/process access forbidden")
    studio.cv2.VideoCapture = forbidden
    studio.serial_link.open_port = forbidden
    flashlink.subprocess.Popen = forbidden
    flashlink.initialization_lock = contextlib.nullcontext
    unittest.main(verbosity=2)
