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
    phone_wrist_ratio_threshold: float = 0.28
    head_provider: str = "pose_heuristic"  # "pose_heuristic" (default) or "sixdrepnet"
    head_hpe_hz: float = 5.0              # Scheduled HPE update frequency (Hz)
    head_estimate_max_age_ms: float = 600.0 # Maximum age before cached estimate expires to UNKNOWN
    head_min_crop_size: int = 24          # Minimum crop width/height in pixels
    head_min_quality: float = 0.35        # Quality threshold below which head pose is marked UNKNOWN
    head_median_window: int = 3           # Temporal median filter window size (samples)
    head_merge_gap_ms: float = 500.0      # Maximum gap in ms to merge noise-induced fragmented episodes
    head_yaw_activation_deg: float = 32.0 # Minimum relative yaw to activate head turn episode
    head_yaw_release_deg: float = 16.0    # Release threshold for head turn episode
    head_min_persistence_ms: float = 500.0 # Minimum persistence before candidate becomes active episode

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
    recidivism_window_seconds: float = 15.0  # Window for recidivism escalation
    recidivism_score_threshold: float = 3.0  # Lower threshold for instant recidivism trigger
    decay_rate_per_sec: float = 8.0          # Risk score decay rate per second when no signals
    require_human_review: bool = True        # Human-in-the-Loop decision support model

    # ---- Behavior Signal Thresholds (v1.1) ----
    head_turn_yaw_threshold: float = 35.0
    body_lean_angle_threshold: float = 18.0
    look_down_pitch_threshold: float = 40.0
    hand_motion_threshold: float = 6.0
    under_desk_min_duration_ms: float = 1000.0
    composite_suppression: bool = True  # Suppress component signals when composite is active
    
    # ---- Risk Weights (Base rate per second - v1.1 Taxonomy) ----
    risk_weights: Dict[str, float] = field(
        default_factory=lambda: {
            "PROLONGED_HEAD_TURN": 18.0,
            "BODY_LEAN_SIDE": 15.0,
            "SUSPICIOUS_BELOW_DESK_ACTIVITY": 24.0, # P0 Composite Suspicious Signal
            "LOOK_DOWN_LONG": 1.0,                 # Context observation (very low)
            "LOW_HAND_POSTURE": 3.0,               # Context observation
            "MULTIPLE_PERSON_NEAR_SEAT": 20.0,
        }
    )

    # ---- Contextual Signal Combinations (Bonus rate per second when signals co-occur) ----
    combination_weights: Dict[str, float] = field(
        default_factory=lambda: {
            "PROLONGED_HEAD_TURN+BODY_LEAN_SIDE": 18.0,   # Active side peeking at peer's paper
            "PROLONGED_HEAD_TURN+LOW_HAND_POSTURE": 15.0, # Multi-cue cheating posture
            "BODY_LEAN_SIDE+LOW_HAND_POSTURE": 15.0,      # Leaning with concealed hands
        }
    )

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
    suspicious_classes: Set[str] = field(
        default_factory=lambda: {
            "back peeking",
            "front peeking",
            "phone using",
            "side peeking",
        }
    )
    normal_classes: Set[str] = field(default_factory=lambda: {"no cheating"})

    @property
    def cheating_classes(self) -> Set[str]:
        """Backward compatibility for legacy references."""
        return self.suspicious_classes

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


def resolve_runtime_config(
    scene_profile: Optional[Any] = None,
    demo_config: Optional[Any] = None,
    base_config: Optional[ClassroomConfig] = None,
) -> Dict[str, Dict[str, Any]]:
    """Resolve single source of truth for runtime execution parameters.

    Precedence: Scene Profile explicit override > DemoVideoConfig / ClassroomConfig > Engine Defaults.
    """
    base = base_config or DEFAULT_CONFIG

    # Extract raw scene overrides if present
    scene_raw = scene_profile.raw_config if (scene_profile and hasattr(scene_profile, "raw_config")) else {}
    scene_thresholds = scene_raw.get("thresholds", {}) if isinstance(scene_raw, dict) else {}

    # 1. Head Pose & HPE
    hpe_hz = getattr(demo_config, "hpe_hz", None) or scene_thresholds.get("hpe_hz", base.head_hpe_hz)
    provider = getattr(demo_config, "head_provider", None) or scene_thresholds.get("head_provider", base.head_provider)
    head_cfg = {
        "provider": provider,
        "hpe_hz": float(hpe_hz),
        "cache_max_age_ms": float(scene_thresholds.get("cache_max_age_ms", base.head_estimate_max_age_ms)),
        "min_crop_size": int(scene_thresholds.get("min_crop_size", base.head_min_crop_size)),
        "min_quality": float(scene_thresholds.get("min_quality", base.head_min_quality)),
    }

    # 2. Temporal Episodes
    temp_scene = scene_thresholds.get("temporal", {}) if isinstance(scene_thresholds, dict) else {}
    temporal_cfg = {
        "min_persistence_ms": float(temp_scene.get("min_persistence_ms", 400.0)),
        "release_hysteresis_ms": float(temp_scene.get("release_hysteresis_ms", 350.0)),
        "missing_observation_grace_ms": float(temp_scene.get("missing_observation_grace_ms", 1200.0)),
        "yaw_activation_deg": float(temp_scene.get("yaw_activation_deg", 28.0)),
        "yaw_release_deg": float(temp_scene.get("yaw_release_deg", 16.0)),
        "lean_activation_deg": float(temp_scene.get("lean_activation_deg", 15.0)),
        "lean_release_deg": float(temp_scene.get("lean_release_deg", 8.0)),
        "pitch_down_activation_deg": float(temp_scene.get("pitch_down_activation_deg", 20.0)),
    }

    # 3. Behavior Patterns
    pat_scene = scene_thresholds.get("patterns", {}) if isinstance(scene_thresholds, dict) else {}
    pattern_cfg = {
        "glance_rolling_window_ms": float(pat_scene.get("glance_rolling_window_ms", 25000.0)),
        "min_glance_episodes": int(pat_scene.get("min_glance_episodes", 2)),
        "lean_min_duration_ms": float(pat_scene.get("lean_min_duration_ms", 1000.0)),
        "seat_left_timeout_ms": float(pat_scene.get("seat_left_timeout_ms", 15000.0)),
        "multi_person_dwell_ms": float(pat_scene.get("multi_person_dwell_ms", 2500.0)),
        "below_desk_min_duration_ms": float(pat_scene.get("below_desk_min_duration_ms", 1500.0)),
    }

    # 4. Seat Risk Tracker
    risk_scene = scene_thresholds.get("risk", {}) if isinstance(scene_thresholds, dict) else {}
    risk_cfg = {
        "observe_threshold": float(risk_scene.get("observe_threshold", 30.0)),
        "suspicious_threshold": float(risk_scene.get("suspicious_threshold", 60.0)),
        "flagged_threshold": float(risk_scene.get("flagged_threshold", 80.0)),
        "post_event_reset_score": float(risk_scene.get("post_event_reset_score", 45.0)),
        "decay_rate_per_sec": float(risk_scene.get("decay_rate_per_sec", 2.5)),
        "cooldown_duration_ms": float(risk_scene.get("cooldown_duration_ms", 5000.0)),
        "recidivism_window_ms": float(risk_scene.get("recidivism_window_ms", 15000.0)),
        "incident_merge_window_ms": float(risk_scene.get("incident_merge_window_ms", 15000.0)),
    }

    return {
        "head_pose": head_cfg,
        "temporal": temporal_cfg,
        "patterns": pattern_cfg,
        "risk": risk_cfg,
    }
