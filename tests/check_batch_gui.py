"""Run the real Tk/worker/capture workflow with saved and simulated inputs."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--tk-root', type=Path, required=True)
args = parser.parse_args()
sys.path[:0] = [str(args.tk_root/'Lib'),str(args.tk_root/'DLLs'),str(ROOT/'source'),str(ROOT/'tests')]
os.environ['TCL_LIBRARY'] = str(args.tk_root/'tcl/tcl8.6')
os.environ['TK_LIBRARY'] = str(args.tk_root/'tcl/tk8.6')
import tkinter as tk
from tkinter import filedialog
import numpy as np
import ees331_studio as studio
import mnist_camera_input
import test_demo


def forbidden(*args, **kwargs):
    raise AssertionError('real camera, UART and immediate DISPLAY forbidden')


with tempfile.TemporaryDirectory() as temporary, \
        patch.object(studio, 'APP_ROOT', Path(temporary)), \
        patch.object(studio.serial_link, 'list_ports', return_value=[]), \
        patch.object(studio.board_init, 'detect_port', return_value=None), \
        patch.object(studio.serial_link, 'open_port', side_effect=forbidden), \
        patch.object(studio.cv2, 'VideoCapture', side_effect=forbidden), \
        patch.object(studio.Board, 'set_display', side_effect=forbidden):
    root = tk.Tk()
    root.withdraw()
    app = studio.Studio(root, SimpleNamespace(camera_eye='auto',port=None,offline=True,capture_mode='guided'))

    def wait_for(predicate, timeout=6):
        deadline = time.monotonic()+timeout
        while time.monotonic()<deadline:
            root.update()
            if predicate():
                return
            time.sleep(.01)
        raise AssertionError('GUI workflow timed out; '+str(app.lbl_status.cget('text')))

    app.start_batch()
    for text in ['1','50','8','blank','0','9']:
        app.var_mode.set('two' if len(text)==2 else 'single')
        app.frame = np.full((800,1280),255,np.uint8) if text=='blank' else test_demo.demo_frame(text)
        app.source_info = {'kind':'file','filename':text+'.png'}
        app._input_changed()
        app.run_recognition()
        wait_for(lambda:not app.busy)
    assert app.batch.values==['1','50','8','0','9']
    assert '超过一页' in app.lbl_batch_state.cget('text')
    assert app.txt_batch.get('1.0','end').strip()=='01 50 08 00 09'
    assert sum(e['status']=='error' for e in app.batch.data['entries'])==1
    app.finish_batch()
    assert app.batch.data['pages']==['01 50 08','00 09   ']
    assert '1/2' in app.lbl_page.cget('text')
    for cell in (2,5):
        assert all(app.cv_display.itemcget(item,'fill')=='#29313f'
                   for item in app.cv_display.find_withtag('cell_%d'%cell))
    started = time.monotonic()
    wait_for(lambda:'2/2' in app.lbl_page.cget('text'))
    page_elapsed = time.monotonic()-started
    assert 4.9 <= page_elapsed < 6.5
    app.stop_batch_pages()
    assert not app.page_player.playing and app.page_player.job is None
    app.replay_batch()
    assert '1/2' in app.lbl_page.cget('text')
    app.clear_batch()
    assert app.batch.values==[] and app.page_player.job is None

    # Multi-file import, sorting, and Finish while a worker/queue is still active.
    images = Path(temporary)/'images'
    images.mkdir()
    for filename, text in [('b_5.png','5'),('a_9.png','9')]:
        mnist_camera_input.write_png(images/filename, test_demo.demo_frame(text))
    app.start_batch()
    app.var_mode.set('single')
    with patch.object(filedialog,'askopenfilenames',return_value=[str(images/'b_5.png'),str(images/'a_9.png')]):
        app.open_file()
    assert app.busy
    app.finish_batch()
    assert app.batch_finish_requested
    wait_for(lambda:not app.busy and not app.batch.collecting)
    assert app.batch.values==['9','5'] and app.batch.data['pages']==['09 05   ']
    for entry in app.batch.data['entries']:
        run = json.loads((app.batch.path/entry['run']/'run.json').read_text(encoding='utf-8'))
        assert run['context']['display_deferred']
        assert run['results'][0]['display']['deferred']
    app.board.mode='board'
    with patch.object(app.board,'set_display_page',side_effect=RuntimeError('当前固件不支持八位分页')):
        app.replay_batch()
        wait_for(lambda:'分页失败' in app.lbl_status.cget('text'))
    assert all(not e['hardware_verified'] for e in app.batch.data['display_events'])
    sent=[]
    with patch.object(app.board,'set_display_page',side_effect=lambda text,*a:sent.append(text) or text):
        app.replay_batch()
        wait_for(lambda:'板卡已回读' in app.lbl_page.cget('text'))
        assert sent==['09 05   ']
        assert app.batch.data['display_events'][-1]['ack_verified']
        app.stop_batch_pages()
        app.clear_batch()
        wait_for(lambda:'全灭页ACK' in app.lbl_sync.cget('text'))
        assert sent==['09 05   ','        ']
    app.board.mode='offline'
    app.clear_batch()

    # Automatic capture reads a fresh simulated camera frame, keeps preview live,
    # and Finish cancels the next capture without waiting for the interval.
    current = test_demo.demo_frame('5')
    def open_camera(index):
        app.cam.cap = object()
        app.cam.index = 0
        return 0,50,current.copy()
    def read_camera():
        app.cam.frame_id += 1
        app.cam.captured_at = time.monotonic()
        app.cam.raw_last = current.copy()
        return current.copy()
    def close_camera(): app.cam.cap = None
    with patch.object(app.cam,'open',side_effect=open_camera), \
            patch.object(app.cam,'read',side_effect=read_camera), \
            patch.object(app.cam,'close',side_effect=close_camera):
        app.var_auto_capture.set(True)
        app.var_capture_seconds.set('2')
        app.start_batch()
        app.toggle_preview()
        wait_for(lambda:len(app.batch.values)==1 and not app.busy)
        assert app.preview_job is not None
        assert app.batch.values==['5']
        app.finish_batch()
        assert app.auto_job is None and not app.batch.collecting
        assert app.batch.data['pages']==['05      ']
        app.stop_preview()
    app.stop_batch_pages()
    root.update_idletasks()
    report = dict(hardware_accessed=False,camera_opened=False,native_tk_ui_created=True,
        screenshot_checked=False,mixed_single_two_values=['1','50','8','0','9'],
        blank_failure_kept=True,overflow_continues=True,complete_text_preserved=True,
        first_page='01 50 08',second_page='00 09   ',real_page_interval_seconds=page_elapsed,
        blank_cells_have_zero_lit_segments=True,replay_stop_clear_verified=True,
        multi_file_import_sorted=True,finish_waits_for_worker_and_import_queue=True,
        deferred_immediate_display=True,old_firmware_not_claimed_as_eight_cell_capable=True,
        automatic_capture_uses_fresh_simulated_frames=True,automatic_capture_keeps_preview=True,
        finish_cancels_automatic_capture=True)
    report['requested_window_size'] = [root.winfo_reqwidth(), root.winfo_reqheight()]
    root.destroy()
(ROOT/'integration/batch-gui.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
print('PASS: real Tk batch workflows and real 5-second page change; no hardware')
