#!/usr/bin/env python
"""Single-Video Demonstration Runner for VIGIL AI SRS v2.0.

Executes calibrated end-to-end proctoring on a single video feed:
Examples:
  python scripts/run_demo_video.py --video india
  python scripts/run_demo_video.py --video student --show
  python scripts/run_demo_video.py --video demo_video/india_classroom.mp4 --debug-overlay
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Ensure project root in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from classroom_monitor.demo import (
    build_arg_parser,
    get_demo_config,
    run_demo_pipeline,
)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("RunDemoVideo")


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    video_target = args.input_video or args.video

    overrides = {
        "show_window": args.show,
        "debug_overlay": args.debug_overlay,
        "save_evidence": not args.no_evidence,
        "allow_mock": args.allow_mock,
        "head_provider": args.head_provider,
        "hpe_hz": args.hz,
        "pose_conf": args.conf,
        "pose_imgsz": args.imgsz,
        "max_frames": args.max_frames,
        "stride": args.stride,
    }

    if args.output_dir:
        overrides["output_dir"] = Path(args.output_dir)
    if args.scene_config:
        overrides["scene_config_path"] = Path(args.scene_config)

    config = get_demo_config(name_or_path=video_target, **overrides)
    run_demo_pipeline(config)


if __name__ == "__main__":
    main()
