"""Batch ordering, padding/blank cells, display deferral and 5s scheduling."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT/'source'), str(ROOT/'tests')]
from batch_capture import BatchSession, PagePlayer, display_pages
import batch_cli
import display_preview
import ees331_studio as studio
import inference_pipeline
import mnist_camera_input
import sample_log
import test_demo
import vision_pipeline


class Scheduler:
    def __init__(self):
        self.now = 0
        self.next_id = 0
        self.jobs = {}

    def after(self, milliseconds, callback):
        self.next_id += 1
        self.jobs[self.next_id] = (self.now+milliseconds, callback)
        return self.next_id

    def after_cancel(self, job):
        self.jobs.pop(job, None)

    def advance(self, milliseconds):
        target = self.now+milliseconds
        while self.jobs:
            job = min(self.jobs, key=lambda i:self.jobs[i][0])
            when, callback = self.jobs[job]
            if when > target:
                break
            self.now = when
            del self.jobs[job]
            callback()
        self.now = target


class Batch(unittest.TestCase):
    def test_three_pairs_with_true_blank_separators_and_zero_padding(self):
        self.assertEqual(display_pages(['1','02','3','45','0']), ['01 02 03','45 00   '])
        self.assertEqual(display_pages(['00','01','99']), ['00 01 99'])
        self.assertEqual(display_pages([]), [])
        for invalid in [['001'], [' 1'], ['x'], [1], ['１']]:
            with self.assertRaises(ValueError):
                display_pages(invalid)

    def test_blank_cells_have_no_lit_segment(self):
        class Canvas:
            def __init__(self): self.cells = []
            def delete(self, *args): self.cells.clear()
            def create_rectangle(self, *args, **kwargs): self.cells.append(kwargs)
        canvas = Canvas()
        display_preview.draw(canvas, '01 02 03')
        for index in (2, 5):
            segments = [s for s in canvas.cells if 'cell_%d'%index in s['tags']]
            self.assertEqual(len(segments), 7)
            self.assertTrue(all(s['fill']=='#29313f' for s in segments))

    def test_more_than_eight_digits_remain_complete_and_failures_keep_order(self):
        with tempfile.TemporaryDirectory() as temp:
            session = BatchSession(temp)
            session.start()
            for index in range(12):
                text = str(index)
                ticket = session.reserve('single' if len(text)==1 else 'two')
                session.success(ticket, text, Path(temp)/'run')
            failed = session.reserve('single')
            session.failure(failed, 'blank')
            ticket = session.reserve('two')
            session.success(ticket, '01', Path(temp)/'run')
            pages = session.finish()
            saved = json.loads((session.path/'batch.json').read_text())
            self.assertEqual(len(pages), 5)
            self.assertEqual(saved['values'], [str(i) for i in range(12)]+['01'])
            self.assertEqual(saved['full_text'], ' '.join(str(i).zfill(2) for i in range(12))+' 01')
            self.assertEqual(saved['entries'][12]['status'], 'error')
            self.assertEqual(pages[-1], '01      ')
            self.assertTrue(all(len(page)==8 for page in pages))

    def test_pending_duplicate_and_old_batch_results_cannot_be_committed(self):
        with tempfile.TemporaryDirectory() as temp:
            session = BatchSession(temp)
            session.start()
            ticket = session.reserve('single')
            with self.assertRaises(ValueError): session.finish()
            session.success(ticket, '1', Path(temp)/'run')
            with self.assertRaises(ValueError): session.success(ticket, '1', Path(temp)/'run')
            session.cancel()
            session.start()
            with self.assertRaises(ValueError): session.success(ticket, '1', Path(temp)/'run')
            self.assertEqual(session.values, [])

    def test_page_is_held_for_exactly_five_seconds_including_last_page(self):
        clock = Scheduler()
        player = PagePlayer(clock)
        shown, done = [], []
        player.play(display_pages(['1','2','3','4']),
                    lambda i,p,n:shown.append((clock.now,i,p,n)), lambda:done.append(clock.now))
        self.assertEqual(shown, [(0,0,'01 02 03',2)])
        clock.advance(4999)
        self.assertEqual(len(shown), 1)
        clock.advance(1)
        self.assertEqual(shown[-1], (5000,1,'04      ',2))
        clock.advance(4999)
        self.assertEqual(done, [])
        clock.advance(1)
        self.assertEqual(done, [10000])
        self.assertFalse(player.playing)
        self.assertEqual(clock.jobs, {})

    def test_stop_and_replay_cannot_be_advanced_by_old_callbacks(self):
        clock = Scheduler()
        player = PagePlayer(clock)
        shown = []
        callback = lambda i,p,n:shown.append(p)
        player.play(display_pages(['1','2','3','4']), callback, lambda:None)
        stale = next(iter(clock.jobs.values()))[1]
        player.stop()
        player.play(display_pages(['9']), callback, lambda:None)
        stale()
        self.assertEqual(shown, ['01 02 03', '09      '])
        self.assertEqual(len(clock.jobs), 1)

    def test_hardware_inference_can_be_collected_without_immediate_display(self):
        class FakeBoard:
            mode = 'board'
            def __init__(self): self.calls = 0
            def infer(self, pixels):
                self.calls += 1
                digit, scores = studio.offline_ref.infer(pixels)
                return digit, scores, 'simulated board', 0
            def set_display(self, value): raise AssertionError('batch wrote immediate DISPLAY')
        board = FakeBoard()
        frame = test_demo.demo_frame('50')
        with tempfile.TemporaryDirectory() as temp:
            run = sample_log.SampleRun(temp, frame, {'mode':'two'})
            items = vision_pipeline.prepare(frame, 'two', capture_mode='guided')
            run.add_items(items)
            results = inference_pipeline.recognize(board, items, run, ROOT/'resources', update_display=False)
            self.assertEqual([r['digit'] for r in results], [5,0])
            self.assertEqual(board.calls, 2)
            self.assertTrue(all(r['display']['deferred'] and not r['display']['verified'] for r in results))

    def test_offline_manifest_mixes_modes_and_continues_after_error(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            for text in ['5','50','9']:
                mnist_camera_input.write_png(directory/(text+'.png'), test_demo.demo_frame(text))
            manifest = directory/'batch.json'
            manifest.write_text(json.dumps({'captures':[
                {'image':'5.png'}, {'image':'missing.png'},
                {'image':'50.png','mode':'two'}, {'image':'9.png'}]}))
            with patch.object(studio.serial_link, 'open_port', side_effect=AssertionError('UART forbidden')):
                session = batch_cli.run_manifest(manifest, directory/'output', studio.Board(), ROOT/'resources')
            self.assertEqual(session.values, ['5','50','9'])
            self.assertEqual(session.data['pages'], ['05 50 09'])
            self.assertEqual([e['status'] for e in session.data['entries']],
                             ['recognized','error','recognized','recognized'])
            self.assertEqual(session.data['playback_state'], 'not_started')

    def test_manifest_cli_rejects_hardware_or_other_actions(self):
        for options in (['--port','COM5'], ['--initialize'], ['--image','a.png'], ['--selftest']):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                studio.main(['--batch-manifest','batch.json']+options)
            self.assertEqual(error.exception.code, 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
