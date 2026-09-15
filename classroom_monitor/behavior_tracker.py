"""Per-Person Behavior State Machine with Silent Cooldown Tracking.

Maintains fine-grained temporal state transitions for each student:
NORMAL -> WATCHING -> SUSPICIOUS -> FLAGGED_FOR_HUMAN_REVIEW -> COOLDOWN

Includes:
1. Silent Background Score Tracking during cooldown.
2. Rapid Recidivism Escalation to HIGH risk when repeated cheating occurs.
3. Time-aware millisecond durations to eliminate frame rate jitter.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Optional, Tuple

import numpy as np

from classroom_monitor.config import DEFAULT_CONFIG, ClassroomConfig
from classroom_monitor.live_event import LiveEvent
from classroom_monitor.models import ClassroomEvent, Detection, EventStatus, SeverityLevel
from classroom_monitor.score_accumulator import ScoreAccumulator

logger = logging.getLogger("BehaviorTracker")


class StudentState(str, Enum):
    NORMAL = "NORMAL"
    WATCHING = "WATCHING"
    SUSPICIOUS = "SUSPICIOUS"
    FLAGGED_FOR_HUMAN_REVIEW = "FLAGGED_FOR_HUMAN_REVIEW"
    CONFIRMED = "CONFIRMED"  # Backward-compatible alias
    COOLDOWN = "COOLDOWN"


class PersonBehaviorTracker:
    """State machine tracking an individual student's proctoring timeline."""

    def __init__(
        self,
        track_id: int,
        fps: float = 30.0,
        config: Optional[ClassroomConfig] = None,
    ):
        self.track_id: int = track_id
        self.fps: float = max(1.0, fps)
        self.config: ClassroomConfig = config or DEFAULT_CONFIG

        self.state: StudentState = StudentState.NORMAL
        self.score_accumulator: ScoreAccumulator = ScoreAccumulator(
            window_size=self.config.score_window_size,
            score_threshold=self.config.score_threshold,
            cheating_ratio_threshold=self.config.cheating_ratio_threshold,
            min_frames=self.config.min_frames_in_window,
            normal_penalty=self.config.normal_penalty,
            window_duration_ms=self.config.window_duration_ms,
            min_duration_ms=self.config.min_duration_ms,
            cheating_classes=self.config.cheating_classes,
        )
        self.live_event: Optional[LiveEvent] = None
        self.cooldown_until_frame: int = 0
        self.cooldown_until_ms: float = 0.0
        self.consecutive_missing_frames: int = 0
        self.total_violations: int = 0

    def update(
        self,
        detection: Optional[Detection],
        frame_idx: int,
        frame_image: Optional[np.ndarray] = None,
        timestamp_ms: Optional[float] = None,
    ) -> Optional[ClassroomEvent]:
        """Update behavior state machine for this frame and return event if state changed."""
        ts_ms = (
            float(timestamp_ms)
            if timestamp_ms is not None
            else float(frame_idx / self.fps * 1000.0)
        )

        # -------------------------------------------------------------
        # 1. Cooldown Guard & Silent Background Tracking
        # -------------------------------------------------------------
        if self.state == StudentState.COOLDOWN:
            is_cooldown_expired = (
                frame_idx >= self.cooldown_until_frame
                or (self.cooldown_until_ms > 0 and ts_ms >= self.cooldown_until_ms)
            )

            if is_cooldown_expired:
                self.state = StudentState.NORMAL
                self.score_accumulator.reset()
            else:
                # Still within cooldown window: perform Silent Tracking
                if self.config.silent_cooldown_tracking and detection is not None:
                    self.score_accumulator.add(
                        detection.class_name,
                        detection.confidence,
                        timestamp_ms=ts_ms,
                        frame_idx=frame_idx,
                    )

                    # Check for Recidivism Escalation
                    if self.config.recidivism_escalation:
                        is_recidivist_trigger = (
                            self.score_accumulator.is_suspicious()
                            or (
                                detection.class_name in self.config.cheating_classes
                                and self.score_accumulator.cumulative_score
                                >= self.config.recidivism_score_threshold
                            )
                        )
                        if is_recidivist_trigger:
                            # Instant escalation to HIGH severity event
                            self.state = StudentState.FLAGGED_FOR_HUMAN_REVIEW
                            self.total_violations += 1
                            dominant = self.score_accumulator.dominant_behavior
                            self.live_event = LiveEvent(
                                track_id=self.track_id,
                                behavior=dominant,
                                start_frame=frame_idx,
                                confidence=detection.confidence,
                                bbox=detection.bbox,
                                fps=self.fps,
                                config=self.config,
                                timestamp_ms=ts_ms,
                                is_recidivist=True,
                            )
                            if frame_image is not None:
                                self.live_event.evidence_frame = frame_image.copy()
                            return self.live_event.to_event()

                return None

        # -------------------------------------------------------------
        # 2. Missing Detection (Temporary Occlusion Handling)
        # -------------------------------------------------------------
        if detection is None:
            self.consecutive_missing_frames += 1
            if self.consecutive_missing_frames >= 15:
                # Conclude ongoing event if student disappeared permanently
                if (
                    self.state in (StudentState.SUSPICIOUS, StudentState.FLAGGED_FOR_HUMAN_REVIEW, StudentState.CONFIRMED)
                    and self.live_event
                ):
                    closed_event = self.live_event.close(frame_idx, timestamp_ms=ts_ms)
                    self._enter_cooldown(frame_idx, ts_ms)
                    return closed_event
            return None

        self.consecutive_missing_frames = 0
        behavior = detection.class_name
        confidence = detection.confidence
        bbox = detection.bbox

        # Accumulate score
        self.score_accumulator.add(
            behavior, confidence, timestamp_ms=ts_ms, frame_idx=frame_idx
        )

        is_cheating = behavior in self.config.cheating_classes

        # -------------------------------------------------------------
        # 3. State Transitions
        # -------------------------------------------------------------
        if self.state == StudentState.NORMAL:
            if is_cheating and confidence >= self.config.confidence_threshold:
                self.state = StudentState.WATCHING
            return None

        elif self.state == StudentState.WATCHING:
            if self.score_accumulator.is_suspicious():
                self.state = StudentState.SUSPICIOUS
                self.total_violations += 1
                dominant = self.score_accumulator.dominant_behavior
                start_frame = max(0, frame_idx - len(self.score_accumulator.scores) + 1)
                self.live_event = LiveEvent(
                    track_id=self.track_id,
                    behavior=dominant,
                    start_frame=start_frame,
                    confidence=confidence,
                    bbox=bbox,
                    fps=self.fps,
                    config=self.config,
                    timestamp_ms=ts_ms,
                    is_recidivist=(self.total_violations > 1),
                )
                if frame_image is not None:
                    self.live_event.evidence_frame = frame_image.copy()
                return self.live_event.to_event()

            elif self.score_accumulator.is_cleared():
                self.state = StudentState.NORMAL
                self.score_accumulator.reset()
            return None

        elif self.state == StudentState.SUSPICIOUS:
            if self.live_event is not None:
                escalated = self.live_event.update(
                    frame_idx, confidence, bbox, frame_image, timestamp_ms=ts_ms
                )
                if escalated:
                    self.state = StudentState.FLAGGED_FOR_HUMAN_REVIEW
                    return self.live_event.to_event()

            # Check if student stopped cheating
            if self.score_accumulator.is_cleared():
                if self.live_event is not None:
                    closed_event = self.live_event.close(frame_idx, timestamp_ms=ts_ms)
                    self._enter_cooldown(frame_idx, ts_ms)
                    return closed_event
            return None

        elif self.state in (StudentState.FLAGGED_FOR_HUMAN_REVIEW, StudentState.CONFIRMED):
            if self.live_event is not None:
                self.live_event.update(
                    frame_idx, confidence, bbox, frame_image, timestamp_ms=ts_ms
                )

            if self.score_accumulator.is_cleared():
                if self.live_event is not None:
                    closed_event = self.live_event.close(frame_idx, timestamp_ms=ts_ms)
                    self._enter_cooldown(frame_idx, ts_ms)
                    return closed_event
            return None

        return None

    def _enter_cooldown(self, frame_idx: int, timestamp_ms: Optional[float] = None) -> None:
        """Place student in cooldown mode with silent monitoring active."""
        self.state = StudentState.COOLDOWN
        self.cooldown_until_frame = frame_idx + int(self.config.cooldown_seconds * self.fps)
        ts = timestamp_ms if timestamp_ms is not None else (frame_idx / self.fps * 1000.0)
        self.cooldown_until_ms = ts + (self.config.cooldown_seconds * 1000.0)
        self.live_event = None
        self.score_accumulator.reset()
