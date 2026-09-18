"""Raw Observation Layer Extractor with Unknown-Safe & Context-Aware Logic.

Implements Scope 06 (FR-OBSERV-001 to FR-OBSERV-005) of SRS v2.0:
- Extracts verifiable, explainable raw observations from person perception.
- Relative baseline subtraction for head yaw/pitch per Seat perspective.
- Mandatory Writing Zone suppression: Wrists in writing zone NEVER classify as UNDER_DESK.
- Unknown-safe keypoint failure handling (missing keypoints -> UNKNOWN, never false normal/cheating).
- Capability gating compliance: If desk geometry is missing, hand interaction is DISABLED/UNKNOWN.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from classroom_monitor.head_pose_provider import HeadOrientationEstimate, HeadOrientationProvider, PoseHeuristicHeadOrientationProvider
from classroom_monitor.models import Detection
from classroom_monitor.scene_context import CapabilityStatus, DeskGeometry, SeatContext

logger = logging.getLogger("ObservationExtractor")


class ObservationType(str, Enum):
    HEAD_YAW_RELATIVE = "HEAD_YAW_RELATIVE"
    HEAD_PITCH_RELATIVE_DOWN = "HEAD_PITCH_RELATIVE_DOWN"
    TORSO_LEAN_X = "TORSO_LEAN_X"
    TORSO_ORIENTATION = "TORSO_ORIENTATION"
    LEFT_WRIST_ZONE = "LEFT_WRIST_ZONE"
    RIGHT_WRIST_ZONE = "RIGHT_WRIST_ZONE"
    WRIST_VELOCITY = "WRIST_VELOCITY"
    SEAT_OCCUPANCY = "SEAT_OCCUPANCY"
    PERSON_COUNT_NEAR_SEAT = "PERSON_COUNT_NEAR_SEAT"


class WristZone(str, Enum):
    WRITING = "WRITING"
    DESK_EDGE = "DESK_EDGE"
    UNDER_DESK = "UNDER_DESK"
    UNKNOWN = "UNKNOWN"


@dataclass
class RawObservation:
    """Standardized observable atomic measurement for a Seat Actor at a specific timestamp."""

    seat_id: str
    timestamp_ms: float
    observation_type: str
    value: Any
    quality: float = 1.0       # 0.0 to 1.0 observation clarity
    confidence: float = 1.0    # Detector / Keypoint confidence
    source: str = "perception"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "seat_id": self.seat_id,
            "timestamp_ms": self.timestamp_ms,
            "observation_type": self.observation_type,
            "value": self.value,
            "quality": round(self.quality, 3),
            "confidence": round(self.confidence, 3),
            "source": self.source,
            "metadata": self.metadata,
        }


class ObservationExtractor:
    """Extracts raw atomic observations for each individual Seat Actor."""

    def __init__(
        self,
        head_pose_provider: Optional[HeadOrientationProvider] = None,
        min_keypoint_conf: float = 0.30,
        lean_angle_threshold: float = 18.0,
    ):
        self.head_pose_provider = head_pose_provider or PoseHeuristicHeadOrientationProvider(min_kp_conf=min_keypoint_conf)
        self.min_kp_conf = min_keypoint_conf
        self.lean_angle_threshold = lean_angle_threshold
        self._wrist_histories: Dict[str, List[Tuple[float, float, float]]] = {}  # seat_id -> [(x, y, ts_ms)]

    def extract(
        self,
        detection: Optional[Detection],
        seat_context: SeatContext,
        timestamp_ms: float,
        nearby_person_count: Optional[int] = None,
        occupancy_state: Optional[str] = None,
        frame: Optional[np.ndarray] = None,
    ) -> List[RawObservation]:
        """Extract all valid observations for the candidate person in the given seat context."""
        observations: List[RawObservation] = []
        seat_id = seat_context.seat_id

        # Determine effective occupancy state
        if occupancy_state is None:
            eff_occupancy = "OCCUPIED" if detection is not None else "EMPTY"
        else:
            eff_occupancy = str(occupancy_state).upper()

        # Determine effective nearby person count
        if nearby_person_count is None:
            eff_person_count = 1 if detection is not None else 0
        else:
            eff_person_count = int(nearby_person_count)

        # 1. Occupancy & Person Count Observation
        if detection is None:
            observations.append(
                RawObservation(
                    seat_id=seat_id,
                    timestamp_ms=timestamp_ms,
                    observation_type=ObservationType.SEAT_OCCUPANCY.value,
                    value=eff_occupancy,
                    quality=1.0,
                    confidence=1.0,
                    source="seat_mapping",
                )
            )
            observations.append(
                RawObservation(
                    seat_id=seat_id,
                    timestamp_ms=timestamp_ms,
                    observation_type=ObservationType.PERSON_COUNT_NEAR_SEAT.value,
                    value=eff_person_count,
                    quality=1.0,
                    confidence=1.0,
                    source="seat_mapping",
                )
            )
            return observations

        observations.append(
            RawObservation(
                seat_id=seat_id,
                timestamp_ms=timestamp_ms,
                observation_type=ObservationType.SEAT_OCCUPANCY.value,
                value=eff_occupancy,
                quality=detection.confidence,
                confidence=detection.confidence,
                source="seat_mapping",
            )
        )

        observations.append(
            RawObservation(
                seat_id=seat_id,
                timestamp_ms=timestamp_ms,
                observation_type=ObservationType.PERSON_COUNT_NEAR_SEAT.value,
                value=eff_person_count,
                quality=1.0,
                confidence=detection.confidence,
                source="seat_mapping",
            )
        )

        keypoints = detection.keypoints
        if keypoints is None or len(keypoints) < 11:
            return observations

        # 2. Head Orientation Observations
        if seat_context.capabilities.head_orientation != CapabilityStatus.DISABLED:
            head_est = self.head_pose_provider.estimate(
                keypoints=keypoints,
                bbox=detection.bbox,
                seat_baseline_yaw=seat_context.reference_directions.baseline_yaw,
                seat_baseline_pitch=seat_context.reference_directions.baseline_pitch,
                frame=frame,
            )

            if head_est.yaw is not None:
                observations.append(
                    RawObservation(
                        seat_id=seat_id,
                        timestamp_ms=timestamp_ms,
                        observation_type=ObservationType.HEAD_YAW_RELATIVE.value,
                        value=head_est.yaw,
                        quality=head_est.quality,
                        confidence=detection.confidence,
                        source=head_est.source,
                    )
                )

            if head_est.pitch is not None:
                observations.append(
                    RawObservation(
                        seat_id=seat_id,
                        timestamp_ms=timestamp_ms,
                        observation_type=ObservationType.HEAD_PITCH_RELATIVE_DOWN.value,
                        value=head_est.pitch,
                        quality=head_est.quality,
                        confidence=detection.confidence,
                        source=head_est.source,
                    )
                )

        # 3. Torso Lean Observation
        if seat_context.capabilities.body_lean != CapabilityStatus.DISABLED:
            ls = keypoints[5]  # Left shoulder
            rs = keypoints[6]  # Right shoulder
            if ls[2] >= self.min_kp_conf and rs[2] >= self.min_kp_conf:
                dx = float(rs[0] - ls[0])
                dy = float(rs[1] - ls[1])
                torso_angle = float(np.degrees(np.arctan2(dy, max(1.0, abs(dx)))))
                shoulder_quality = float((ls[2] + rs[2]) / 2.0)

                observations.append(
                    RawObservation(
                        seat_id=seat_id,
                        timestamp_ms=timestamp_ms,
                        observation_type=ObservationType.TORSO_LEAN_X.value,
                        value=torso_angle,
                        quality=shoulder_quality,
                        confidence=detection.confidence,
                        source="pose_torso",
                    )
                )

        # 4. Wrist Zone & Trajectory Observations
        lw = keypoints[9]   # Left wrist
        rw = keypoints[10]  # Right wrist
        desk_geo = seat_context.desk_geometry

        # Evaluate Left Wrist
        lw_zone = self._evaluate_wrist_zone(lw, desk_geo, seat_context.capabilities.desk_hand_interaction)
        observations.append(
            RawObservation(
                seat_id=seat_id,
                timestamp_ms=timestamp_ms,
                observation_type=ObservationType.LEFT_WRIST_ZONE.value,
                value=lw_zone.value,
                quality=float(lw[2]),
                confidence=float(lw[2]),
                source="desk_geometry_matcher",
            )
        )

        # Evaluate Right Wrist
        rw_zone = self._evaluate_wrist_zone(rw, desk_geo, seat_context.capabilities.desk_hand_interaction)
        observations.append(
            RawObservation(
                seat_id=seat_id,
                timestamp_ms=timestamp_ms,
                observation_type=ObservationType.RIGHT_WRIST_ZONE.value,
                value=rw_zone.value,
                quality=float(rw[2]),
                confidence=float(rw[2]),
                source="desk_geometry_matcher",
            )
        )

        # Track Wrist Velocity
        if rw[2] >= self.min_kp_conf or lw[2] >= self.min_kp_conf:
            active_wrist = rw if rw[2] >= lw[2] else lw
            velocity = self._compute_wrist_velocity(seat_id, active_wrist[0], active_wrist[1], timestamp_ms)
            observations.append(
                RawObservation(
                    seat_id=seat_id,
                    timestamp_ms=timestamp_ms,
                    observation_type=ObservationType.WRIST_VELOCITY.value,
                    value=velocity,
                    quality=float(active_wrist[2]),
                    confidence=float(active_wrist[2]),
                    source="wrist_kinematics",
                )
            )

        return observations

    def _evaluate_wrist_zone(
        self,
        wrist_kp: np.ndarray,
        desk_geo: Optional[DeskGeometry],
        capability: CapabilityStatus,
    ) -> WristZone:
        """Unknown-Safe Wrist Zone Classifier enforcing Mandatory Writing Zone Suppression."""
        if wrist_kp[2] < self.min_kp_conf:
            return WristZone.UNKNOWN

        if capability == CapabilityStatus.DISABLED or desk_geo is None:
            return WristZone.UNKNOWN

        pt = (float(wrist_kp[0]), float(wrist_kp[1]))

        # Priority 1: Writing Zone check (Mandatory Suppression)
        if desk_geo.contains_wrist_in_writing_zone(pt):
            return WristZone.WRITING

        # Priority 2: Under Desk check
        if desk_geo.is_wrist_below_desk(pt):
            return WristZone.UNDER_DESK

        return WristZone.DESK_EDGE

    def _compute_wrist_velocity(self, seat_id: str, x: float, y: float, ts_ms: float) -> float:
        if seat_id not in self._wrist_histories:
            self._wrist_histories[seat_id] = []
        hist = self._wrist_histories[seat_id]
        hist.append((x, y, ts_ms))

        # Keep history within last 1000ms
        while hist and (ts_ms - hist[0][2]) > 1000.0:
            hist.pop(0)

        if len(hist) < 2:
            return 0.0

        prev_x, prev_y, prev_ts = hist[0]
        dt = max(0.05, (ts_ms - prev_ts) / 1000.0)
        dist = float(np.hypot(x - prev_x, y - prev_y))
        return float(dist / dt)  # Pixels per second
