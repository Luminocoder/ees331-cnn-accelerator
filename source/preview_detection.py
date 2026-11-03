"""Bounded asynchronous camera localization. No inference, UART or sample logs.

The GUI owns submit/poll/invalidate; workers only process an immutable snapshot
and return through a private queue. Invalidating never starts a second worker.
"""
import queue
import threading
import time

import guided_capture
import vision_pipeline


def detect_boxes(frame, mode, roi, capture_mode):
    if capture_mode == 'guided':
        boxes = []
        # A missing digit in one guide must not hide the other guide's live box.
        for guide in guided_capture.regions(frame.shape, mode, roi):
            try:
                item = guided_capture.prepare(frame, 'single', guide)[0]
            except ValueError:
                continue
            boxes.append(item['meta']['bbox'])
        return boxes
    try:
        return [item['meta']['bbox'] for item in
                vision_pipeline.prepare(frame, mode, roi, capture_mode)]
    except ValueError:
        return []


class PreviewDetector:
    def __init__(self, detect=detect_boxes, interval=.20, max_age=1.0, clock=time.monotonic):
        self.detect = detect
        self.interval, self.max_age, self.clock = interval, max_age, clock
        self._generation = 0
        self._running = False
        self._next_at = 0.0
        self._updated_at = float('-inf')
        self._boxes = []
        self._results = queue.Queue()

    def invalidate(self):
        self._generation += 1
        self._boxes = []
        self._updated_at = float('-inf')
        self._next_at = 0.0

    def submit(self, frame, mode, roi, capture_mode):
        now = self.clock()
        if self._running or now < self._next_at:
            return False
        snapshot = frame.copy()
        roi = tuple(roi) if roi is not None else None
        token = self._generation
        self._running = True
        self._next_at = now + self.interval

        def work():
            try:
                boxes = self.detect(snapshot, mode, roi, capture_mode)
            except Exception:
                # Detection is advisory. A failed frame clears the previous box.
                boxes = []
            self._results.put((token, now, boxes))

        threading.Thread(target=work, daemon=True).start()
        return True

    def poll(self):
        now = self.clock()
        try:
            while True:
                token, started_at, boxes = self._results.get_nowait()
                self._running = False
                if token == self._generation and now - started_at <= self.max_age:
                    self._boxes = [list(box) for box in boxes]
                    self._updated_at = started_at
        except queue.Empty:
            pass
        if now - self._updated_at > self.max_age:
            self._boxes = []
        return [box.copy() for box in self._boxes]
