"""Build the VIGIL One installer for candidates.

    venv\\Scripts\\python.exe packaging\\build_vigil_one.py --hub https://thi.truong.edu.vn [--version 2.0.0] [--proctor]

1. PyInstaller -> dist\\VIGIL One\\  (folder app, runs without Python)
2. writes packaging\\vigil-one.json (mode + Hub address, locked for candidates)
3. Inno Setup (ISCC.exe, if installed) -> dist\\VIGIL-One-Setup-<version>.exe

Needs: pip install pyinstaller; Inno Setup 6 for step 3 (otherwise ship the dist folder as a zip).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACK = ROOT / "packaging"


def make_icon() -> Path:
    icon = PACK / "vigil_one.ico"
    if not icon.exists():
        from PIL import Image

        src = Image.open(ROOT / "assets" / "V.png").convert("RGBA")
        src = src.crop(src.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox())
        side = max(src.size)
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        square.paste(src, ((side - src.width) // 2, (side - src.height) // 2))
        square.resize((256, 256), Image.Resampling.LANCZOS).save(icon, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return icon


def find_iscc() -> str | None:
    for candidate in (shutil.which("ISCC"),
                      os.path.expandvars(r"%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"),
                      os.path.expandvars(r"%ProgramFiles%\Inno Setup 6\ISCC.exe"),
                      os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe")):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hub", required=True, help="VIGIL Hub address the candidates connect to (https:// on the Internet)")
    parser.add_argument("--version", default="2.0.0")
    parser.add_argument("--proctor", action="store_true", help="Build the proctor-mode app instead of candidate mode")
    parser.add_argument("--unlock", action="store_true", help="Let candidates edit the Hub address (test builds)")
    parser.add_argument("--skip-pyinstaller", action="store_true", help="Reuse dist\\VIGIL One from an earlier build")
    args = parser.parse_args()

    make_icon()
    if not args.skip_pyinstaller:
        subprocess.check_call([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                               "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build" / "pyinstaller"),
                               str(PACK / "vigil_one.spec")], cwd=ROOT)
    config = {"mode": "proctor" if args.proctor else "candidate", "hub_url": args.hub.rstrip("/"),
              "lock_hub_url": not (args.proctor or args.unlock), "camera_index": 0}
    (PACK / "vigil-one.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.copy2(PACK / "vigil-one.json", ROOT / "dist" / "VIGIL One" / "vigil-one.json")
    print(f"[build] app folder: {ROOT / 'dist' / 'VIGIL One'} (config: {config})")

    iscc = find_iscc()
    if iscc is None:
        archive = shutil.make_archive(str(ROOT / "dist" / f"VIGIL-One-{args.version}-win64"), "zip", ROOT / "dist", "VIGIL One")
        print(f"[build] Inno Setup not found: portable zip {archive} (unzip anywhere, run 'VIGIL One.exe')")
        return 0
    subprocess.check_call([iscc, f"/DAppVersion={args.version}", str(PACK / "vigil_one.iss")], cwd=PACK)
    print(f"[build] installer: {ROOT / 'dist' / f'VIGIL-One-Setup-{args.version}.exe'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
