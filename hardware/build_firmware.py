"""Build the no-BSP Cortex-A9 firmware with LLVM; no hardware access."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--llvm-bin', type=Path, default=Path('C:/Program Files/LLVM/bin'))
    p.add_argument('--output', type=Path, default=ROOT/'build/arm')
    args = p.parse_args()
    subprocess.run([sys.executable,'-B','-X','utf8',str(ROOT/'generate_benchmark.py'),
                    '--compiler',str(args.llvm_bin/'clang.exe')],check=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    clang = args.llvm_bin/'clang.exe'
    linker = args.llvm_bin/'ld.lld.exe'
    flags = ['--target=armv7a-none-eabi','-mcpu=cortex-a9','-marm','-mfloat-abi=soft',
             '-mfpu=none','-mno-unaligned-access','-ffreestanding','-fno-builtin',
             '-fno-stack-protector','-fno-unwind-tables','-fno-asynchronous-unwind-tables']
    commands = []
    with (output/'build.log').open('w',encoding='utf-8') as log:
        def run(command):
            command = [str(x) for x in command]
            commands.append(command)
            result = subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=120)
            log.write(json.dumps(command)+'\n'+result.stdout.decode('utf-8',errors='replace')+'\n')
            log.flush()
            if result.returncode: raise RuntimeError('Build failed; see '+str(output/'build.log'))
        run([clang,*flags,'-c',ROOT/'arm/start.S','-o',output/'start.o'])
        objects=[output/'start.o']
        for name in ('fw_seg','cpu_infer','bench_platform','benchmark','benchmark_data','runtime'):
            obj=output/(name+'.o'); objects.append(obj)
            run([clang,*flags,'-std=c11','-O2','-Wall','-Wextra','-Werror',
                 '-c',ROOT/('arm/'+name+'.c'),'-o',obj])
        run([linker,'-T',ROOT/'arm/link.ld','-z','max-page-size=4096','--build-id=none',
             '-Map='+str(output/'app.map'),*objects,'-o',output/'app.elf'])
    elf = (output/'app.elf').read_bytes()
    h = struct.unpack_from('<16sHHIIIIIHHHHHH',elf)
    assert h[1:3] == (2,40) and 0x100000 <= h[4] < 0x200000
    loads=[]
    for i in range(h[10]):
        ph=struct.unpack_from('<8I',elf,h[5]+h[9]*i)
        if ph[0]==1:
            assert 0x100000 <= ph[3] <= ph[2] < 0x200000 and ph[3]+ph[5]<=0x200000
            assert ph[4]<=ph[5] and ph[1]+ph[4]<=len(elf)
            loads.append(list(ph))
    assert loads
    report=dict(status='BUILD_PASS_NOT_BOARD_TESTED',hardware_accessed=False,
        elf_sha256=hashlib.sha256(elf).hexdigest(),loads=loads,commands=commands,
        inputs={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in (ROOT/'arm').iterdir() if f.is_file()})
    (output/'build.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('ARM_BUILD_PASS',report['elf_sha256'])


if __name__=='__main__': main()
