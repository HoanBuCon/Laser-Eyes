#!/usr/bin/env python
"""Unified Batch Demonstration Runner for VIGIL AI SRS v2.0.

Sequentially executes the calibrated SRS v2.0 pipeline across both classroom videos:
1. India Classroom (`demo_video/india_classroom.mp4` - 1280x720, Room ROOM-CALIB-01)
2. Student Classroom (`demo_video/student_classroom.mp4` - 640x352, Room ROOM-STUDENT-01)

Exports complete standardized artifacts into:
- data/demo_final/india/
- data/demo_final/student/
- data/demo_final/final_execution_manifest.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

# Ensure project root in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from classroom_monitor.demo import (
    DEMO_PRESETS,
    DemoVideoConfig,
    get_demo_config,
    run_demo_pipeline,
)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("RunDemoAllVideos")


def run_all_demos(
    output_root: Path = Path("data/demo_final"),
    head_provider: str = "sixdrepnet",
    hpe_hz: float = 5.0,
    pose_conf: float = 0.20,
    show_window: bool = False,
    debug_overlay: bool = False,
    save_evidence: bool = True,
    max_frames: int | None = None,
    stride: int = 1,
) -> Dict[str, Any]:
    """Execute both India and Student classroom demos sequentially and aggregate results."""
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    results: Dict[str, Any] = {}
    start_total_time = time.time()

    demo_names = ["india", "student"]
    print("\n" + "=" * 90)
    print(" VIGIL AI SRS v2.0 - BATCH DEMONSTRATION ORCHESTRATOR")
    print(f" Targets: {', '.join(demo_names).upper()} | Provider: {head_provider} @ {hpe_hz:.1f} Hz")
    print("=" * 90)

    for name in demo_names:
        logger.info(">>> Starting Demo: %s ...", name.upper())
        out_dir = output_root / name

        cfg = get_demo_config(
            name_or_path=name,
            output_dir=out_dir,
            head_provider=head_provider,
            hpe_hz=hpe_hz,
            pose_conf=pose_conf,
            show_window=show_window,
            debug_overlay=debug_overlay,
            save_evidence=save_evidence,
            max_frames=max_frames,
            stride=stride,
        )

        try:
            res = run_demo_pipeline(cfg)
            results[name] = {"status": "SUCCESS", "data": res}
        except Exception as e:
            logger.error("Demo failed for %s: %s", name, e, exc_info=True)
            results[name] = {"status": "FAILED", "error": str(e)}

    total_wall = time.time() - start_total_time

    # Print Summary Table
    print("\n" + "=" * 90)
    print(" VIGIL AI SRS v2.0 - BATCH EXECUTION SUMMARY TABLE")
    print("=" * 90)
    header = f"{'Demo Target':<12} | {'Status':<8} | {'Frames':<8} | {'Dur(s)':<8} | {'FPS':<6} | {'Speed':<8} | {'Episodes':<9} | {'Events':<7} | {'Prec / Rec'}"
    print(header)
    print("-" * 90)

    for name, r in results.items():
        if r["status"] == "SUCCESS":
            d = r["data"]
            bm = d.get("benchmark") or {}
            bm_str = f"{bm.get('precision', '-'):.1f}% / {bm.get('recall', '-'):.1f}%" if bm else "N/A"
            row = (
                f"{name:<12} | "
                f"{'SUCCESS':<8} | "
                f"{d['total_frames']:<8} | "
                f"{d['duration_sec']:<8.1f} | "
                f"{d['overall_fps']:<6.1f} | "
                f"{d['realtime_factor']:<7.2f}x | "
                f"{d['total_episodes']:<9} | "
                f"{d['total_events']:<7} | "
                f"{bm_str}"
            )
            print(row)
        else:
            print(f"{name:<12} | {'FAILED':<8} | Error: {r.get('error')}")

    print("=" * 90)
    print(f"Total Batch Runtime: {total_wall:.1f}s")
    print("=" * 90 + "\n")

    # Export Manifest JSON
    manifest_path = output_root / "final_execution_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "total_wall_time_sec": round(total_wall, 2),
                "head_provider": head_provider,
                "hpe_hz": hpe_hz,
                "results": results,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )
    logger.info("Saved batch execution manifest to %s", manifest_path.resolve())

    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="VIGIL AI SRS v2.0 - Batch Multi-Video Demo Orchestrator",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--output-root",
        type=str,
        default="data/demo_final",
        help="Root directory for storing all demo outputs.",
    )
    parser.add_argument(
        "--head-provider",
        type=str,
        default="sixdrepnet",
        choices=["sixdrepnet", "pose_heuristic"],
        help="Head orientation estimation provider.",
    )
    parser.add_argument(
        "--hz",
        type=float,
        default=5.0,
        help="Head pose target frequency on occupied seats.",
    )
    parser.add_argument(
        "--conf",
        type=float,
        default=0.20,
        help="YOLO-Pose detection confidence threshold.",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Display live GUI playback window.",
    )
    parser.add_argument(
        "--debug-overlay",
        action="store_true",
        help="Render detailed debug overlay on frames.",
    )
    parser.add_argument(
        "--no-evidence",
        action="store_true",
        help="Disable async evidence clip extraction.",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=None,
        help="Maximum frames per video (useful for quick checks).",
    )
    parser.add_argument(
        "--stride",
        type=int,
        default=1,
        help="Frame subsampling stride.",
    )

    args = parser.parse_args()

    run_all_demos(
        output_root=Path(args.output_root),
        head_provider=args.head_provider,
        hpe_hz=args.hz,
        pose_conf=args.conf,
        show_window=args.show,
        debug_overlay=args.debug_overlay,
        save_evidence=not args.no_evidence,
        max_frames=args.max_frames,
        stride=args.stride,
    )


if __name__ == "__main__":
    main()
