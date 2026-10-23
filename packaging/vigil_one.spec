# PyInstaller spec for the VIGIL One desktop (both modes: proctor and candidate).
# Build with: venv\Scripts\python.exe packaging\build_vigil_one.py
# onedir (not onefile): starts in a second or two instead of unpacking ~200 MB at every launch.

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

ROOT = Path(SPECPATH).resolve().parent

datas = [
    (str(ROOT / "exam_monitor" / "assets"), "exam_monitor/assets"),   # MediaPipe models + DMC5 font
    (str(ROOT / "assets" / "V.png"), "assets"),                        # logo
]
datas += collect_data_files("mediapipe", includes=["**/*.binarypb", "**/*.tflite", "**/*.txt", "**/*.pbtxt"])
datas += collect_data_files("cv2", includes=["data/haarcascade_frontalface_default.xml"])  # OpenCV fallback face detector
binaries = collect_dynamic_libs("mediapipe") + collect_dynamic_libs("sounddevice")

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    # imported lazily inside functions, so PyInstaller cannot see them
    hiddenimports=["sounddevice", "webrtcvad", "exam_monitor.candidate_app", "exam_monitor.app"],
    hookspath=[str(ROOT / "packaging" / "hooks")],
    excludes=[
        # server-side and training stacks: never needed by the desktop app
        "torch", "torchvision", "ultralytics", "tensorflow", "fastapi", "uvicorn", "sqlalchemy",
        # (not matplotlib: "import mediapipe" imports it through mediapipe.python.solutions.drawing_utils)
        "pandas", "scipy", "IPython", "jupyter", "pytest",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VIGIL One",
    icon=str(ROOT / "packaging" / "vigil_one.ico"),
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="VIGIL One")
