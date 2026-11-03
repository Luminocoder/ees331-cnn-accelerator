"""Real Tk button/queue/worker with actual C, emulated MMIO/ticks; no devices."""
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
p=argparse.ArgumentParser(); p.add_argument('--tk-root',type=Path,required=True); args=p.parse_args()
sys.path[:0]=[str(args.tk_root/'Lib'),str(args.tk_root/'DLLs'),str(ROOT/'source'),str(ROOT/'tests')]
os.environ['TCL_LIBRARY']=os.path.relpath(args.tk_root/'tcl/tcl8.6')
os.environ['TK_LIBRARY']=os.path.relpath(args.tk_root/'tcl/tk8.6')
import tkinter as tk
import ees331_studio as studio
import arm_benchmark
from test_display_transport import FirmwareStream

def forbidden(*a,**kw): raise AssertionError('real camera/UART/JTAG forbidden')
real_run=arm_benchmark.run_on_stream
def emulated_run(*a,**kw):
    kw['evidence_kind']='firmware_host_emulator'
    return real_run(*a,**kw)
with tempfile.TemporaryDirectory() as temp,patch.object(studio,'APP_ROOT',Path(temp)), \
        patch.object(studio.serial_link,'list_ports',return_value=[]), \
        patch.object(studio.board_init,'detect_port',return_value=None), \
        patch.object(studio.serial_link,'open_port',side_effect=forbidden), \
        patch.object(studio.cv2,'VideoCapture',side_effect=forbidden), \
        patch.object(arm_benchmark,'run_on_stream',side_effect=emulated_run):
    root=tk.Tk(); root.withdraw()
    app=studio.Studio(root,SimpleNamespace(camera_eye='auto',port=None,offline=False,capture_mode='guided'))
    app.do_benchmark(); assert not app.busy
    app.board.mode='board'; app.board.stream=FirmwareStream()
    app.start_batch(); app.do_benchmark(); assert not app.busy and app.batch.collecting
    app.batch.cancel()
    app.do_benchmark(); assert app.busy
    deadline=time.monotonic()+12
    while app.busy and time.monotonic()<deadline: root.update(); time.sleep(.005)
    assert not app.busy
    files=list((Path(temp)/'logs/benchmark').glob('*/results.json')); assert len(files)==1
    report=json.loads(files[0].read_text(encoding='utf-8'))
    assert report['status']=='EMULATED_FLOW_PASS_NOT_A_MEASUREMENT',report.get('error')
    assert report['speedup'] is None and not report['valid_for_hardware_report']
    assert len(report['rows'])==130 and report['restored']['active']==0
    assert '未生成有效加速比' in app.lbl_status.cget('text')
    app.do_benchmark(); app.stop_benchmark()
    deadline=time.monotonic()+5
    while app.busy and time.monotonic()<deadline: root.update(); time.sleep(.005)
    assert not app.busy
    files=list((Path(temp)/'logs/benchmark').glob('*/results.json')); assert len(files)==2
    assert any(json.loads(f.read_text(encoding='utf-8'))['status']=='CANCELLED' for f in files)
    app.board.close(); root.destroy()
    result=dict(native_tk_created=True,actual_cpu_c=True,mmio_and_ticks_emulated=True,
                full_130_pair_flow_passed=True,cancel_and_restore_passed=True,
                camera_opened=False,hardware_accessed=False,hardware_speedup=None)
    (ROOT/'integration/benchmark-gui.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print('PASS: actual Tk benchmark button/worker/cancel/restore; emulator only, no hardware speedup')
