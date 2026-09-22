"""Configuration and Presets for VIGIL AI SRS v2.0 Demo Video Execution.

Defines:
- DemoVideoConfig: Dataclass encapsulating single-video run parameters.
- DEMO_PRESETS: Calibrated presets for 'india' (ROOM-CALIB-01) and 'student' (ROOM-STUDENT-01).
- Utility functions for path resolution and command-line argument parsing.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


@dataclass
class DemoVideoConfig:
    """Configuration dataclass for executing a single video through the SRS v2.0 pipeline."""

    name: str
    video_path: Path
    scene_config_path: Optional[Path] = None
    output_dir: Path = field(default_factory=lambda: Path("data/demo_final"))
    room_code: str = "ROOM-01"
    camera_id: str = "CAM-01"
    head_provider: str = "sixdrepnet"
    hpe_hz: float = 5.0
    pose_conf: float = 0.20
    pose_imgsz: int = 1280
    show_window: bool = False
    debug_overlay: bool = False
    save_evidence: bool = True
    allow_mock: bool = False
    max_frames: Optional[int] = None
    stride: int = 1
    gt_path: Optional[Path] = None
    seats_preset: Optional[List[Dict[str, Any]]] = None

    def __post_init__(self) -> None:
        if isinstance(self.video_path, str):
            self.video_path = Path(self.video_path)
        if isinstance(self.scene_config_path, str):
            self.scene_config_path = Path(self.scene_config_path)
        if isinstance(self.output_dir, str):
            self.output_dir = Path(self.output_dir)
        if isinstance(self.gt_path, str):
            self.gt_path = Path(self.gt_path)


# Calibrated Seat Fallback Definitions for Video 1: India Classroom (1280x720, Room ROOM-CALIB-01)
INDIA_CALIBRATED_SEATS = [
    {"seat_code": "SEAT-ROOM-CALIB-01-01", "seat_label": "Bàn 1 Dãy Trái", "polygon_json": [[92.0, 390.0], [299.0, 390.0], [299.0, 568.0], [92.0, 568.0]], "desk_y": 480.0, "baseline_yaw": 0.0},
    {"seat_code": "SEAT-ROOM-CALIB-01-02", "seat_label": "Bàn 1 Dãy Giữa", "polygon_json": [[484.0, 485.0], [702.0, 485.0], [702.0, 651.0], [484.0, 651.0]], "desk_y": 560.0, "baseline_yaw": -5.0},
    {"seat_code": "SEAT-ROOM-CALIB-01-03", "seat_label": "Bàn 1 Dãy Phải", "polygon_json": [[978.0, 413.0], [1168.0, 413.0], [1168.0, 682.0], [978.0, 682.0]], "desk_y": 550.0, "baseline_yaw": -10.0},
    {"seat_code": "SEAT-ROOM-CALIB-01-04", "seat_label": "Bàn 2 Dãy Trái", "polygon_json": [[190.0, 283.0], [382.0, 283.0], [382.0, 418.0], [190.0, 418.0]], "desk_y": 350.0, "baseline_yaw": 5.0},
    {"seat_code": "SEAT-ROOM-CALIB-01-05", "seat_label": "Bàn 2 Dãy Giữa", "polygon_json": [[470.0, 324.0], [650.0, 324.0], [650.0, 502.0], [470.0, 502.0]], "desk_y": 410.0, "baseline_yaw": -2.0},
    {"seat_code": "SEAT-ROOM-CALIB-01-06", "seat_label": "Bàn 2 Dãy Phải", "polygon_json": [[768.0, 334.0], [932.0, 334.0], [932.0, 484.0], [768.0, 484.0]], "desk_y": 410.0, "baseline_yaw": -8.0},
    {"seat_code": "SEAT-ROOM-CALIB-01-07", "seat_label": "Bàn 3 Dãy Trái", "polygon_json": [[262.0, 226.0], [409.0, 226.0], [409.0, 326.0], [262.0, 326.0]], "desk_y": 270.0, "baseline_yaw": 8.0},
    {"seat_code": "SEAT-ROOM-CALIB-01-08", "seat_label": "Bàn 3 Dãy Giữa", "polygon_json": [[472.0, 240.0], [626.0, 240.0], [626.0, 344.0], [472.0, 344.0]], "desk_y": 290.0, "baseline_yaw": 0.0},
    {"seat_code": "SEAT-ROOM-CALIB-01-09", "seat_label": "Bàn 3 Dãy Phải", "polygon_json": [[688.0, 246.0], [832.0, 246.0], [832.0, 356.0], [688.0, 356.0]], "desk_y": 300.0, "baseline_yaw": -6.0},
    {"seat_code": "SEAT-ROOM-CALIB-01-10", "seat_label": "Bàn 4 Dãy Trái", "polygon_json": [[308.0, 184.0], [428.0, 184.0], [428.0, 256.0], [308.0, 256.0]], "desk_y": 220.0, "baseline_yaw": 10.0},
    {"seat_code": "SEAT-ROOM-CALIB-01-11", "seat_label": "Bàn 4 Dãy Giữa", "polygon_json": [[476.0, 192.0], [598.0, 192.0], [598.0, 268.0], [476.0, 268.0]], "desk_y": 230.0, "baseline_yaw": 0.0},
    {"seat_code": "SEAT-ROOM-CALIB-01-12", "seat_label": "Bàn 4 Dãy Phải", "polygon_json": [[642.0, 198.0], [756.0, 198.0], [756.0, 276.0], [642.0, 276.0]], "desk_y": 235.0, "baseline_yaw": -5.0},
    {"seat_code": "SEAT-ROOM-CALIB-01-13", "seat_label": "Bàn 5 Dãy Trái", "polygon_json": [[344.0, 150.0], [442.0, 150.0], [442.0, 206.0], [344.0, 206.0]], "desk_y": 180.0, "baseline_yaw": 10.0},
    {"seat_code": "SEAT-ROOM-CALIB-01-14", "seat_label": "Bàn 5 Dãy Giữa", "polygon_json": [[478.0, 156.0], [576.0, 156.0], [576.0, 214.0], [478.0, 214.0]], "desk_y": 185.0, "baseline_yaw": 0.0},
    {"seat_code": "SEAT-ROOM-CALIB-01-15", "seat_label": "Bàn 5 Dãy Phải", "polygon_json": [[606.0, 160.0], [702.0, 160.0], [702.0, 220.0], [606.0, 220.0]], "desk_y": 190.0, "baseline_yaw": -4.0},
    {"seat_code": "SEAT-ROOM-CALIB-01-16", "seat_label": "Bàn 6 Dãy Trái", "polygon_json": [[372.0, 126.0], [452.0, 126.0], [452.0, 172.0], [372.0, 172.0]], "desk_y": 150.0, "baseline_yaw": 8.0},
    {"seat_code": "SEAT-ROOM-CALIB-01-17", "seat_label": "Bàn 6 Dãy Giữa", "polygon_json": [[480.0, 130.0], [560.0, 130.0], [560.0, 176.0], [480.0, 176.0]], "desk_y": 155.0, "baseline_yaw": 0.0},
    {"seat_code": "SEAT-ROOM-CALIB-01-18", "seat_label": "Bàn 6 Dãy Phải", "polygon_json": [[580.0, 134.0], [658.0, 134.0], [658.0, 180.0], [580.0, 180.0]], "desk_y": 160.0, "baseline_yaw": -3.0},
    {"seat_code": "SEAT-ROOM-CALIB-01-19", "seat_label": "Bàn 7 Dãy Trái", "polygon_json": [[392.0, 108.0], [460.0, 108.0], [460.0, 146.0], [392.0, 146.0]], "desk_y": 125.0, "baseline_yaw": 5.0},
    {"seat_code": "SEAT-ROOM-CALIB-01-20", "seat_label": "Bàn 7 Dãy Giữa", "polygon_json": [[482.0, 110.0], [548.0, 110.0], [548.0, 148.0], [482.0, 148.0]], "desk_y": 130.0, "baseline_yaw": 0.0},
    {"seat_code": "SEAT-ROOM-CALIB-01-21", "seat_label": "Bàn 7 Dãy Phải", "polygon_json": [[562.0, 112.0], [626.0, 112.0], [626.0, 150.0], [562.0, 150.0]], "desk_y": 135.0, "baseline_yaw": -2.0},
]

# Calibrated Seat Fallback Definitions for Video 2: Student Classroom (640x352, Room ROOM-STUDENT-01)
STUDENT_CALIBRATED_SEATS = [
    {"seat_code": "SEAT-STUDENT-01", "seat_label": "Bàn 1 Dãy Giữa", "polygon_json": [[194.0, 209.0], [283.0, 209.0], [283.0, 329.0], [194.0, 329.0]], "desk_y": 280.0, "baseline_yaw": 0.0},
    {"seat_code": "SEAT-STUDENT-02", "seat_label": "Bàn 1 Dãy Trái", "polygon_json": [[28.0, 187.0], [100.0, 187.0], [100.0, 259.0], [28.0, 259.0]], "desk_y": 225.0, "baseline_yaw": 6.0},
    {"seat_code": "SEAT-STUDENT-03", "seat_label": "Bàn 1 Dãy Phải", "polygon_json": [[465.0, 200.0], [537.0, 200.0], [537.0, 263.0], [465.0, 263.0]], "desk_y": 235.0, "baseline_yaw": -6.0},
    {"seat_code": "SEAT-STUDENT-04", "seat_label": "Bàn 1 Góc Phải Xa", "polygon_json": [[503.0, 179.0], [579.0, 179.0], [579.0, 251.0], [503.0, 251.0]], "desk_y": 215.0, "baseline_yaw": -8.0},
    {"seat_code": "SEAT-STUDENT-05", "seat_label": "Bàn 2 Dãy Giữa", "polygon_json": [[232.0, 153.0], [310.0, 153.0], [310.0, 269.0], [232.0, 269.0]], "desk_y": 210.0, "baseline_yaw": 0.0},
    {"seat_code": "SEAT-STUDENT-06", "seat_label": "Bàn 2 Dãy Trái", "polygon_json": [[80.0, 185.0], [132.0, 185.0], [132.0, 301.0], [80.0, 301.0]], "desk_y": 240.0, "baseline_yaw": 5.0},
    {"seat_code": "SEAT-STUDENT-07", "seat_label": "Bàn 2 Dãy Phải", "polygon_json": [[432.0, 125.0], [525.0, 125.0], [525.0, 226.0], [432.0, 226.0]], "desk_y": 175.0, "baseline_yaw": -5.0},
    {"seat_code": "SEAT-STUDENT-08", "seat_label": "Bàn 3 Dãy Giữa", "polygon_json": [[230.0, 112.0], [312.0, 112.0], [312.0, 202.0], [230.0, 202.0]], "desk_y": 160.0, "baseline_yaw": 0.0},
    {"seat_code": "SEAT-STUDENT-09", "seat_label": "Bàn 3 Dãy Phải", "polygon_json": [[291.0, 113.0], [353.0, 113.0], [353.0, 204.0], [291.0, 204.0]], "desk_y": 160.0, "baseline_yaw": -3.0},
    {"seat_code": "SEAT-STUDENT-10", "seat_label": "Bàn 3 Dãy Trái", "polygon_json": [[54.0, 140.0], [116.0, 140.0], [116.0, 213.0], [54.0, 213.0]], "desk_y": 180.0, "baseline_yaw": 5.0},
    {"seat_code": "SEAT-STUDENT-11", "seat_label": "Bàn 4 Dãy Trái", "polygon_json": [[97.0, 114.0], [184.0, 114.0], [184.0, 222.0], [97.0, 222.0]], "desk_y": 170.0, "baseline_yaw": 4.0},
    {"seat_code": "SEAT-STUDENT-12", "seat_label": "Bàn 4 Dãy Phải", "polygon_json": [[469.0, 139.0], [526.0, 139.0], [526.0, 186.0], [469.0, 186.0]], "desk_y": 165.0, "baseline_yaw": -4.0},
]

DEMO_PRESETS: Dict[str, Dict[str, Any]] = {
    "india": {
        "name": "india",
        "video_path": "demo_video/india_classroom.mp4",
        "scene_config_path": "configs/scenes/india_classroom.yaml",
        "room_code": "ROOM-CALIB-01",
        "camera_id": "CAM-CALIB-01",
        "output_dir": "data/demo_final/india",
        "gt_path": "data/ground_truth/india_classroom_gt.json",
        "pose_imgsz": 1280,
        "seats_preset": INDIA_CALIBRATED_SEATS,
    },
    "student": {
        "name": "student",
        "video_path": "demo_video/student_classroom.mp4",
        "scene_config_path": "configs/scenes/student_classroom.yaml",
        "room_code": "ROOM-STUDENT-01",
        "camera_id": "CAM-STUDENT-01",
        "output_dir": "data/demo_final/student",
        "gt_path": "data/ground_truth/student_classroom_gt.json",
        "pose_imgsz": 640,
        "seats_preset": STUDENT_CALIBRATED_SEATS,
    },
}


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
    """Instantiate a DemoVideoConfig from a preset name ('india', 'student') or custom video path."""
    clean_name = name_or_path.lower().strip()
    # Check alias in presets
    if clean_name in DEMO_PRESETS:
        preset = dict(DEMO_PRESETS[clean_name])
        # Resolve video path
        preset["video_path"] = resolve_video_path(preset["video_path"])
        if preset.get("scene_config_path"):
            preset["scene_config_path"] = Path(preset["scene_config_path"])
        if preset.get("output_dir"):
            preset["output_dir"] = Path(preset["output_dir"])
        if preset.get("gt_path"):
            preset["gt_path"] = Path(preset["gt_path"])

        # Apply overrides
        for k, v in kwargs.items():
            if v is not None:
                preset[k] = v
        return DemoVideoConfig(**preset)

    # Custom video path
    resolved_video = resolve_video_path(name_or_path)
    stem_name = resolved_video.stem
    output_dir = Path(kwargs.get("output_dir", f"data/demo_final/{stem_name}"))
    room_code = kwargs.get("room_code", f"ROOM-{stem_name.upper()}")
    camera_id = kwargs.get("camera_id", f"CAM-{stem_name.upper()}-01")

    # Look for matching scene YAML if not explicitly provided
    scene_path = kwargs.get("scene_config_path")
    if scene_path is None:
        cand1 = Path(f"configs/scenes/{stem_name}.yaml")
        if cand1.exists():
            scene_path = cand1

    config_dict: Dict[str, Any] = {
        "name": stem_name,
        "video_path": resolved_video,
        "scene_config_path": scene_path,
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
        default="india",
        help="Demo preset ('india', 'student') or path to video file (.mp4).",
    )
    parser.add_argument(
        "--input",
        type=str,
        dest="input_video",
        default=None,
        help="Alternative argument for custom input video path.",
    )
    parser.add_argument(
        "--scene",
        type=str,
        dest="scene_config",
        default=None,
        help="Path to custom scene context YAML configuration.",
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
        help="YOLO-Pose input resolution (default: 1280 for india, 640 for student).",
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
