"""Verify source/resource/build and unchanged input submissions, without hardware.

Default is read-only. --record saves the local audit; --write-manifest seals the
delivery directory excluding runtime logs, Python caches and the manifest itself.
The original submissions are optional on a recipient's machine and are reported
as unavailable rather than silently claimed to have been checked.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def files(root):
    return {p.relative_to(root).as_posix(): p for p in root.rglob("*") if p.is_file()}


def check(path, sha, size=None):
    if not path.is_file() or (size is not None and path.stat().st_size != size) or digest(path) != sha:
        raise RuntimeError("File mismatch: "+str(path))


def delivery_files():
    return {name: path for name, path in files(ROOT).items()
            if name != "delivery-manifest.json" and name.split("/")[0] != "logs"
            and not name.startswith("hardware/build/") and "__pycache__" not in Path(name).parts and path.suffix != ".pyc"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--write-manifest", action="store_true")
    args = parser.parse_args()
    build = json.loads((ROOT/"build-manifest.json").read_text(encoding="utf-8"))
    check(ROOT/"EES331_Studio.exe", build["exe_sha256"])
    if {p.name for p in (ROOT/"source").glob("*.py")} != set(build["source_sha256"]):
        raise RuntimeError("Source file set changed since build")
    for name, sha in build["source_sha256"].items():
        check(ROOT/"source"/name, sha)
    for directory in (ROOT/"resources", ROOT/"_internal/resources"):
        if set(files(directory)) != set(build["resource_sha256"]):
            raise RuntimeError("Resource file set changed: "+str(directory))
        for name, sha in build["resource_sha256"].items():
            check(directory/name, sha)
    audit = json.loads((ROOT/'integration/source-package-audit.json').read_text(encoding='utf-8'))
    firmware = json.loads((ROOT/'integration/firmware-build.json').read_text(encoding='utf-8'))
    for name, sha in firmware['inputs'].items():
        check(ROOT/'hardware/arm'/name, sha)
    check(ROOT/'resources/flash_payload/app.elf', firmware['elf_sha256'])
    unchanged_pl = 0
    for name, sha in audit['files'].items():
        if name.startswith('2_PL工程源码/') and not name.endswith(('rtl/display/sevenseg_axi.v', 'rtl/display/sevenseg_result.v',
                'rtl/soc/mnist_soc_top_tb.sv','rtl/accelerator/conv_engine.sv','rtl/accelerator/conv_bias_tb.sv')):
            relative = name.split('/', 1)[1]
            check(ROOT/'hardware/pl'/relative, sha)
            unchanged_pl += 1
    implementation = json.loads((ROOT/'integration/pl-implementation-hashes.json').read_text(encoding='utf-8'))
    if set(files(ROOT/'hardware/pl')) != set(implementation['files']):
        raise RuntimeError('PL file set changed since implemented source mirror')
    for name, sha in implementation['files'].items():
        check(ROOT/'hardware/pl'/name,sha)
    for name, sha in implementation['test_inputs'].items():
        check(ROOT/name,sha)
    board_build=json.loads((ROOT/'integration/board-build.json').read_text(encoding='utf-8'))
    if board_build['status']!='BUILD_PASS_NOT_BOARD_TESTED':
        raise RuntimeError('Successful bitstream build evidence missing')
    check(ROOT/'resources/flash_payload/system.bit',board_build['outputs']['system.bit'])
    report = {'hardware_accessed': False, 'exe_sha256': build['exe_sha256'],
              'source_and_exe_match_build': True, 'resources_and_packaged_resources_match': True,
              'original_source_package_payload_matches_v52': audit['original_payload_matches_v52'],
              'pl_files_unchanged_outside_declared_display_conv_and_test_edits': unchanged_pl,
              'all_pl_source_matches_implemented_mirror': True,
              'deployment_bit_matches_vivado_build': True,
              'hardware_verification_pending': True}
    if args.record:
        (ROOT/"integration/integrity-verification.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    manifest_path = ROOT/"delivery-manifest.json"
    if args.write_manifest:
        manifest = {"version": "5.6-convfast-candidate", "date": datetime.now().date().isoformat(),
                    "excluded": ["hardware/build/", "logs/", "**/__pycache__/", "**/*.pyc", "delivery-manifest.json"],
                    "files": {name: {"bytes": path.stat().st_size, "sha256": digest(path)}
                              for name, path in sorted(delivery_files().items())}}
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        current = delivery_files()
        if set(current) != set(manifest["files"]):
            raise RuntimeError("Delivery file set changed")
        for name, item in manifest["files"].items():
            check(current[name], item["sha256"], item["bytes"])
        report["delivery_manifest_verified"] = True
        report["delivery_file_count"] = len(current)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("PASS: integrity verified; no hardware accessed")


if __name__ == "__main__":
    main()
