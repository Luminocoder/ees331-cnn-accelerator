"""PC-only tests. Fake serial responses are not FPGA inference evidence."""

import argparse
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mnist_camera_input as app
import mnist_serial as wire
import cv2
import numpy as np


def digit_image():
    image = np.full((200, 200), 255, np.uint8)
    cv2.putText(image, "5", (45, 150), cv2.FONT_HERSHEY_SIMPLEX, 4, 0, 9, cv2.LINE_AA)
    return image


class FakeBoard:
    """Fragmenting transport stub with fixed logits; no model or hardware."""

    def __init__(self, ack=wire.IDENTITY, stale=False, board_error=False, bad_result=False):
        self.incoming = bytearray(b"boot ready\r\n")
        self.pending = bytearray()
        self.packets = []
        self.ack, self.stale = ack, stale
        self.board_error, self.bad_result = board_error, bad_result

    def write(self, data):
        chunk = data[:5]
        self.pending.extend(chunk)
        if len(self.pending) >= wire.HEADER.size:
            length = wire.HEADER.unpack_from(self.pending)[-1]
            total = wire.HEADER.size + length + wire.CRC.size
            if len(self.pending) == total:
                packet = wire.decode_packet(self.pending)
                self.pending.clear()
                self.packets.append(packet)
                sequence = (packet.sequence + int(self.stale)) & 0xFFFFFFFF
                if packet.kind == wire.HELLO:
                    kind, payload = wire.HELLO_ACK, self.ack
                elif self.board_error:
                    kind, payload = wire.ERROR, b"inference timeout"
                else:
                    kind = wire.SCORES
                    payload = wire.RESULT.pack(9 if self.bad_result else 3, 0, -1, 0, 100, 1, 0, 0, 0, 0, 0)
                self.incoming.extend(wire.encode_packet(kind, sequence, payload))
        return len(chunk)

    def read(self, size):
        chunk = bytes(self.incoming[:min(size, 3)])
        del self.incoming[:len(chunk)]
        return chunk


class PreprocessTests(unittest.TestCase):
    def test_dark_digit_shape_range_and_center(self):
        image, _, meta = app.prepare_digit(digit_image())
        self.assertEqual(image.shape, (28, 28))
        self.assertEqual(image.dtype, np.uint8)
        self.assertGreater(image.max(), 127)
        self.assertEqual(int(image[0].sum() + image[-1].sum()), 0)
        moments = cv2.moments(image)
        self.assertLess(abs(moments["m10"] / moments["m00"] - 13.5), 0.6)
        self.assertLess(abs(moments["m01"] / moments["m00"] - 13.5), 0.6)
        self.assertFalse(meta["accuracy_validated"])

    def test_polarity_and_auto(self):
        expected = app.prepare_digit(digit_image(), polarity="dark")[0]
        for image, polarity in ((digit_image(), "auto"), (255 - digit_image(), "auto"),
                                (255 - digit_image(), "light")):
            with self.subTest(polarity=polarity):
                np.testing.assert_array_equal(app.prepare_digit(image, polarity=polarity)[0], expected)

    def test_bgr_matches_grayscale(self):
        image = digit_image()
        np.testing.assert_array_equal(app.prepare_digit(cv2.cvtColor(image, cv2.COLOR_GRAY2BGR))[0],
                                      app.prepare_digit(image)[0])

    def test_blank_and_wrong_dtype_rejected(self):
        for image in (np.zeros((200, 200), np.uint8), np.full((200, 200), 255, np.uint8),
                      np.zeros((20, 20), np.float32), np.zeros((0, 0), np.uint8),
                      np.zeros((20, 20, 4), np.uint8)):
            with self.subTest(shape=image.shape, dtype=str(image.dtype)):
                with self.assertRaises(ValueError):
                    app.prepare_digit(image)

    def test_multiple_digits_rejected_and_roi_resolves(self):
        image = np.hstack((digit_image(), digit_image()))
        with self.assertRaisesRegex(ValueError, "Multiple"):
            app.prepare_digit(image)
        np.testing.assert_array_equal(app.prepare_digit(image, (0, 0, 200, 200))[0],
                                      app.prepare_digit(digit_image())[0])

    def test_small_noise_removed(self):
        image = digit_image()
        image[10, 10] = 0
        np.testing.assert_array_equal(app.prepare_digit(image)[0], app.prepare_digit(digit_image())[0])

    def test_roi_bounds_and_size(self):
        for roi in ((-1, 0, 10, 10), (0, 0, 0, 10), (195, 0, 10, 10), (0, 0, 2, 2)):
            with self.subTest(roi=roi), self.assertRaises(ValueError):
                app.prepare_digit(digit_image(), roi)

    def test_boundary_digit_rejected(self):
        image = np.full((100, 100), 255, np.uint8)
        image[:, 30:50] = 0
        with self.assertRaisesRegex(ValueError, "boundary"):
            app.prepare_digit(image)

    def test_aspect_ratio_of_thin_digit(self):
        image = np.full((200, 200), 255, np.uint8)
        image[30:170, 95:105] = 0
        prepared = app.prepare_digit(image)[0]
        ys, xs = np.nonzero(prepared)
        self.assertLessEqual(int(xs.max() - xs.min() + 1), 3)
        self.assertEqual(int(ys.max() - ys.min() + 1), 20)

    def test_eye_halves_and_invalid_frames(self):
        image = np.zeros((800, 2560, 3), np.uint8)
        image[:, 1280:] = 200
        self.assertEqual(int(app.select_eye(image, "first").max()), 0)
        self.assertEqual(int(app.select_eye(image, "second").min()), 200)
        for bad in (None, image[:400], np.zeros((800, 2560, 4), np.uint8)):
            with self.assertRaises(ValueError):
                app.select_eye(bad, "first")
        with self.assertRaises(ValueError):
            app.select_eye(image, "left")


class SampleAndCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def sample(self):
        image = np.arange(784, dtype=np.uint16).astype(np.uint8).reshape(28, 28)
        path = self.root / "sample"
        app.save_sample(image, path, {"test_fixture": True})
        return path, image

    def test_sample_roundtrip_and_quantization_once(self):
        path, image = self.sample()
        raw = app.load_sample(path)
        self.assertEqual(raw, image.tobytes())
        np.testing.assert_array_equal(np.frombuffer(raw, np.uint8) >> 1, image.ravel() >> 1)
        self.assertEqual(max(raw), 255)
        np.testing.assert_array_equal(app.read_image(path / "mnist28.png"), image)

    def test_overwrite_refused(self):
        path, image = self.sample()
        with self.assertRaises(FileExistsError):
            app.save_sample(image, path, {})

    def test_payload_tamper_rejected(self):
        path, _ = self.sample()
        (path / "pixels-u8.bin").write_bytes(bytes(784))
        with self.assertRaisesRegex(ValueError, "hash"):
            app.load_sample(path)

    def test_quantized_manifest_rejected(self):
        path, _ = self.sample()
        manifest = json.loads((path / "sample.json").read_text())
        manifest["representation"] = "int8"
        (path / "sample.json").write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "quantized"):
            app.load_sample(path)

    def test_unicode_png(self):
        path = self.root / "\u6570\u5b57.png"
        app.write_png(path, digit_image())
        np.testing.assert_array_equal(app.read_image(path), digit_image())

    def test_cli_prepare_and_dry_run(self):
        source = self.root / "source.png"
        app.write_png(source, digit_image())
        with redirect_stdout(io.StringIO()):
            self.assertEqual(app.main(["prepare", "--input", str(source), "--output", str(self.root / "prepared")]), 0)
            self.assertEqual(app.main(["send", "--sample", str(self.root / "prepared"), "--dry-run",
                                       "--sequence", "7", "--packet-output", str(self.root / "request.bin")]), 0)
        decoded = wire.decode_packet((self.root / "request.bin").read_bytes())
        self.assertEqual(decoded.sequence, 7)
        self.assertEqual(decoded.payload, app.load_sample(self.root / "prepared"))

    def test_real_send_guard_before_serial_import(self):
        path, _ = self.sample()
        with redirect_stderr(io.StringIO()):
            self.assertEqual(app.main(["send", "--sample", str(path), "--port", "COM5"]), 1)

    def test_dry_run_refuses_port(self):
        path, _ = self.sample()
        with redirect_stderr(io.StringIO()):
            self.assertEqual(app.main(["send", "--sample", str(path), "--dry-run", "--port", "COM5"]), 1)

    def test_blank_cli_creates_no_output(self):
        source = self.root / "blank.png"
        app.write_png(source, np.zeros((100, 100), np.uint8))
        output = self.root / "prepared"
        with redirect_stderr(io.StringIO()):
            self.assertEqual(app.main(["prepare", "--input", str(source), "--output", str(output)]), 1)
        self.assertFalse(output.exists())

    def test_cli_subprocess_help(self):
        process = subprocess.run([sys.executable, str(Path(app.__file__)), "--help"],
                                 capture_output=True, text=True, timeout=10)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn("capture", process.stdout)

    def test_mock_camera_capture_and_release(self):
        frame = np.zeros((800, 2560, 3), np.uint8)
        args = argparse.Namespace(output=str(self.root / "photo"), camera_index=0,
                                  eye="second", fps=30, warmup_frames=1, headless=True)
        with patch.object(app.cv2, "VideoCapture") as constructor:
            camera = constructor.return_value
            camera.isOpened.return_value = True
            camera.set.return_value = True
            camera.read.return_value = (True, frame)
            self.assertEqual(app.capture(args)["status"], "photo_saved")
            camera.release.assert_called_once()
        self.assertEqual(app.read_image(self.root / "photo" / "eye.png").shape, (800, 1280, 3))

    def test_wrong_camera_resolution_releases_without_saving(self):
        args = argparse.Namespace(output=str(self.root / "photo"), camera_index=0,
                                  eye="first", fps=30, warmup_frames=1, headless=True)
        with patch.object(app.cv2, "VideoCapture") as constructor:
            camera = constructor.return_value
            camera.isOpened.return_value = True
            camera.read.return_value = (True, np.zeros((720, 1280, 3), np.uint8))
            with self.assertRaisesRegex(ValueError, "2560x800"):
                app.capture(args)
            camera.release.assert_called_once()
        self.assertFalse((self.root / "photo").exists())

    def test_capture_refuses_existing_output_before_camera_open(self):
        with patch.object(app.cv2, "VideoCapture") as constructor:
            with self.assertRaises(FileExistsError):
                app.capture(argparse.Namespace(output=str(self.root)))
            constructor.assert_not_called()

    def test_roi_cancelled(self):
        with patch.object(app.cv2, "selectROI", return_value=(0, 0, 0, 0)), \
                patch.object(app.cv2, "destroyAllWindows"):
            with self.assertRaisesRegex(ValueError, "cancelled"):
                app.interactive_roi(digit_image())


class ProtocolTests(unittest.TestCase):
    def test_known_crc_vector(self):
        import zlib
        self.assertEqual(zlib.crc32(b"123456789"), 0xCBF43926)

    def test_packet_layout_and_roundtrip(self):
        pixels = bytes(i % 256 for i in range(784))
        packet = wire.image_packet(pixels, 0x12345678)
        self.assertEqual(len(packet), 800)
        self.assertEqual(packet[:12], b"MNST\x01\x02\x78\x56\x34\x12\x10\x03")
        self.assertEqual(wire.decode_packet(packet), wire.Packet(wire.IMAGE, 0x12345678, pixels))

    def test_corrupt_truncated_and_oversized_rejected(self):
        packet = wire.image_packet(bytes(784), 1)
        bad = bytearray(packet)
        bad[20] ^= 1
        for data in (bytes(bad), packet[:-1], packet + b"x", b"", b"WRNG" + packet[4:]):
            with self.subTest(size=len(data)), self.assertRaises(wire.ProtocolError):
                wire.decode_packet(data)
        for payload in (bytes(783), bytes(785)):
            with self.assertRaises(wire.ProtocolError):
                wire.image_packet(payload, 1)
        with self.assertRaises(wire.ProtocolError):
            wire.encode_packet(1, 1, bytes(1025))

    def test_version_and_sequence_bounds(self):
        packet = bytearray(wire.image_packet(bytes(784), 1))
        packet[4] = 2
        with self.assertRaises(wire.ProtocolError):
            wire.decode_packet(packet)
        for sequence in (-1, 2**32):
            with self.assertRaises(wire.ProtocolError):
                wire.image_packet(bytes(784), sequence)

    def test_partial_io_handshake_and_fixed_result(self):
        board = FakeBoard()
        result = wire.send_image(board, bytes(784), 77, timeout=1)
        self.assertEqual(result["digit"], 3)
        self.assertEqual(len(result["logits"]), 10)
        self.assertEqual([p.kind for p in board.packets], [wire.HELLO, wire.IMAGE])
        self.assertEqual(board.packets[-1].payload, bytes(784))

    def test_bad_identity_never_sends_image(self):
        board = FakeBoard(ack=b"not the receiver")
        with self.assertRaisesRegex(wire.ProtocolError, "acknowledge"):
            wire.send_image(board, bytes(784), 1, timeout=1)
        self.assertEqual([p.kind for p in board.packets], [wire.HELLO])

    def test_stale_response_never_sends_image(self):
        board = FakeBoard(stale=True)
        with self.assertRaisesRegex(wire.ProtocolError, "sequence"):
            wire.send_image(board, bytes(784), 1, timeout=1)
        self.assertEqual(len(board.packets), 1)

    def test_board_error_propagates(self):
        with self.assertRaisesRegex(wire.ProtocolError, "inference timeout"):
            wire.send_image(FakeBoard(board_error=True), bytes(784), 1, timeout=1)

    def test_result_argmax_mismatch_rejected(self):
        with self.assertRaisesRegex(wire.ProtocolError, "argmax"):
            wire.send_image(FakeBoard(bad_result=True), bytes(784), 1, timeout=1)

    def test_missing_receiver_times_out_after_hello_only(self):
        board = FakeBoard()
        board.read = lambda size: b""
        with self.assertRaises(TimeoutError):
            wire.send_image(board, bytes(784), 1, timeout=0.01)
        self.assertEqual([p.kind for p in board.packets], [wire.HELLO])

    def test_zero_write_times_out(self):
        board = FakeBoard()
        board.write = lambda data: 0
        with self.assertRaises(TimeoutError):
            wire.send_image(board, bytes(784), 1, timeout=0.01)

    def test_noise_scan_is_bounded(self):
        board = FakeBoard()
        board.incoming = bytearray(b"x" * 5000)
        with self.assertRaisesRegex(wire.ProtocolError, "4096"):
            wire.send_image(board, bytes(784), 1, timeout=1)


def run_with_evidence(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    log = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    (output / "tests.log").write_text(log.getvalue(), encoding="utf-8")
    files = [Path(app.__file__), Path(wire.__file__), Path(__file__)]
    summary = {"status": "PC_TESTS_PASS" if result.wasSuccessful() else "FAIL",
               "tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
               "opencv": cv2.__version__, "numpy": np.__version__,
               "real_camera_opened": False, "real_serial_opened": False,
               "fpga_inference_tested": False,
               "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
    if result.wasSuccessful():
        source = digit_image()
        app.write_png(output / "synthetic-photo.png", source)
        prepared, foreground, metadata = app.prepare_digit(source)
        app.save_sample(prepared, output / "synthetic-prepared", metadata, foreground)
        app.write_png(output / "preview-280.png", cv2.resize(prepared, (280, 280), interpolation=cv2.INTER_NEAREST))
        (output / "synthetic-request.bin").write_bytes(wire.image_packet(prepared.tobytes(), 7))
    app.write_json(output / "summary.json", summary)
    print(log.getvalue())
    print(json.dumps(summary, indent=2))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", help="New directory for test log, hashes and synthetic example")
    args = parser.parse_args()
    if args.artifacts:
        raise SystemExit(run_with_evidence(args.artifacts))
    unittest.main(argv=[sys.argv[0]], verbosity=2)
