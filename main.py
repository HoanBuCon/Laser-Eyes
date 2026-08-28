from __future__ import annotations

import argparse
import ctypes
import os
from pathlib import Path
import subprocess
import sys


def _relaunch_in_project_venv() -> None:
    """Make `python main.py` as reliable as launching through run_app.bat."""
    project_dir = Path(__file__).resolve().parent
    venv_python = None
    venv_dir = None
    for name in (".venv", "venv"):
        candidate_dir = project_dir / name
        candidate_python = candidate_dir / "Scripts" / "python.exe"
        if candidate_python.exists():
            venv_dir = candidate_dir
            venv_python = candidate_python
            break
    if venv_python is None or os.environ.get("VIGIL_VENV_REEXEC") == "1":
        return
    try:
        if venv_dir and Path(sys.prefix).resolve() == venv_dir.resolve():
            return
    except OSError:
        pass
    env = os.environ.copy()
    env["VIGIL_VENV_REEXEC"] = "1"
    completed = subprocess.run(
        [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]],
        cwd=project_dir,
        env=env,
        check=False,
    )
    raise SystemExit(completed.returncode)


def main() -> None:
    _relaunch_in_project_venv()
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    parser = argparse.ArgumentParser(description="VIGIL AI local exam monitoring MVP")
    parser.add_argument("--demo", action="store_true", help="Mở ứng dụng và tự chạy nguồn mô phỏng")
    parser.add_argument("--screenshot", help=argparse.SUPPRESS)
    args = parser.parse_args()

    from exam_monitor.app import run

    run(auto_demo=args.demo, screenshot_path=args.screenshot)


if __name__ == "__main__":
    main()
