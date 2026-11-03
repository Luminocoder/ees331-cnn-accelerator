"""Install a matching successful PL build; no JTAG or serial access."""
from pathlib import Path
import hashlib
import json
import shutil

ROOT=Path(__file__).resolve().parent.parent
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def read(path):return json.loads(path.read_text(encoding='utf-8'))
def save(path,data):path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def main():
    build=read(ROOT/'integration/board-build.json')
    if build['status']!='BUILD_PASS_NOT_BOARD_TESTED' or not all(s['passed'] for s in build['steps']):
        raise RuntimeError('A successful complete PL build is required')
    stage=Path(build['work_directory'])
    files={p.relative_to(ROOT/'hardware/pl').as_posix():sha(p)
           for p in (ROOT/'hardware/pl').rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    excluded={'dfx_runtime.txt':'Generated Vivado runtime profile, not a design input'}
    for name,digest in files.items():
        if name not in excluded and sha(stage/'source'/name)!=digest:
            raise RuntimeError('Source changed after build: '+name)
    for name in ('tests/conv_pipeline_tb.sv','tests/sevenseg_page_tb.sv'):
        if sha(ROOT/name)!=sha(stage/name):raise RuntimeError('RTL test changed after build: '+name)
    firmware=read(ROOT/'integration/firmware-build.json')
    if sha(ROOT/'resources/flash_payload/app.elf')!=firmware['elf_sha256']:
        raise RuntimeError('ARM ELF does not match firmware evidence')
    if sha(ROOT/'hardware/build/pl/system.bit')!=build['outputs']['system.bit']:
        raise RuntimeError('Bitstream differs from build result')
    for name in ('system.bit','ps7_init.tcl'):
        shutil.copy2(ROOT/'hardware/build/pl'/name,ROOT/'resources/flash_payload'/name)
    manifest=read(ROOT/'resources/deployment-manifest.json')
    manifest['source']='v5.6 cached 9-tap pipelined convolution + ghost suppression; board acceptance pending'
    for name in manifest['files']:
        p=ROOT/'resources'/name
        manifest['files'][name]={'bytes':p.stat().st_size,'sha256':sha(p)}
    save(ROOT/'resources/deployment-manifest.json',manifest)
    save(ROOT/'integration/pl-implementation-hashes.json',{
        'source_matches_implemented_mirror':True,'files':files,
        'generated_metadata_excluded_from_mirror_comparison':excluded,
        'test_inputs':{n:sha(ROOT/n) for n in ('tests/conv_pipeline_tb.sv','tests/sevenseg_page_tb.sv')},
        'generated_by':'hardware/build_board.py; Vivado 2025.1'})
    print('PL_PAYLOAD_INSTALLED: build/source hashes match; rebuild EXE before delivery; hardware untested')

if __name__=='__main__':main()
