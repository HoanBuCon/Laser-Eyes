"""Head Orientation Provider Abstraction and Pose-Heuristic Implementation.

Implements Scope 03 (FR-PER-003, FR-PER-004) and Scope 06 of SRS v2.0:
- Pluggable HeadOrientationProvider interface.
- Unknown-safe keypoint confidence and quality gating.
- Seat-perspective relative yaw baseline subtraction.
"""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger("HeadPoseProvider")


@dataclass
class HeadOrientationEstimate:
    """Standardized estimate of head 3D orientation and observation quality."""

    yaw: Optional[float] = None       # Degrees: negative = left, positive = right
    pitch: Optional[float] = None     # Degrees: positive = downward tilt
    roll: Optional[float] = None      # Degrees: lateral tilt
    quality: float = 0.0              # 0.0 (occluded/blurry) to 1.0 (crystal clear)
    source: str = "unknown"           # "pose_heuristic", "pretrained_hpe", "unknown"

    @property
    def is_valid(self) -> bool:
        return self.yaw is not None and self.pitch is not None and self.quality > 0.20

    def to_dict(self) -> dict:
        return {
            "yaw": round(self.yaw, 2) if self.yaw is not None else None,
            "pitch": round(self.pitch, 2) if self.pitch is not None else None,
            "roll": round(self.roll, 2) if self.roll is not None else None,
            "quality": round(self.quality, 3),
            "source": self.source,
        }


class HeadOrientationProvider(abc.ABC):
    """Abstract interface for all head pose estimation providers."""

    @abc.abstractmethod
    def estimate(
        self,
        keypoints: np.ndarray,
        bbox: Optional[Tuple[float, float, float, float]] = None,
        seat_baseline_yaw: float = 0.0,
        seat_baseline_pitch: float = 0.0,
    ) -> HeadOrientationEstimate:
        """Estimate 3D head yaw and pitch relative to the seat's neutral perspective."""
        pass


class PoseHeuristicHeadOrientationProvider(HeadOrientationProvider):
    """Zero-shot 2D keypoint geometric head orientation provider."""

    def __init__(self, min_kp_conf: float = 0.30, min_quality: float = 0.25):
        self.min_kp_conf = min_kp_conf
        self.min_quality = min_quality

    def estimate(
        self,
        keypoints: np.ndarray,
        bbox: Optional[Tuple[float, float, float, float]] = None,
        seat_baseline_yaw: float = 0.0,
        seat_baseline_pitch: float = 0.0,
    ) -> HeadOrientationEstimate:
        if keypoints is None or len(keypoints) < 7:
            return HeadOrientationEstimate(source="unknown", quality=0.0)

        nose = keypoints[0]
        l_eye = keypoints[1]
        r_eye = keypoints[2]
        l_ear = keypoints[3]
        r_ear = keypoints[4]
        ls = keypoints[5]
        rs = keypoints[6]

        head_confs = [nose[2], l_eye[2], r_eye[2], l_ear[2], r_ear[2], ls[2], rs[2]]
        valid_confs = [c for c in head_confs if c > 0]
        quality = float(np.mean(valid_confs)) if valid_confs else 0.0

        # Quality gate: if nose is missing or head is too noisy
        if nose[2] < self.min_kp_conf or quality < self.min_quality:
            return HeadOrientationEstimate(quality=quality, source="unknown")

        raw_yaw: Optional[float] = None
        raw_pitch: Optional[float] = None

        # 1. Compute Horizontal Yaw
        if l_ear[2] >= self.min_kp_conf and r_ear[2] >= self.min_kp_conf:
            ear_mid_x = (l_ear[0] + r_ear[0]) / 2.0
            ear_dist = max(5.0, float(np.linalg.norm(l_ear[:2] - r_ear[:2])))
            yaw_ratio = (nose[0] - ear_mid_x) / (ear_dist / 2.0)
            raw_yaw = float(np.clip(yaw_ratio * 45.0, -90.0, 90.0))
        elif l_eye[2] >= self.min_kp_conf and r_eye[2] >= self.min_kp_conf:
            eye_mid_x = (l_eye[0] + r_eye[0]) / 2.0
            eye_dist = max(5.0, float(np.linalg.norm(l_eye[:2] - r_eye[:2])))
            yaw_ratio = (nose[0] - eye_mid_x) / (eye_dist / 2.0)
            raw_yaw = float(np.clip(yaw_ratio * 45.0, -90.0, 90.0))
        elif l_ear[2] >= self.min_kp_conf and nose[2] >= self.min_kp_conf:
            # Only left ear visible -> facing right
            raw_yaw = 35.0
        elif r_ear[2] >= self.min_kp_conf and nose[2] >= self.min_kp_conf:
            # Only right ear visible -> facing left
            raw_yaw = -35.0

        # 2. Compute Vertical Pitch
        if ls[2] >= self.min_kp_conf and rs[2] >= self.min_kp_conf:
            shoulder_mid_y = (ls[1] + rs[1]) / 2.0
            shoulder_width = max(10.0, float(np.linalg.norm(ls[:2] - rs[:2])))
            nose_to_shoulder_ratio = (shoulder_mid_y - nose[1]) / shoulder_width
            raw_pitch = float(np.clip((0.65 - nose_to_shoulder_ratio) * 60.0, -45.0, 60.0))
        elif (l_ear[2] >= self.min_kp_conf or r_ear[2] >= self.min_kp_conf) and nose[2] >= self.min_kp_conf:
            ref_ear_y = l_ear[1] if l_ear[2] >= self.min_kp_conf else r_ear[1]
            raw_pitch = float(np.clip((nose[1] - ref_ear_y) * 1.5, -45.0, 60.0))

        if raw_yaw is None or raw_pitch is None:
            return HeadOrientationEstimate(
                yaw=raw_yaw,
                pitch=raw_pitch,
                quality=quality,
                source="pose_heuristic_partial",
            )

        # Subtract Seat Baseline perspective offset
        relative_yaw = float(np.clip(raw_yaw - seat_baseline_yaw, -90.0, 90.0))
        relative_pitch = float(np.clip(raw_pitch - seat_baseline_pitch, -45.0, 60.0))

        return HeadOrientationEstimate(
            yaw=relative_yaw,
            pitch=relative_pitch,
            roll=0.0,
            quality=quality,
            source="pose_heuristic",
        )
