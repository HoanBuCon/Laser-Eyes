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
    if venv_python is None or os.environ.get("VIGIL_VENV_REEXEC") == "1" or getattr(sys, "frozen", False):
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
    parser.add_argument("--candidate", action="store_true", help="Chế độ thí sinh thi trực tuyến (cũng bật bằng vigil-one.json)")
    parser.add_argument("--sim", action="store_true", help="Chế độ thí sinh với camera mô phỏng (thử nghiệm)")
    parser.add_argument("--hub", help="Địa chỉ VIGIL Hub cho chế độ thí sinh")
    parser.add_argument("--selftest", metavar="REPORT.json", help="Kiểm tra cài đặt (model, camera) rồi thoát")
    args = parser.parse_args()

    if args.selftest:
        raise SystemExit(_selftest(args.selftest))

    config = _load_config()
    if args.hub:
        config["hub_url"] = args.hub
    if args.candidate or args.sim or config.get("mode") == "candidate":
        from exam_monitor.candidate_app import run as run_candidate

        run_candidate(config, simulate=args.sim)
        return

    from exam_monitor.app import run

    run(auto_demo=args.demo, screenshot_path=args.screenshot)


def _selftest(report_path: str) -> int:
    """Check an installation: models load and analyse a frame, a camera opens. Writes a JSON report."""
    import json
    import time
    import traceback

    report: dict = {"frozen": bool(getattr(sys, "frozen", False)), "python": sys.version.split()[0]}
    try:
        import cv2
        import numpy as np

        from exam_monitor.engine import GazeAnalyzer
        from exam_monitor.paths import app_data_dir, resource

        report["data_dir"] = str(app_data_dir())
        report["logo"] = resource("assets", "V.png").is_file()
        analyzer = GazeAnalyzer()
        report["face_backend"] = analyzer.backend_name
        report["object_detector"] = getattr(analyzer, "_object_detector", None) is not None
        report["init_error"] = getattr(analyzer, "init_error", "")
        report["model_exists"] = analyzer.model_path.is_file()
        frame = np.full((720, 1280, 3), 90, np.uint8)
        cv2.circle(frame, (640, 330), 140, (170, 180, 200), -1)
        started = time.perf_counter()
        result = analyzer.analyze(frame, time.monotonic())
        report["analyze_ms"] = round((time.perf_counter() - started) * 1000, 1)
        report["faces_on_test_frame"] = int(result.face_count)
        analyzer.close()
        capture = cv2.VideoCapture(0, cv2.CAP_DSHOW)
        report["camera0_opens"] = bool(capture.isOpened())
        capture.release()
        report["ok"] = "MediaPipe" in report["face_backend"]
    except Exception:
        report["ok"] = False
        report["error"] = traceback.format_exc()
    Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1


def _load_config() -> dict:
    """vigil-one.json next to the app (shipped with the installer): mode, hub_url, lock_hub_url, camera_index."""
    import json

    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    path = base / "vigil-one.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


if __name__ == "__main__":
    main()
