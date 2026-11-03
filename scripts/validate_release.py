"""Run existing meaningful regressions after folder relocation; no devices."""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parent.parent
TESTS=('test_v42.py','test_v5.py','test_native_runtime.py','test_demo.py','test_preview.py',
       'test_camera_center.py','test_batch.py','test_display_transport.py','test_arm_benchmark.py')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,default=ROOT/'validation')
    p.add_argument('--runtime',type=Path,help='optional companion folder with the original EXE and _internal')
    args=p.parse_args();out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    results=[]
    def run(name,argv,timeout=300):
        print('START',name,flush=True)
        result=subprocess.run([str(x) for x in argv],cwd=ROOT,stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,timeout=timeout,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        data=result.stdout.decode('utf-8',errors='replace')
        (out/(name+'.log')).write_text(data,encoding='utf-8')
        results.append({'name':name,'exit_code':result.returncode,'log':name+'.log'})
        if result.returncode:raise RuntimeError(name+' failed; inspect '+str(out/(name+'.log')))
        print('PASS',name,flush=True)
    if os.name!='nt':raise RuntimeError('Windows x64 required for Win32 serial/native runtime regressions')
    if not (ROOT/'hardware/build/firmware_emulator.dll').is_file():
        run('build_host_harness',[sys.executable,'-B','-X','utf8',ROOT/'scripts/build_host_harness.py'])
    runtime=args.runtime.resolve() if args.runtime else ROOT
    binary=(runtime/'ees331_studio.exe').is_file() and (runtime/'_internal').is_dir()
    if args.runtime and not binary:raise RuntimeError('The supplied runtime folder is incomplete')
    if binary:
        run('original_full_delivery_acceptance',[sys.executable,'-B','-X','utf8',ROOT/'tests/validate_delivery.py','--output',out,'--runtime',runtime],600)
    else:
        for name in TESTS:run(name,[sys.executable,'-B','-X','utf8',ROOT/'tests'/name])
        run('source_selftest',[sys.executable,'-B','-X','utf8',ROOT/'source/ees331_studio.py','--selftest'])
        run('source_initializer_hashes',[sys.executable,'-B','-X','utf8',ROOT/'source/ees331_studio.py','--check-initializer'])
    summary={'date_utc':datetime.now(timezone.utc).isoformat(),'passed':True,
        'binary_validation':binary,'binary_validation_skipped_reason':None if binary else 'Git source clone: optional EXE/runtime not included',
        'source_regression_scripts':list(TESTS),'results':results,
        'hardware_accessed':False,'camera_opened':False,'jtag_accessed':False,
        'not_a_new_board_measurement':True}
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('RELEASE_VALIDATION_PASS',out/'summary.json',flush=True)
if __name__=='__main__':main()
