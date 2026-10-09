"""Configuration and Presets for VIGIL AI SRS v2.0 Demo Video Execution.

Defines:
- DemoVideoConfig: Dataclass encapsulating single-video run parameters.
- DEMO_PRESETS: video/room bindings for the demo videos (china1..china3, ROOM-CHINA-01..03).
  Seat ROIs are NOT part of a preset: they are drawn on the web calibration page
  and read from the database at run time (see classroom_monitor.demo.seating).
- Utility functions for path resolution and command-line argument parsing.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


@dataclass
class DemoVideoConfig:
    """Configuration dataclass for executing a single video through the SRS v2.0 pipeline."""

    name: str
    video_path: Path
    # Starting layout the calibration page can import; never read by a run.
    seat_template_path: Optional[Path] = None
    output_dir: Path = field(default_factory=lambda: Path("data/demo_final"))
    room_code: str = "ROOM-01"
    camera_id: str = "CAM-01"
    head_provider: str = "sixdrepnet"
    hpe_hz: float = 5.0
    pose_conf: float = 0.20
    pose_imgsz: int = 1280
    show_window: bool = False
    debug_overlay: bool = False
    behavior_labels: bool = False
    save_evidence: bool = True
    allow_mock: bool = False
    max_frames: Optional[int] = None
    stride: int = 1
    gt_path: Optional[Path] = None
    # Seats given in code (tests / controlled experiments only); runs otherwise
    # use the seats saved through the web calibration page.
    seats_preset: Optional[List[Dict[str, Any]]] = None
    # Default camera placement when the calibration does not declare one.
    camera_view: str = "facing_subjects"

    def __post_init__(self) -> None:
        if isinstance(self.video_path, str):
            self.video_path = Path(self.video_path)
        if isinstance(self.seat_template_path, str):
            self.seat_template_path = Path(self.seat_template_path)
        if isinstance(self.output_dir, str):
            self.output_dir = Path(self.output_dir)
        if isinstance(self.gt_path, str):
            self.gt_path = Path(self.gt_path)


# The demo videos offered by the website, the calibration page and the CLI.
# This is the only list: add or remove a video here and every interface follows.
# Seat ROIs are drawn per room on the calibration page; no layout is bundled.
DEMO_PRESETS: Dict[str, Dict[str, Any]] = {
    f"china{n}": {
        "name": f"china{n}",
        "title": f"China Classroom {n}",
        "video_path": f"demo_video/china{n}_classroom.mp4",
        "room_code": f"ROOM-CHINA-0{n}",
        "camera_id": f"CAM-CHINA-0{n}",
        "output_dir": f"data/demo_final/china{n}",
        "resolution": "1920x1080",
        "pose_imgsz": 1280,
    }
    for n in (1, 2, 3)
}
DEFAULT_PRESET = "china1"


def resolve_video_path(video_arg: Union[str, Path], video_dir: str = "demo_video") -> Path:
    """Resolve a video file path across multiple common directory locations."""
    v_path = Path(video_arg)
    if v_path.exists():
        return v_path

    # Try exact match or with .mp4 in video_dir
    p1 = Path(video_dir) / video_arg
    if p1.exists():
        return p1
    if not str(video_arg).endswith(".mp4"):
        p2 = Path(video_dir) / f"{video_arg}.mp4"
        if p2.exists():
            return p2

    # Fallback search across directories
    for search_dir in ["demo_video", "video", "data"]:
        p3 = Path(search_dir) / video_arg
        if p3.exists():
            return p3
        p4 = Path(search_dir) / f"{video_arg}.mp4"
        if p4.exists():
            return p4

    raise FileNotFoundError(
        f"Video feed not found for '{video_arg}'. Searched local path, demo_video/, video/, and data/."
    )


def get_demo_config(name_or_path: str, **kwargs: Any) -> DemoVideoConfig:
    """Instantiate a DemoVideoConfig from a preset name (see DEMO_PRESETS) or custom video path."""
    clean_name = name_or_path.lower().strip()
    # Check alias in presets
    if clean_name in DEMO_PRESETS:
        preset = dict(DEMO_PRESETS[clean_name])
        # Resolve video path
        preset["video_path"] = resolve_video_path(preset["video_path"])
        if preset.get("seat_template_path"):
            preset["seat_template_path"] = Path(preset["seat_template_path"])
        if preset.get("output_dir"):
            preset["output_dir"] = Path(preset["output_dir"])
        if preset.get("gt_path"):
            preset["gt_path"] = Path(preset["gt_path"])

        # Apply overrides
        for k, v in kwargs.items():
            if v is not None:
                preset[k] = v
        # Display-only keys (title, resolution) are not run settings
        run_fields = {f.name for f in fields(DemoVideoConfig)}
        return DemoVideoConfig(**{k: v for k, v in preset.items() if k in run_fields})

    # Custom video path
    resolved_video = resolve_video_path(name_or_path)
    stem_name = resolved_video.stem
    output_dir = Path(kwargs.get("output_dir", f"data/demo_final/{stem_name}"))
    room_code = kwargs.get("room_code", f"ROOM-{stem_name.upper()}")
    camera_id = kwargs.get("camera_id", f"CAM-{stem_name.upper()}-01")

    config_dict: Dict[str, Any] = {
        "name": stem_name,
        "video_path": resolved_video,
        "output_dir": output_dir,
        "room_code": room_code,
        "camera_id": camera_id,
        "head_provider": kwargs.get("head_provider", "sixdrepnet"),
        "hpe_hz": kwargs.get("hpe_hz", 5.0),
        "pose_conf": kwargs.get("pose_conf", 0.20),
        "pose_imgsz": kwargs.get("pose_imgsz", 1280),
        "show_window": kwargs.get("show_window", False),
        "debug_overlay": kwargs.get("debug_overlay", False),
        "save_evidence": kwargs.get("save_evidence", True),
        "allow_mock": kwargs.get("allow_mock", False),
        "max_frames": kwargs.get("max_frames", None),
        "stride": kwargs.get("stride", 1),
        "gt_path": kwargs.get("gt_path", None),
        "seats_preset": kwargs.get("seats_preset", None),
    }
    return DemoVideoConfig(**config_dict)


def build_arg_parser() -> argparse.ArgumentParser:
    """Create standard CLI argument parser for demo video execution."""
    parser = argparse.ArgumentParser(
        description="VIGIL AI SRS v2.0 - Calibrated Classroom Demo Video Runner",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--allow-mock",
        action="store_true",
        help="Explicitly allow synthetic pose detections and visibly mark the run as MOCK",
    )
    parser.add_argument(
        "--video",
        type=str,
        default=DEFAULT_PRESET,
        help=f"Demo preset ({', '.join(DEMO_PRESETS)}) or path to video file (.mp4).",
    )
    parser.add_argument(
        "--input",
        type=str,
        dest="input_video",
        default=None,
        help="Alternative argument for custom input video path.",
    )
    parser.add_argument(
        "--output",
        type=str,
        dest="output_dir",
        default=None,
        help="Custom output directory path.",
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
        help="Head pose estimation target frequency (Hz) on occupied seats.",
    )
    parser.add_argument(
        "--conf",
        type=float,
        default=0.20,
        help="YOLO-Pose detection confidence threshold.",
    )
    parser.add_argument(
        "--imgsz",
        type=int,
        default=None,
        help="YOLO-Pose input resolution.",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Display live GUI playback window.",
    )
    parser.add_argument(
        "--debug-overlay",
        action="store_true",
        help="Render detailed debug metrics overlay (latencies, raw yaw vs relative yaw).",
    )
    parser.add_argument(
        "--behavior-labels",
        action="store_true",
        help="Show display-only behaviour tags (head turn, posture, hands); press 'B' in the window to toggle.",
    )
    parser.add_argument(
        "--no-evidence",
        action="store_true",
        help="Disable async evidence video clip saving.",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=None,
        help="Maximum frames to process.",
    )
    parser.add_argument(
        "--stride",
        type=int,
        default=1,
        help="Frame subsampling stride.",
    )
    return parser
