"""Generate screenshot comparisons for human review (Clean Proctor Mode vs Developer Debug Mode)."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np

from classroom_monitor.demo import (
    DEMO_PRESETS,
    DemoVideoConfig,
    get_demo_config,
    run_demo_pipeline,
)


def extract_sample_frames():
    root = Path(".")
    india_res_path = root / "data" / "demo_final" / "india" / "result.mp4"
    student_res_path = root / "data" / "demo_final" / "student" / "result.mp4"

    diag_dir = root / "data" / "diagnostics"
    diag_dir.mkdir(parents=True, exist_ok=True)
    art_dir = Path(r"C:\Users\ADMIN\.gemini\antigravity-cli\brain\012a305f-c7f3-41ae-af16-8876f25ae244")

    # 1. Extract India Clean Mode frame around an incident (e.g. frame 650 / 800)
    cap_india = cv2.VideoCapture(str(india_res_path))
    cap_india.set(cv2.CAP_PROP_POS_FRAMES, 750)
    ret, frame_india_clean = cap_india.read()
    cap_india.release()

    if ret:
        p1 = diag_dir / "india_clean_sample.jpg"
        cv2.imwrite(str(p1), frame_india_clean)
        shutil.copy(p1, art_dir / "india_clean_sample.jpg")
        print(f"Saved {p1}")

    # 2. Render a short India debug run to extract debug screenshot
    cfg_debug = get_demo_config(
        name_or_path="india",
        output_dir=root / "data" / "diagnostics" / "temp_debug_india",
        head_provider="sixdrepnet",
        hpe_hz=5.0,
        debug_overlay=True,
        save_evidence=False,
        max_frames=800,
    )
    run_demo_pipeline(cfg_debug)

    cap_debug = cv2.VideoCapture(str(root / "data" / "diagnostics" / "temp_debug_india" / "result.mp4"))
    cap_debug.set(cv2.CAP_PROP_POS_FRAMES, 750)
    ret_dbg, frame_india_dbg = cap_debug.read()
    cap_debug.release()

    if ret_dbg:
        p2 = diag_dir / "india_debug_sample.jpg"
        cv2.imwrite(str(p2), frame_india_dbg)
        shutil.copy(p2, art_dir / "india_debug_sample.jpg")
        print(f"Saved {p2}")

    # Clean up temp debug run
    shutil.rmtree(root / "data" / "diagnostics" / "temp_debug_india", ignore_errors=True)

    # 3. Extract Student Clean Mode frame
    cap_stud = cv2.VideoCapture(str(student_res_path))
    cap_stud.set(cv2.CAP_PROP_POS_FRAMES, 120)
    ret_s, frame_stud_clean = cap_stud.read()
    cap_stud.release()

    if ret_s:
        p3 = diag_dir / "student_clean_sample.jpg"
        cv2.imwrite(str(p3), frame_stud_clean)
        shutil.copy(p3, art_dir / "student_clean_sample.jpg")
        print(f"Saved {p3}")


if __name__ == "__main__":
    extract_sample_frames()
