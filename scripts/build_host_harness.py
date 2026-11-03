"""Compile real CPU/firmware C with fake MMIO/time; do not regenerate data."""
import argparse
import os
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parent.parent
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--llvm-bin',type=Path,default=Path(os.environ.get('EES331_LLVM_BIN','C:/Program Files/LLVM/bin')))
    args=p.parse_args()
    out=ROOT/'hardware/build';out.mkdir(parents=True,exist_ok=True)
    clang=args.llvm_bin/'clang.exe'
    if not clang.is_file():raise RuntimeError('Install LLVM or supply --llvm-bin / EES331_LLVM_BIN')
    command=[str(clang),'-O2','-ffreestanding','-fno-builtin','-fno-stack-protector',
        '-DEES331_HOST_TEST=1','-shared','-nostdlib','-fuse-ld=lld','-Wl,/noentry','-Wl,/nodefaultlib',
        str(ROOT/'tests/firmware_emulator.c'),*[str(ROOT/('hardware/arm/'+f+'.c')) for f in
        ('cpu_infer','benchmark','benchmark_data','runtime')],'-o',str(out/'firmware_emulator.dll')]
    subprocess.run(command,check=True)
    print('HOST_HARNESS_PASS: actual firmware C, fake MMIO/time, no hardware')
if __name__=='__main__':main()
