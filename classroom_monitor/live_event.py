"""Active Behavioral Event Lifecycle Management.

Tracks ongoing events across frames, identifies the optimal peak-confidence
frame for evidence capture, flags for human review, and manages automatic severity escalation.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from classroom_monitor.config import DEFAULT_CONFIG, ClassroomConfig
from classroom_monitor.models import ClassroomEvent, EventStatus, SeverityLevel


class LiveEvent:
    """Represents an ongoing violation episode with continuous state updates."""

    def __init__(
        self,
        track_id: int,
        behavior: str,
        start_frame: int,
        confidence: float,
        bbox: Tuple[int, int, int, int],
        fps: float = 30.0,
        config: Optional[ClassroomConfig] = None,
        timestamp_ms: Optional[float] = None,
        is_recidivist: bool = False,
    ):
        self.config = config or DEFAULT_CONFIG
        self.event_id: str = f"EVT-{uuid.uuid4().hex[:8].upper()}"
        self.track_id: int = track_id
        self.behavior: str = behavior
        self.start_frame: int = start_frame
        self.end_frame: int = start_frame
        self.fps: float = max(1.0, fps)
        self.is_recidivist: bool = is_recidivist
        self.requires_human_review: bool = self.config.require_human_review

        self.start_timestamp_ms: Optional[float] = timestamp_ms
        self.end_timestamp_ms: Optional[float] = timestamp_ms

        self.confidences: List[float] = [float(confidence)]
        self.peak_confidence: float = float(confidence)
        self.peak_frame_idx: int = start_frame
        self.peak_bbox: Tuple[int, int, int, int] = bbox
        self.latest_bbox: Tuple[int, int, int, int] = bbox

        # Base severity based on behavior class or recidivism
        if self.is_recidivist:
            self.severity: str = SeverityLevel.HIGH.value
        else:
            self.severity = self.config.severity_map.get(
                behavior, SeverityLevel.MEDIUM.value
            )

        self.status: str = EventStatus.SUSPICIOUS.value
        self.is_closed: bool = False
        self.created_at: float = time.time()
        self.evidence_frame: Optional[np.ndarray] = None
        self.evidence_path: Optional[str] = None
        self.evidence_video_path: Optional[str] = None
        self.room_context: Optional[str] = None
        self.reviewer_note: str = ""

    @property
    def duration_frames(self) -> int:
        """Total frame span of the event."""
        return max(1, self.end_frame - self.start_frame + 1)

    @property
    def duration_seconds(self) -> float:
        """Elapsed duration of event in seconds."""
        if (
            self.start_timestamp_ms is not None
            and self.end_timestamp_ms is not None
            and self.end_timestamp_ms >= self.start_timestamp_ms
        ):
            return float((self.end_timestamp_ms - self.start_timestamp_ms) / 1000.0)
        return float(self.duration_frames / self.fps)

    @property
    def average_confidence(self) -> float:
        """Mean confidence score over the duration."""
        if not self.confidences:
            return 0.0
        return float(sum(self.confidences) / len(self.confidences))

    def update(
        self,
        frame_idx: int,
        confidence: float,
        bbox: Tuple[int, int, int, int],
        frame_image: Optional[np.ndarray] = None,
        timestamp_ms: Optional[float] = None,
    ) -> bool:
        """Update live event with current frame telemetry."""
        if self.is_closed:
            return False

        self.end_frame = frame_idx
        if timestamp_ms is not None:
            self.end_timestamp_ms = timestamp_ms
        self.latest_bbox = bbox
        self.confidences.append(float(confidence))

        # Check if current frame provides clearer peak evidence
        if confidence >= self.peak_confidence:
            self.peak_confidence = float(confidence)
            self.peak_frame_idx = frame_idx
            self.peak_bbox = bbox
            if frame_image is not None:
                self.evidence_frame = frame_image.copy()

        # Automatic severity escalation for protracted violations
        if (
            self.severity == SeverityLevel.MEDIUM.value
            and self.duration_seconds >= self.config.escalation_duration_seconds
        ):
            self.severity = SeverityLevel.HIGH.value
            self.status = EventStatus.FLAGGED_FOR_HUMAN_REVIEW.value
            return True  # Signal escalation

        return False

    def close(
        self, end_frame: int, timestamp_ms: Optional[float] = None
    ) -> ClassroomEvent:
        """Conclude and package event into an immutable ClassroomEvent."""
        self.is_closed = True
        self.end_frame = max(self.end_frame, end_frame)
        if timestamp_ms is not None:
            self.end_timestamp_ms = max(
                self.end_timestamp_ms or 0.0, timestamp_ms
            )

        if (
            self.duration_seconds >= 3.0
            or self.severity == SeverityLevel.HIGH.value
            or self.is_recidivist
        ):
            self.status = EventStatus.FLAGGED_FOR_HUMAN_REVIEW.value

        return self.to_event()

    def to_event(self) -> ClassroomEvent:
        """Export snapshot of current event state."""
        return ClassroomEvent(
            event_id=self.event_id,
            track_id=self.track_id,
            behavior=self.behavior,
            severity=self.severity,
            confidence_avg=self.average_confidence,
            confidence_peak=self.peak_confidence,
            start_frame=self.start_frame,
            end_frame=self.end_frame,
            duration_seconds=self.duration_seconds,
            status=self.status,
            peak_frame_idx=self.peak_frame_idx,
            bbox=self.peak_bbox,
            evidence_path=self.evidence_path,
            evidence_video_path=self.evidence_video_path,
            evidence_frame=self.evidence_frame,
            room_context=self.room_context,
            reviewer_note=self.reviewer_note,
            is_recidivist=self.is_recidivist,
            requires_human_review=self.requires_human_review,
            timestamp_ms=self.start_timestamp_ms,
            created_at=self.created_at,
        )
