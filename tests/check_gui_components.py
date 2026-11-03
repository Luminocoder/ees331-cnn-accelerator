"""Create the actual Tk UI and exercise its display components without hardware."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parent.parent
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--tk-root',type=Path)
args=parser.parse_args()
if args.tk_root:
    sys.path[:0]=[str(args.tk_root/'Lib'),str(args.tk_root/'DLLs')]
    os.environ['TCL_LIBRARY']=str(args.tk_root/'tcl/tcl8.6')
    os.environ['TK_LIBRARY']=str(args.tk_root/'tcl/tk8.6')
sys.path[:0]=[str(ROOT/'source'),str(ROOT/'tests')]
import tkinter as tk
import ees331_studio as studio
import inference_pipeline
import sample_log
import test_demo
import vision_pipeline
import cv2
import numpy as np

def forbidden(*a,**kw):
    raise AssertionError('Hardware access forbidden in GUI component check')

with patch.object(studio.serial_link,'list_ports',return_value=[]), \
     patch.object(studio.board_init,'detect_port',return_value=None), \
     patch.object(studio.serial_link,'open_port',side_effect=forbidden), \
     patch.object(studio.cv2,'VideoCapture',side_effect=forbidden):
    root=tk.Tk()
    root.withdraw()
    options=SimpleNamespace(camera_eye='auto',port=None,offline=True,capture_mode='guided',strategy='single',no_compare=True)
    app=studio.Studio(root,options)
    assert app.var_guided.get()
    records=[]
    with tempfile.TemporaryDirectory() as temp:
        for text in ['5','50']:
            mode='single' if len(text)==1 else 'two'
            app.var_mode.set(mode)
            app.frame=test_demo.demo_frame(text)
            app._input_changed()
            items=vision_pipeline.prepare(app.frame,mode=mode,capture_mode='guided')
            run=sample_log.SampleRun(temp,app.frame,{'mode':mode})
            run.add_items(items)
            results=inference_pipeline.recognize(studio.Board(),items,run,ROOT/'resources')
            if len(results)==1:
                result=results[0]
                app._show_one(result['canvas'],result['meta'],result['digit'],result['display_scores'],
                              result['where'],result['ms'],app.frame)
            else:
                app._show_two(results,app.frame)
            app._show_extra(results)
            app.detected_boxes=[r['meta']['bbox'] for r in results]
            app._paint_cam()
            root.update_idletasks()
            assert app.lbl_digit.cget('text')==text
            assert 'MLP' not in app.lbl_compare.cget('text')
            assert '离线' in app.lbl_sync.cget('text')
            records.append(dict(expected=text,label=app.lbl_digit.cget('text'),
                canvas_objects=len(app.cv_cam.find_all()),display=app.lbl_sync.cget('text')))
    # Exercise the real preview callback / Tk event loop with simulated frames.
    # Board, serial, logging and camera-device access remain forbidden.
    current = [test_demo.demo_frame('5')]
    def open_camera(index):
        app.cam.cap = object()
        return 0, 50.0, current[0].copy()
    def close_camera():
        app.cam.cap = None
    def wait_for(predicate):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            root.update()
            if predicate():
                return
            time.sleep(.01)
        raise AssertionError('GUI live box did not update')
    app.var_mode.set('single')
    with patch.object(app.cam, 'open', side_effect=open_camera), \
            patch.object(app.cam, 'read', side_effect=lambda: current[0].copy() if current[0] is not None else None), \
            patch.object(app.cam, 'close', side_effect=close_camera), \
            patch.object(app.board, 'infer', side_effect=forbidden), \
            patch.object(app.board, 'set_display', side_effect=forbidden), \
            patch.object(studio.sample_log, 'SampleRun', side_effect=forbidden):
        app.toggle_preview()
        app.busy = True  # Live results must never release an unrelated operation.
        wait_for(lambda: len(app.detected_boxes)==1)
        first = list(app.detected_boxes[0])
        cyan = [i for i in app.cv_cam.find_all() if app.cv_cam.type(i)=='rectangle'
                and app.cv_cam.itemcget(i, 'outline')==studio.CYAN]
        assert len(cyan)==1
        current[0] = cv2.warpAffine(current[0], np.float32([[1,0,40],[0,1,0]]),
                                    (1280,800), borderValue=255)
        wait_for(lambda: app.detected_boxes and abs(app.detected_boxes[0][0]-first[0]-40)<=1)
        assert app.busy
        current[0] = test_demo.demo_frame('50')
        app.var_mode.set('two')
        app._input_changed()
        assert app.detected_boxes==[]
        wait_for(lambda: len(app.detected_boxes)==2)
        current[0] = np.full_like(current[0],255)
        wait_for(lambda: app.detected_boxes==[])
        current[0] = None
        wait_for(lambda: app.preview_job is None)
        assert app.frame is None and not app.cv_cam.find_all()
        assert app.busy
        app.busy = False
    live = dict(start_preview_draws_cyan=True,moving_digit_updates_box=True,
                mode_change_invalidates=True,two_live_boxes=True,blank_clears_boxes=True,
                camera_failure_stops_and_clears=True,does_not_release_busy=True,
                no_inference_or_display_or_sample_logs=True,simulated_camera_frames=True)
    report=dict(hardware_accessed=False,camera_opened=False,visual_screenshot_checked=False,
        native_tk_ui_created=True,default_guided=True,tk_patchlevel=root.tk.call('info','patchlevel'),cases=records,
        live_preview=live)
    root.destroy()
(ROOT/'integration/gui-components.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
print('PASS: actual Tk components, single/two guide drawing and result rendering; no screenshot or hardware')
