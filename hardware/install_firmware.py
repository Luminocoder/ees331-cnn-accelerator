"""Install the matching freshly built ELF into source resources; no board access.
Rebuild the EXE afterwards so its embedded resources and manifest also match.
"""
from pathlib import Path
import hashlib
import json
import shutil

ROOT=Path(__file__).resolve().parent.parent
def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    build=ROOT/'hardware/build/arm'
    record=json.loads((build/'build.json').read_text(encoding='utf-8'))
    for name,sha in record['inputs'].items():
        if digest(ROOT/'hardware/arm'/name)!=sha: raise RuntimeError('Source changed since ARM build: '+name)
    if digest(build/'app.elf')!=record['elf_sha256']: raise RuntimeError('ELF differs from ARM build')
    shutil.copy2(build/'app.elf',ROOT/'resources/flash_payload/app.elf')
    (ROOT/'integration').mkdir(exist_ok=True)
    shutil.copy2(build/'build.json',ROOT/'integration/firmware-build.json')
    path=ROOT/'resources/deployment-manifest.json'
    manifest=json.loads(path.read_text(encoding='utf-8'))
    manifest['source']='v5.4 ARM scalar benchmark + v5.3 PAG1 PL + guarded V5 initialization; hardware acceptance pending'
    for name in manifest['files']:
        p=ROOT/'resources'/name
        manifest['files'][name]={'bytes':p.stat().st_size,'sha256':digest(p)}
    path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('ELF_INSTALLED_NO_HARDWARE: rebuild EXE, run validation, then seal integrity manifest')

if __name__=='__main__': main()
