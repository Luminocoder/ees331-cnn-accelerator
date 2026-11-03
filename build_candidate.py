"""Build the V5 integration in its own directory; preserve all input submissions."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pyinstaller-deps", type=Path,
                        help="optional isolated pip --target directory containing PyInstaller")
    parser.add_argument("--tk-root", type=Path, help="optional extracted matching official Tcl/Tk MSI")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    if args.pyinstaller_deps:
        sys.path.insert(0, str(args.pyinstaller_deps.resolve()))
    extra_paths = []
    if args.tk_root:
        tk_root = args.tk_root.resolve()
        extra_paths = [str(tk_root/"Lib"), str(tk_root/"DLLs")]
        sys.path[:0] = extra_paths
        os.environ["TCL_LIBRARY"] = str(tk_root/"tcl/tcl8.6")
        os.environ["TK_LIBRARY"] = str(tk_root/"tcl/tk8.6")
    import tkinter  # Fail before packaging if the local runtime lacks the GUI.
    import PyInstaller.__main__ as freezer
    work = root.parent/"_build_v5"/(datetime.now().strftime("%Y%m%d-%H%M%S-")+uuid.uuid4().hex[:8])
    work.mkdir(parents=True)
    freezer.run(["--noconfirm", "--onedir", "--console", "--noupx", "--name", "EES331_Studio",
                 "--distpath", str(work/"dist"), "--workpath", str(work/"work"),
                 "--specpath", str(work), "--paths", str(root/"source"),
                 "--add-data", str(root/"resources")+os.pathsep+"resources",
                 "--hidden-import", "board_acceptance",
                 "--hidden-import", "arm_benchmark",
                 *[part for path in extra_paths for part in ("--paths", path)],
                 str(root/"source/ees331_studio.py")])
    app = work/"dist/EES331_Studio"
    shutil.copy2(app/"EES331_Studio.exe", root/"EES331_Studio.exe")
    shutil.copytree(app/"_internal", root/"_internal", dirs_exist_ok=True)
    manifest = {"date": datetime.now().isoformat(), "python": sys.version,
                "pyinstaller": __import__("PyInstaller").__version__,
                "exe_sha256": hashlib.sha256((root/"EES331_Studio.exe").read_bytes()).hexdigest(),
                "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in sorted((root/"source").glob("*.py"))},
                "resource_sha256": {p.relative_to(root/"resources").as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                    for p in sorted((root/"resources").rglob("*")) if p.is_file()},
                "hardware_accessed": False}
    (root/"build-manifest.json").write_text(json.dumps(manifest, indent=2)+"\n", encoding="utf-8")
    print("BUILT", root/"EES331_Studio.exe")


if __name__ == "__main__":
    main()
