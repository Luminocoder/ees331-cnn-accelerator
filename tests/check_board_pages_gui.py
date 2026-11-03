"""Actual Tk -> worker -> serial codec -> actual C -> mocked PL, never hardware."""
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
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--tk-root',type=Path,required=True)
args=p.parse_args()
sys.path[:0]=[str(args.tk_root/'Lib'),str(args.tk_root/'DLLs'),str(ROOT/'source'),str(ROOT/'tests')]
os.environ['TCL_LIBRARY']=os.path.relpath(args.tk_root/'tcl/tcl8.6')
os.environ['TK_LIBRARY']=os.path.relpath(args.tk_root/'tcl/tk8.6')
import tkinter as tk
import ees331_studio as studio
from test_display_transport import FirmwareStream


def forbidden(*a,**kw): raise AssertionError('Real devices forbidden')


with tempfile.TemporaryDirectory() as temporary, \
        patch.object(studio,'APP_ROOT',Path(temporary)), \
        patch.object(studio.serial_link,'list_ports',return_value=[]), \
        patch.object(studio.board_init,'detect_port',return_value=None), \
        patch.object(studio.serial_link,'open_port',side_effect=forbidden), \
        patch.object(studio.cv2,'VideoCapture',side_effect=forbidden):
    root=tk.Tk(); root.withdraw()
    app=studio.Studio(root,SimpleNamespace(camera_eye='auto',port=None,offline=True,capture_mode='guided'))
    def wait_for(predicate,timeout=7):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            root.update()
            if predicate(): return
            time.sleep(.01)
        raise AssertionError('Timed out: '+app.lbl_status.cget('text'))
    app.start_batch()
    for value in ['1','2','3','0','9']:
        ticket=app.batch.reserve('single')
        app.batch.success(ticket,value,Path(temporary)/'saved-run')
    stream=FirmwareStream()
    app.board.stream=stream; app.board.mode='board'
    app.finish_batch()
    wait_for(lambda:'板卡已回读  第1/2页' in app.lbl_page.cget('text'))
    assert stream.inspect(0)==0x01f02f03 and stream.inspect(1)==6
    first=time.monotonic()
    wait_for(lambda:'板卡已回读  第2/2页' in app.lbl_page.cget('text'))
    interval=time.monotonic()-first
    assert 4.9 <= interval < 6.5,interval
    assert stream.inspect(0)==0x00f09fff
    app.stop_batch_pages(); count=len(stream.requests)
    root.update()
    assert len(stream.requests)==count and not app.board_page_player.playing
    events=list(app.batch.data['display_events'])
    assert len([e for e in events if e['ack_verified']])==2
    assert all(not e['hardware_verified'] for e in events)
    app.clear_batch()
    wait_for(lambda:'全灭页ACK' in app.lbl_sync.cget('text'))
    assert stream.inspect(0)==0xffffffff
    app.board.close()
    assert stream.inspect(5)==1 # legacy per-image automatic display restored on disconnect
    report=dict(hardware_accessed=False,camera_opened=False,native_tk_ui_created=True,
        real_firmware_c_used=True,pl_mmio_emulated=True,page_interval_seconds=interval,
        packed_pages=['01f02f03','00f09fff'],clear_page='ffffffff',ack_verified=True,
        stop_verified=True,legacy_auto_display_restored_on_disconnect=True,
        physical_digit_order_verified=False)
    root.destroy()
(ROOT/'integration/board-pages-gui.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report,indent=2))
print('PASS: real GUI and firmware C page playback; no hardware')
