"""Centralized Configuration for VIGIL AI Classroom Proctoring Engine."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Set


@dataclass
class ClassroomConfig:
    """Enterprise Configuration parameters for Classroom Surveillance."""

    # ---- Model & Perception Settings ----
    model_path: str = "models/classroom_best.pt"
    fallback_model: str = "yolov8n.pt"
    confidence_threshold: float = 0.45
    nms_iou_threshold: float = 0.45
    default_fps: float = 30.0
    input_resolution: int = 640

    # ---- Spatial Matching & Person Tracking ----
    iou_match_threshold: float = 0.30
    max_missing_frames: int = 12

    # ---- Anti-Flickering Score Accumulator ----
    score_window_size: int = 45          # ~1.5 seconds at 30 fps
    score_threshold: float = 4.5         # Minimum cumulative score to trigger suspicious
    cheating_ratio_threshold: float = 0.55 # >=55% of frames in window indicate cheating
    min_frames_in_window: int = 12       # Minimum frames (~0.4s) before deciding
    normal_penalty: float = -0.3         # Mild penalty for normal frames to absorb flicker

    # ---- Behavior State Machine & Cooldown ----
    cooldown_seconds: float = 5.0
    escalation_duration_seconds: float = 5.0 # After 5s continuous, escalate MEDIUM -> HIGH

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

    # ---- Evidence Storage ----
    evidence_dir: str = "data/evidence"
    evidence_quality: int = 92 # JPEG quality

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
            "iou_match_threshold": self.iou_match_threshold,
            "max_missing_frames": self.max_missing_frames,
            "score_window_size": self.score_window_size,
            "score_threshold": self.score_threshold,
            "cheating_ratio_threshold": self.cheating_ratio_threshold,
            "cooldown_seconds": self.cooldown_seconds,
            "escalation_duration_seconds": self.escalation_duration_seconds,
            "collective_suppress_ratio": self.collective_suppress_ratio,
            "class_names": self.class_names,
            "severity_map": self.severity_map,
        }


# Global default instance
DEFAULT_CONFIG = ClassroomConfig()
