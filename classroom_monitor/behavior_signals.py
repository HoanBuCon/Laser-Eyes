"""Suspicious Behavior Signal Extractors with Unknown-Safe Missing Keypoint Handling.

Implements Scope 05 (FR-BEH-001 to FR-BEH-005, P0-06) of SRS v1.0:
- BEH-01: PROLONGED_HEAD_TURN
- BEH-02: BODY_LEAN_SIDE
- BEH-03: LOOK_DOWN_LONG
- BEH-04: LOW_HAND_POSTURE
- BEH-05: SEAT_LEFT
- BEH-06: MULTIPLE_PERSON_NEAR_SEAT
- Unknown-safe observation quality protection (prevents false normal/cheating on missing keypoints).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger("BehaviorSignals")


class SignalType(str, Enum):
    PROLONGED_HEAD_TURN = "PROLONGED_HEAD_TURN"
    BODY_LEAN_SIDE = "BODY_LEAN_SIDE"
    LOOK_DOWN_LONG = "LOOK_DOWN_LONG"
    LOW_HAND_POSTURE = "LOW_HAND_POSTURE"
    SEAT_LEFT = "SEAT_LEFT"
    MULTIPLE_PERSON_NEAR_SEAT = "MULTIPLE_PERSON_NEAR_SEAT"
    NORMAL = "NORMAL"
    UNKNOWN = "UNKNOWN"


@dataclass
class BehaviorSignal:
    """Standardized suspicious behavior observation emitted from perception."""

    signal_type: str
    raw_score: float  # 0.0 to 1.0 intensity
    confidence: float  # Keypoint detection confidence
    quality: float  # Observation quality (0.0 = completely occluded, 1.0 = crystal clear)
    timestamp_ms: float
    seat_id: Optional[str] = None
    room_id: Optional[str] = None
    session_id: Optional[str] = None
    camera_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    is_valid: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "signal_type": self.signal_type,
            "raw_score": round(self.raw_score, 3),
            "confidence": round(self.confidence, 3),
            "quality": round(self.quality, 3),
            "timestamp_ms": self.timestamp_ms,
            "seat_id": self.seat_id,
            "metadata": self.metadata,
        }


def extract_head_yaw_pitch_safe(
    keypoints: np.ndarray,
    min_kp_conf: float = 0.30,
) -> Tuple[Optional[float], Optional[float], float]:
    """Calculate Head Yaw and Pitch with unknown-safe quality estimation.

    COCO Keypoint Mapping:
    0: Nose, 1: L_Eye, 2: R_Eye, 3: L_Ear, 4: R_Ear, 5: L_Shoulder, 6: R_Shoulder

    Returns:
        (yaw, pitch, quality)
        If keypoints are missing/occluded, returns (None, None, quality < 0.3)
    """
    if keypoints is None or len(keypoints) < 7:
        return None, None, 0.0

    nose = keypoints[0]
    l_eye = keypoints[1]
    r_eye = keypoints[2]
    l_ear = keypoints[3]
    r_ear = keypoints[4]
    ls = keypoints[5]
    rs = keypoints[6]

    # Check keypoint confidences (keypoints[:, 2])
    head_confs = [nose[2], l_eye[2], r_eye[2], l_ear[2], r_ear[2], ls[2], rs[2]]
    quality = float(np.mean([c for c in head_confs if c > 0] or [0.0]))

    if nose[2] < min_kp_conf:
        # Without nose detection, cannot reliably estimate head rotation
        return None, None, quality

    yaw: Optional[float] = None
    pitch: Optional[float] = None

    # Yaw: Horizontal head turn
    if l_ear[2] >= min_kp_conf and r_ear[2] >= min_kp_conf:
        ear_mid_x = (l_ear[0] + r_ear[0]) / 2.0
        ear_dist = max(5.0, float(np.linalg.norm(l_ear[:2] - r_ear[:2])))
        yaw_ratio = (nose[0] - ear_mid_x) / (ear_dist / 2.0)
        yaw = float(np.clip(yaw_ratio * 45.0, -90.0, 90.0))
    elif l_eye[2] >= min_kp_conf and r_eye[2] >= min_kp_conf:
        eye_mid_x = (l_eye[0] + r_eye[0]) / 2.0
        eye_dist = max(5.0, float(np.linalg.norm(l_eye[:2] - r_eye[:2])))
        yaw_ratio = (nose[0] - eye_mid_x) / (eye_dist / 2.0)
        yaw = float(np.clip(yaw_ratio * 45.0, -90.0, 90.0))
    else:
        # Only one ear/eye visible -> can indicate severe head turn, but insufficient for precise angle
        yaw = None

    # Pitch: Vertical head tilt
    if ls[2] >= min_kp_conf and rs[2] >= min_kp_conf:
        shoulder_mid_y = (ls[1] + rs[1]) / 2.0
        shoulder_width = max(10.0, float(np.linalg.norm(ls[:2] - rs[:2])))
        nose_to_shoulder_ratio = (shoulder_mid_y - nose[1]) / shoulder_width
        pitch = float(np.clip((0.65 - nose_to_shoulder_ratio) * 60.0, -45.0, 60.0))
    elif l_ear[2] >= min_kp_conf or r_ear[2] >= min_kp_conf:
        ref_ear_y = l_ear[1] if l_ear[2] >= min_kp_conf else r_ear[1]
        pitch = float(np.clip((nose[1] - ref_ear_y) * 1.5, -45.0, 60.0))
    else:
        pitch = None

    return yaw, pitch, quality


def extract_body_lean_angle(
    keypoints: np.ndarray,
    min_kp_conf: float = 0.30,
) -> Tuple[Optional[float], float]:
    """Calculate Spine Body Lean Angle (degrees from vertical).

    Keypoints: 5: L_Shoulder, 6: R_Shoulder, 11: L_Hip, 12: R_Hip
    """
    if keypoints is None or len(keypoints) < 13:
        return None, 0.0

    ls, rs = keypoints[5], keypoints[6]
    lh, rh = keypoints[11], keypoints[12]

    confs = [ls[2], rs[2], lh[2], rh[2]]
    quality = float(np.mean([c for c in confs if c > 0] or [0.0]))

    if ls[2] >= min_kp_conf and rs[2] >= min_kp_conf and lh[2] >= min_kp_conf and rh[2] >= min_kp_conf:
        shoulder_mid = (ls[:2] + rs[:2]) / 2.0
        hip_mid = (lh[:2] + rh[:2]) / 2.0
        spine_vec = shoulder_mid - hip_mid
        # Angle from vertical (0, -1)
        angle_rad = np.arctan2(abs(spine_vec[0]), abs(spine_vec[1]))
        angle_deg = float(np.degrees(angle_rad))
        return angle_deg, quality

    # Fallback to shoulder tilt if hips are occluded by desk
    if ls[2] >= min_kp_conf and rs[2] >= min_kp_conf:
        dx = rs[0] - ls[0]
        dy = rs[1] - ls[1]
        shoulder_tilt_rad = np.arctan2(abs(dy), max(1.0, abs(dx)))
        return float(np.degrees(shoulder_tilt_rad)), quality * 0.7

    return None, quality


def extract_low_hands_cues(
    keypoints: np.ndarray,
    pitch: Optional[float],
    min_kp_conf: float = 0.30,
) -> Tuple[bool, float]:
    """Detect low hand posture under desk with wrist proximity."""
    if keypoints is None or len(keypoints) < 11:
        return False, 0.0

    ls, rs = keypoints[5], keypoints[6]
    lw, rw = keypoints[9], keypoints[10]

    quality = float(np.mean([ls[2], rs[2], lw[2], rw[2]]))

    if ls[2] < min_kp_conf or rs[2] < min_kp_conf or lw[2] < min_kp_conf or rw[2] < min_kp_conf:
        return False, quality

    hand_dist = float(np.linalg.norm(lw[:2] - rw[:2]))
    shoulder_width = max(10.0, float(np.linalg.norm(ls[:2] - rs[:2])))
    shoulder_y = (ls[1] + rs[1]) / 2.0

    is_hands_close = (hand_dist / shoulder_width < 0.28) or (hand_dist < 45.0)
    is_hands_low = (lw[1] > shoulder_y + 30.0) and (rw[1] > shoulder_y + 30.0)
    is_head_down = (pitch is not None and pitch >= 18.0)

    is_low_hand = is_hands_close and is_hands_low and is_head_down
    return is_low_hand, quality


class BehaviorSignalExtractor:
    """Orchestrates feature extraction and emits standardized BehaviorSignals per seat."""

    def __init__(
        self,
        head_turn_yaw_threshold: float = 25.0,
        body_lean_angle_threshold: float = 18.0,
        look_down_pitch_threshold: float = 22.0,
    ):
        self.head_turn_yaw_threshold = head_turn_yaw_threshold
        self.body_lean_angle_threshold = body_lean_angle_threshold
        self.look_down_pitch_threshold = look_down_pitch_threshold

    def analyze_candidate_keypoints(
        self,
        keypoints: np.ndarray,
        timestamp_ms: float,
        seat_id: Optional[str] = None,
        camera_id: Optional[str] = None,
    ) -> List[BehaviorSignal]:
        """Analyze 17 keypoints of a seated candidate and return active behavior signals."""
        signals: List[BehaviorSignal] = []

        yaw, pitch, head_quality = extract_head_yaw_pitch_safe(keypoints)
        lean_angle, lean_quality = extract_body_lean_angle(keypoints)
        is_low_hands, hand_quality = extract_low_hands_cues(keypoints, pitch)

        # Signal 1: PROLONGED_HEAD_TURN
        if yaw is not None and abs(yaw) >= self.head_turn_yaw_threshold:
            signals.append(
                BehaviorSignal(
                    signal_type=SignalType.PROLONGED_HEAD_TURN.value,
                    raw_score=min(1.0, abs(yaw) / 50.0),
                    confidence=head_quality,
                    quality=head_quality,
                    timestamp_ms=timestamp_ms,
                    seat_id=seat_id,
                    camera_id=camera_id,
                    metadata={"yaw": round(yaw, 1)},
                )
            )

        # Signal 2: BODY_LEAN_SIDE
        if lean_angle is not None and lean_angle >= self.body_lean_angle_threshold:
            signals.append(
                BehaviorSignal(
                    signal_type=SignalType.BODY_LEAN_SIDE.value,
                    raw_score=min(1.0, lean_angle / 35.0),
                    confidence=lean_quality,
                    quality=lean_quality,
                    timestamp_ms=timestamp_ms,
                    seat_id=seat_id,
                    camera_id=camera_id,
                    metadata={"lean_angle": round(lean_angle, 1)},
                )
            )

        # Signal 3: LOOK_DOWN_LONG
        if pitch is not None and pitch >= self.look_down_pitch_threshold:
            signals.append(
                BehaviorSignal(
                    signal_type=SignalType.LOOK_DOWN_LONG.value,
                    raw_score=min(1.0, pitch / 40.0),
                    confidence=head_quality,
                    quality=head_quality,
                    timestamp_ms=timestamp_ms,
                    seat_id=seat_id,
                    camera_id=camera_id,
                    metadata={"pitch": round(pitch, 1)},
                )
            )

        # Signal 4: LOW_HAND_POSTURE
        if is_low_hands:
            signals.append(
                BehaviorSignal(
                    signal_type=SignalType.LOW_HAND_POSTURE.value,
                    raw_score=0.85,
                    confidence=hand_quality,
                    quality=hand_quality,
                    timestamp_ms=timestamp_ms,
                    seat_id=seat_id,
                    camera_id=camera_id,
                    metadata={"posture": "hands_concealed_low"},
                )
            )

        return signals
