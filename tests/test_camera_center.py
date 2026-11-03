"""Real 8/3 development regressions; the CNN must still decide the label."""
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT/'source'))
import cv2
import numpy as np
import camera_normalization
import guided_capture
import legacy_pipeline
import mnist_camera_input
import offline_ref
import vision_pipeline


class Center(unittest.TestCase):
    def test_camera_eights_and_real_three(self):
        directory = ROOT/'tests/fixtures/camera-83'
        for case in json.loads((directory/'cases.json').read_text())['cases']:
            with self.subTest(file=case['file']):
                frame = mnist_camera_input.read_image(directory/case['file'])
                prepare = guided_capture.prepare if case['capture_mode']=='guided' else legacy_pipeline.prepare
                baseline = prepare(frame, case['mode'], case['roi'])
                # Ensure these fixtures exactly reproduce the previous board logits.
                self.assertEqual([offline_ref.infer(i['canvas'])[1] for i in baseline], case['old_logits'])
                items = vision_pipeline.prepare(frame, case['mode'], case['roi'], case['capture_mode'])
                self.assertEqual(''.join(str(offline_ref.infer(i['canvas'])[0]) for i in items), case['expected'])
                for item in items:
                    canvas = item['canvas']
                    self.assertEqual(len(item['variants']), 1)
                    self.assertTrue(np.array_equal(canvas, item['variants'][0]['canvas']))
                    self.assertEqual(hashlib.sha256(canvas.tobytes()).hexdigest(), item['meta']['pixels_sha256'])

    def test_center_is_subpixel_and_close_to_fourteen(self):
        canvas = np.zeros((28, 28), np.uint8)
        canvas[7:20, 9:16] = 210
        canvas[12:18, 16:19] = 90
        result, meta = camera_normalization.center_input(canvas)
        moments = cv2.moments(result)
        for actual in (moments['m10']/moments['m00'], moments['m01']/moments['m00']):
            self.assertAlmostEqual(actual, 14, delta=.04)
        self.assertTrue(any(abs(x-round(x))>.01 for x in meta['subpixel_center_shift_xy']))
        self.assertEqual(result.dtype, np.uint8)

    def test_boundary_foreground_is_not_clipped_and_empty_is_rejected(self):
        canvas = np.zeros((28, 28), np.uint8)
        canvas[0, 0] = 255
        canvas[27, 27] = 20
        result, meta = camera_normalization.center_input(canvas)
        self.assertTrue(meta['center_shift_clamped'])
        self.assertTrue(np.array_equal(result, canvas))
        with self.assertRaises(ValueError):
            camera_normalization.center_input(np.zeros((28, 28), np.uint8))


if __name__ == '__main__':
    unittest.main(verbosity=2)
