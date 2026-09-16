"""Centralized Configuration for VIGIL AI Classroom Proctoring Engine.

Enterprise-grade configuration supporting High-Res Inference (SAHI),
Kalman Multi-Object Tracking, Time-based Millisecond Scoring Windows,
Silent Tracking Cooldowns, and 10-Second Evidence Video Buffering.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Set


@dataclass
class ClassroomConfig:
    """Enterprise Configuration parameters for Classroom Surveillance."""

    # ---- Model & Perception Settings ----
    pipeline_mode: str = "1stage_yolo"  # "1stage_yolo" or "2stage_pose"
    model_path: str = "models/classroom_best.pt"
    pose_model_path: str = "yolo11n-pose.pt"
    fallback_model: str = "yolov8n.pt"
    confidence_threshold: float = 0.45
    pose_confidence_threshold: float = 0.20
    nms_iou_threshold: float = 0.45
    default_fps: float = 30.0
    input_resolution: int = 640
    pose_input_resolution: int = 1280
    pose_ai_fps_target: int = 10
    side_peeking_yaw_threshold: float = 28.0
    phone_pitch_threshold: float = 20.0
    phone_wrist_ratio_threshold: float = 0.28

    # ---- High-Resolution Slicing (SAHI / Dynamic Tiling) ----
    enable_sahi_tiling: bool = False
    sahi_slice_size: int = 640
    sahi_overlap_ratio: float = 0.20
    sahi_min_resolution: int = 1280  # Enable slicing if width or height >= threshold

    # ---- Spatial Matching & Kalman Tracking ----
    tracker_type: str = "kalman_iou"  # "kalman_iou" or "iou_only"
    iou_match_threshold: float = 0.30
    distance_cost_weight: float = 0.15  # Weight for centroid distance penalty in cost matrix
    max_missing_frames: int = 15
    max_coasting_frames: int = 12       # Maximum frames to coast predicted bbox during occlusion
    kalman_process_noise: float = 1e-2  # Kinematic process noise covariance
    kalman_measurement_noise: float = 1e-1 # Observation noise covariance

    # ---- Anti-Flickering Time-Aware Score Accumulator ----
    window_duration_ms: float = 1500.0   # 1.5 seconds sliding time window
    min_duration_ms: float = 400.0       # Minimum duration (~0.4s) before deciding
    score_window_size: int = 45          # Fallback frame count if timestamps not supplied
    score_threshold: float = 4.5         # Minimum cumulative score to trigger suspicious
    cheating_ratio_threshold: float = 0.55 # >=55% of window indicates cheating
    min_frames_in_window: int = 12       # Fallback minimum frame count
    normal_penalty: float = -0.30        # Mild penalty for normal frames to absorb flicker

    # ---- Behavior State Machine & Cooldown ----
    cooldown_seconds: float = 5.0
    escalation_duration_seconds: float = 5.0 # After 5s continuous, escalate MEDIUM -> HIGH
    silent_cooldown_tracking: bool = True    # Maintain score accumulation silently during cooldown
    recidivism_escalation: bool = True       # If cheating repeats during cooldown, escalate to HIGH instantly
    recidivism_score_threshold: float = 3.0  # Lower threshold for instant recidivism trigger
    require_human_review: bool = True        # Human-in-the-Loop decision support model

    # ---- Crowd Room Context ----
    collective_suppress_ratio: float = 0.40 # >40% of room doing same act -> Suppress alert
    cluster_distance_threshold: float = 160.0 # Pixel distance for group spatial clustering
    cluster_min_size: int = 3                # >=3 students near each other -> Boost severity

    # ---- Class Names & Categories ----
    class_names: List[str] = field(
        default_factory=lambda: [
            "back peeking",
            "front peeking",
            "no cheating",
            "phone using",
            "side peeking",
        ]
    )
    cheating_classes: Set[str] = field(
        default_factory=lambda: {
            "back peeking",
            "front peeking",
            "phone using",
            "side peeking",
        }
    )
    normal_classes: Set[str] = field(default_factory=lambda: {"no cheating"})

    # ---- Severity Mapping ----
    severity_map: Dict[str, str] = field(
        default_factory=lambda: {
            "phone using": "HIGH",
            "back peeking": "HIGH",
            "side peeking": "MEDIUM",
            "front peeking": "MEDIUM",
        }
    )

    # ---- Evidence Storage & Video Ring Buffer ----
    evidence_dir: str = "data/evidence"
    evidence_video_dir: str = "data/evidence_clips"
    evidence_quality: int = 92           # JPEG quality
    enable_video_evidence: bool = True   # Capture 10s video clip on confirmed/flagged events
    pre_event_seconds: float = 5.0       # Pre-event buffer duration
    post_event_seconds: float = 5.0      # Post-event record duration

    @classmethod
    def from_file(cls, path: str | Path) -> ClassroomConfig:
        """Load configuration from a JSON file."""
        p = Path(path)
        if not p.exists():
            return cls()
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(**{k: v for k, v in data.items() if hasattr(cls, k)})

    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary representation."""
        return {
            "model_path": self.model_path,
            "confidence_threshold": self.confidence_threshold,
            "enable_sahi_tiling": self.enable_sahi_tiling,
            "sahi_slice_size": self.sahi_slice_size,
            "tracker_type": self.tracker_type,
            "iou_match_threshold": self.iou_match_threshold,
            "max_missing_frames": self.max_missing_frames,
            "max_coasting_frames": self.max_coasting_frames,
            "window_duration_ms": self.window_duration_ms,
            "min_duration_ms": self.min_duration_ms,
            "score_window_size": self.score_window_size,
            "score_threshold": self.score_threshold,
            "cheating_ratio_threshold": self.cheating_ratio_threshold,
            "cooldown_seconds": self.cooldown_seconds,
            "silent_cooldown_tracking": self.silent_cooldown_tracking,
            "recidivism_escalation": self.recidivism_escalation,
            "escalation_duration_seconds": self.escalation_duration_seconds,
            "collective_suppress_ratio": self.collective_suppress_ratio,
            "enable_video_evidence": self.enable_video_evidence,
            "class_names": self.class_names,
            "severity_map": self.severity_map,
        }


# Global default instance
DEFAULT_CONFIG = ClassroomConfig()
