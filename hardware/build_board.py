"""Build in a fresh ASCII work directory. Runs simulations and Vivado, never JTAG."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

ROOT=Path(__file__).resolve().parent


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work-root',type=Path,required=True)
    p.add_argument('--vivado-bin',type=Path,default=Path('D:/2025.1/Vivado/bin'))
    p.add_argument('--sim-only',action='store_true',help='Run RTL regressions without synthesis or hardware access')
    args=p.parse_args()
    vendor_env=os.environ.copy()
    # Some agent shells omit this standard Windows variable. AMD's loader
    # silently exits before launching any tool when it is missing.
    if 'PROCESSOR_ARCHITECTURE' not in vendor_env:
        import struct
        vendor_env['PROCESSOR_ARCHITECTURE']='AMD64' if struct.calcsize('P')==8 else 'x86'
    stage=args.work_root.resolve()/('ees331-page-'+datetime.now().strftime('%Y%m%d-%H%M%S-')+uuid.uuid4().hex[:8])
    assert str(stage).isascii() and not stage.exists(), stage
    stage.mkdir(parents=True)
    (stage/'temp').mkdir()
    vendor_env['TEMP']=str(stage/'temp')
    vendor_env['TMP']=str(stage/'temp')
    source=stage/'source'
    shutil.copytree(ROOT/'pl',source)
    shutil.copytree(ROOT.parent/'tests',stage/'tests',ignore=shutil.ignore_patterns('__pycache__'))
    report=dict(status='RUNNING',hardware_accessed=False,work_directory=str(stage),steps=[])
    report_path=ROOT.parent/'integration/board-build.json'
    def save(): report_path.write_text(json.dumps(report,indent=2),encoding='utf-8')
    save()
    print('BOARD_BUILD_DIRECTORY',stage,flush=True)
    def run(name,tool,arguments,cwd,marker=None,timeout=2400):
        command=[str(args.vivado_bin/(tool+'.bat')),*map(str,arguments)]
        log=stage/(name+'.log')
        print('START',name,flush=True)
        with log.open('wb') as output:
            result=subprocess.run('cmd.exe /d /s /c "'+subprocess.list2cmdline(command)+'"',
                                  cwd=cwd,env=vendor_env,stdout=output,stderr=subprocess.STDOUT,timeout=timeout)
        text=log.read_text(encoding='utf-8',errors='replace')
        ok=result.returncode==0 and (marker is None or marker in text)
        report['steps'].append(dict(name=name,exit_code=result.returncode,passed=ok,log=log.name))
        save()
        if not ok: raise RuntimeError(name+' failed; see '+str(log))
        print('PASS',name,flush=True)
    try:
        for top,rtl in [
            ('conv_pipeline_tb',['rtl/accelerator/buffers.v','rtl/accelerator/mac_array.sv',
                'rtl/accelerator/conv_engine.sv',str(stage/'tests/conv_pipeline_tb.sv')]),
            ('conv_bias_tb',['rtl/accelerator/buffers.v','rtl/accelerator/mac_array.sv',
                'rtl/accelerator/conv_engine.sv','rtl/accelerator/conv_bias_tb.sv']),
            ('sevenseg_page_tb',['rtl/display/sevenseg_result.v',str(stage/'tests/sevenseg_page_tb.sv')]),
            ('mnist_soc_top_tb',['rtl/accelerator/buffers.v','rtl/accelerator/mac_array.sv',
                'rtl/accelerator/controller.v','rtl/accelerator/conv_engine.sv','rtl/accelerator/fc_engine.sv',
                'rtl/accelerator/pool_engine.sv','rtl/accelerator/cnn_accel_top.sv',
                'rtl/soc/axi_if_v2.v','rtl/soc/mnist_soc_top.sv','rtl/soc/mnist_soc_top_tb.sv'])]:
            cwd=stage/top; cwd.mkdir()
            shutil.copytree(source/'ref_model',cwd/'ref_model')
            paths=[source/f if not Path(f).is_absolute() else Path(f) for f in rtl]
            run(top+'-compile','xvlog',['--sv',*paths],cwd)
            run(top+'-elaborate','xelab',[top,'-s','snapshot'],cwd)
            marker={'conv_pipeline_tb':'PASS: conv_pipeline scalar oracle',
                    'conv_bias_tb':'[PASS] conv_bias: all tests passed',
                    'sevenseg_page_tb':'PASS: eight-cell scan',
                    'mnist_soc_top_tb':'[PASS] mnist_soc_top: 20/20 bit-exact'}[top]
            simargs=['snapshot','-runall']
            if top=='mnist_soc_top_tb':
                simargs.extend(['-testplusarg','RESET_STRESS','-testplusarg','EXTRA_INPUTS'])
            run(top+'-simulate','xsim',simargs,cwd,marker)
            if top=='mnist_soc_top_tb':
                text=(stage/(top+'-simulate.log')).read_text(encoding='utf-8',errors='replace')
                if '[PASS] mnist_soc_top: 5/5 extra inputs, 50/50 exact logits' not in text:
                    raise RuntimeError('Extra full-network RTL checks did not complete')
        if args.sim_only:
            report['status']='SIMULATION_PASS_NOT_BOARD_TESTED';save()
            evidence=ROOT.parent/'integration/rtl-sim-evidence';evidence.mkdir(exist_ok=True)
            for log in stage.glob('*.log'): shutil.copy2(log,evidence/log.name)
            print('RTL_SIMULATION_PASS_NOT_BOARD_TESTED',flush=True)
            return
        run('bitstream','vivado',['-mode','batch','-notrace','-source',source/'board/vivado/build.tcl',
            '-tclargs','bitstream',stage/'board-output',source/'board/vivado/configs/ees331-rev1-v1-seg.tcl'],
            stage,'BOARD_BUILD_PASS: bitstream',5400)
        output=stage/'board-output'
        report['outputs']={name:hashlib.sha256((output/name).read_bytes()).hexdigest()
                           for name in ('system.bit','system.xsa')}
        # The exported XSA contains the PS init that actually matches this build.
        import zipfile
        with zipfile.ZipFile(output/'system.xsa') as z:
            names=[n for n in z.namelist() if n.endswith('ps7_init.tcl')]
            assert len(names)==1,names
            (output/'ps7_init.tcl').write_bytes(z.read(names[0]))
        report['status']='BUILD_PASS_NOT_BOARD_TESTED'
        save()
        evidence=ROOT.parent/'integration/board-evidence'
        evidence.mkdir(exist_ok=True)
        for log in stage.glob('*.log'): shutil.copy2(log,evidence/log.name)
        for f in output.iterdir():
            if f.is_file() and f.suffix in ('.rpt','.txt','.tsv','.tcl'):
                shutil.copy2(f,evidence/f.name)
        for name in ('system.bit','system.xsa','ps7_init.tcl'):
            target=ROOT/'build/pl'; target.mkdir(parents=True,exist_ok=True)
            shutil.copy2(output/name,target/name)
        print('BOARD_BUILD_PASS_NOT_BOARD_TESTED',flush=True)
    except Exception as exc:
        report.update(status='FAILED',error=str(exc)); save(); raise


if __name__=='__main__': main()
