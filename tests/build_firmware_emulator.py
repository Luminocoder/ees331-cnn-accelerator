"""Build actual firmware/CPU C for host tests; timing/MMIO are explicitly fake."""
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parent.parent
subprocess.run([sys.executable,'-B','-X','utf8',str(ROOT/'hardware/generate_benchmark.py')],check=True)
out=ROOT/'hardware/build'; out.mkdir(parents=True,exist_ok=True)
command=['C:/Program Files/LLVM/bin/clang.exe','-O2','-ffreestanding','-fno-builtin','-fno-stack-protector',
    '-DEES331_HOST_TEST=1','-shared','-nostdlib','-fuse-ld=lld','-Wl,/noentry','-Wl,/nodefaultlib',
    str(ROOT/'tests/firmware_emulator.c'),*[str(ROOT/('hardware/arm/'+f+'.c')) for f in
    ('cpu_infer','benchmark','benchmark_data','runtime')],'-o',str(out/'firmware_emulator.dll')]
subprocess.run(command,check=True)
print('HOST_FIRMWARE_BUILD_PASS: fake clocks and MMIO, no hardware')
