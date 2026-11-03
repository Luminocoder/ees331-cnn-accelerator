"""Live localization concurrency, invalidation and hardware isolation regressions."""
import sys
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT/'source'), str(ROOT/'tests')]
import numpy as np
import preview_detection as preview
import test_demo
import vision_pipeline
import ees331_studio as studio


def finish(detector):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        boxes = detector.poll()
        if not detector._running:
            return boxes
        time.sleep(.005)
    raise AssertionError('preview worker did not finish')


class Preview(unittest.TestCase):
    def test_one_worker_snapshot_and_no_backlog_even_after_invalidation(self):
        gate = threading.Event()
        seen = []
        def detect(frame, *args):
            gate.wait(2)
            seen.append(int(frame[0, 0]))
            return [[int(frame[0, 0]), 0, 4, 4]]
        detector = preview.PreviewDetector(detect=detect, interval=0)
        frame = np.ones((8, 8), np.uint8)
        self.assertTrue(detector.submit(frame, 'single', None, 'guided'))
        frame[:] = 9
        for _ in range(50):
            self.assertFalse(detector.submit(frame, 'single', None, 'guided'))
        detector.invalidate()
        self.assertFalse(detector.submit(frame, 'two', None, 'guided'))
        gate.set()
        self.assertEqual(finish(detector), [])
        self.assertEqual(seen, [1])
        self.assertTrue(detector.submit(frame, 'two', None, 'guided'))
        self.assertEqual(finish(detector), [[9, 0, 4, 4]])
        self.assertEqual(seen, [1, 9])

    def test_rate_limit_and_box_expiration(self):
        now = [0.0]
        detector = preview.PreviewDetector(detect=lambda *a: [[1, 2, 3, 4]], clock=lambda: now[0])
        frame = np.ones((8, 8), np.uint8)
        detector.submit(frame, 'single', None, 'guided')
        self.assertEqual(finish(detector), [[1, 2, 3, 4]])
        now[0] = .1
        self.assertFalse(detector.submit(frame, 'single', None, 'guided'))
        now[0] = 1.1
        self.assertEqual(detector.poll(), [])
        self.assertTrue(detector.submit(frame, 'single', None, 'guided'))
        self.assertEqual(finish(detector), [[1, 2, 3, 4]])

    def test_failure_and_blank_clear_previous_boxes(self):
        responses = iter([[[1, 2, 3, 4]], [], ValueError('blank')])
        def detect(*args):
            response = next(responses)
            if isinstance(response, Exception):
                raise response
            return response
        detector = preview.PreviewDetector(detect=detect, interval=0)
        frame = np.ones((8, 8), np.uint8)
        for expected in ([[1, 2, 3, 4]], [], []):
            detector.submit(frame, 'single', None, 'guided')
            self.assertEqual(finish(detector), expected)

    def test_late_frame_is_discarded(self):
        now = [0.0]
        gate = threading.Event()
        def detect(*args):
            gate.wait(2)
            return [[1, 2, 3, 4]]
        detector = preview.PreviewDetector(detect=detect, clock=lambda: now[0])
        detector.submit(np.ones((8, 8), np.uint8), 'single', None, 'guided')
        now[0] = 2
        gate.set()
        self.assertEqual(finish(detector), [])

    def test_partial_pair_has_live_box_but_still_cannot_be_recognized(self):
        frame = test_demo.demo_frame('50')
        x, y, w, h = preview.guided_capture.regions(frame.shape, 'two')[1]
        frame[y:y+h, x:x+w] = 255
        boxes = preview.detect_boxes(frame, 'two', None, 'guided')
        self.assertEqual(len(boxes), 1)
        self.assertLess(boxes[0][0], x)
        with self.assertRaises(ValueError):
            vision_pipeline.prepare(frame, 'two', capture_mode='guided')

    def test_live_boxes_match_capture_without_inference_uart_or_logs(self):
        def forbidden(*args, **kwargs):
            raise AssertionError('live preview accessed inference, UART or sample logs')
        frame = test_demo.demo_frame('5')
        with patch.object(studio.Board, 'infer', side_effect=forbidden), \
                patch.object(studio.offline_ref, 'infer', side_effect=forbidden), \
                patch.object(studio.serial_link, 'open_port', side_effect=forbidden), \
                patch.object(studio.sample_log, 'SampleRun', side_effect=forbidden):
            for mode in ('guided', 'auto'):
                boxes = preview.detect_boxes(frame, 'single', None, mode)
                actual = vision_pipeline.prepare(frame, capture_mode=mode)
                self.assertEqual(boxes, [item['meta']['bbox'] for item in actual])
                self.assertEqual(len(boxes), 1)
            self.assertEqual(preview.detect_boxes(np.full_like(frame, 255), 'single', None, 'guided'), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
