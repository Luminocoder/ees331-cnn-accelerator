"""Verify the final source bundle and optional original Windows runtime.

Default is read-only. --write-manifest explicitly seals a new working copy;
it never rewrites original board/build evidence. Windows x64 is required.
"""
from pathlib import Path
import argparse
import hashlib
import json

ROOT=Path(__file__).resolve().parent.parent
IGNORED={'.git','validation','logs','build','_build_v5','local_evidence'}
MANIFEST='release_manifest.json'

def digest(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def inventory():
    core={};optional={}
    for p in sorted(ROOT.rglob('*')):
        if not p.is_file():continue
        rel=p.relative_to(ROOT); parts=rel.parts; name=rel.as_posix()
        if parts[0] in IGNORED or parts[:2]==('hardware','build') or '__pycache__' in parts or p.suffix in ('.pyc','.pyo') or name==MANIFEST:continue
        item={'bytes':p.stat().st_size,'sha256':digest(p)}
        target=optional if parts[0]=='_internal' or name.lower()=='ees331_studio.exe' or parts[:2]==('hardware','build') else core
        target[name]=item
    return core,optional

def check(path, sha):
    if not path.is_file() or digest(path)!=sha:raise RuntimeError('Hash mismatch: '+str(path.relative_to(ROOT)))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--write-manifest',action='store_true')
    p.add_argument('--runtime',type=Path,help='verify the optional separate Windows EXE/runtime folder')
    args=p.parse_args()
    build=json.loads((ROOT/'build-manifest.json').read_text(encoding='utf-8'))
    assert {x.name for x in (ROOT/'source').glob('*.py')}==set(build['source_sha256'])
    for name,sha in build['source_sha256'].items():check(ROOT/'source'/name,sha)
    resource_files={p.relative_to(ROOT/'resources').as_posix().lower() for p in (ROOT/'resources').rglob('*') if p.is_file()}
    assert resource_files=={n.lower() for n in build['resource_sha256']},'Resource set differs from frozen EXE build'
    for name,sha in build['resource_sha256'].items():check(ROOT/'resources'/name,sha)
    runtime=(ROOT/'ees331_studio.exe').is_file()
    if runtime:
        check(ROOT/'ees331_studio.exe',build['exe_sha256'])
        for name,sha in build['resource_sha256'].items():check(ROOT/'_internal/resources'/name,sha)
    firmware=json.loads((ROOT/'integration/firmware-build.json').read_text(encoding='utf-8'))
    for name,sha in firmware['inputs'].items():check(ROOT/'hardware/arm'/name,sha)
    check(ROOT/'resources/flash_payload/app.elf',firmware['elf_sha256'])
    impl=json.loads((ROOT/'integration/pl-implementation-hashes.json').read_text(encoding='utf-8'))
    for name,sha in impl['files'].items():check(ROOT/'hardware/pl'/name,sha)
    for name,sha in impl['test_inputs'].items():check(ROOT/name,sha)
    board=json.loads((ROOT/'integration/board-build.json').read_text(encoding='utf-8'))
    check(ROOT/'resources/flash_payload/system.bit',board['outputs']['system.bit'])
    bench=json.loads((ROOT/'metrics/benchmark-v56/results.json').read_text(encoding='utf-8'))
    assert bench['valid_for_hardware_report'] and bench['status']=='BOARD_BENCHMARK_PASS'
    rows=[r for r in bench['rows'] if r['phase']=='formal']
    assert len(bench['rows'])==130 and len(rows)==100
    assert all(r['cpu_logits']==r['pl_logits'] and r['cpu_match_mask']==1023 and r['pl_match_mask']==1023 for r in bench['rows'])
    cpu=sum(r['cpu_ticks'] for r in rows);pl=sum(r['pl_ticks'] for r in rows)
    assert (cpu,pl)==(164569931,78325846)
    manifest_path=ROOT/MANIFEST
    core,optional=inventory()
    if args.write_manifest:
        manifest_path.write_text(json.dumps({'schema':1,'date':'2026-11-03','platform':'Windows x64',
            'excluded':sorted(IGNORED)+['**/__pycache__','**/*.pyc',MANIFEST],
            'core':core,'optional_local_runtime':optional},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    if not manifest_path.exists():raise RuntimeError('Missing final manifest; maintainer must seal the prepared copy')
    saved=json.loads(manifest_path.read_text(encoding='utf-8'))
    if saved['core']!=core:raise RuntimeError('Core files differ from final release_manifest.json')
    if runtime and saved['optional_local_runtime']!=optional:raise RuntimeError('Runtime differs from final release manifest')
    if not runtime:
        for name,item in optional.items():
            if saved['optional_local_runtime'].get(name)!=item:raise RuntimeError('Changed optional file: '+name)
    companion_checked=False
    if args.runtime:
        companion=args.runtime.resolve()
        check(companion/'ees331_studio.exe',build['exe_sha256'])
        rm=json.loads((companion/'runtime_manifest.json').read_text(encoding='utf-8'))
        for name,item in rm['files'].items():
            file=companion/name
            if not file.is_file() or digest(file)!=item['sha256'] or file.stat().st_size!=item['bytes']:
                raise RuntimeError('Companion runtime mismatch: '+name)
        for name,sha in build['resource_sha256'].items():
            file=companion/'_internal/resources'/name
            if not file.is_file() or digest(file)!=sha:raise RuntimeError('Companion resource mismatch: '+name)
        companion_checked=True
    print(json.dumps({'passed':True,'core_files':len(core),'optional_local_files':len(optional),
        'original_source_and_resources_match_frozen_build':True,'arm_and_pl_match_implemented_sources':True,
        'frozen_exe_present_and_checked':runtime or companion_checked,'speedup_from_raw_formal_ticks':cpu/pl,
        'hardware_accessed':False},ensure_ascii=False,indent=2))
    print('PASS: final source, payload and file integrity; no hardware accessed')

if __name__=='__main__':main()
