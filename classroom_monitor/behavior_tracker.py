"""Per-Person 4-State Behavior Machine.

Maintains fine-grained temporal state transitions for each student:
NORMAL -> WATCHING -> SUSPICIOUS -> CONFIRMED -> COOLDOWN
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Optional, Tuple

import numpy as np

from classroom_monitor.config import DEFAULT_CONFIG, ClassroomConfig
from classroom_monitor.live_event import LiveEvent
from classroom_monitor.models import ClassroomEvent, Detection
from classroom_monitor.score_accumulator import ScoreAccumulator

logger = logging.getLogger("BehaviorTracker")


class StudentState(str, Enum):
    NORMAL = "NORMAL"
    WATCHING = "WATCHING"
    SUSPICIOUS = "SUSPICIOUS"
    CONFIRMED = "CONFIRMED"
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
            cheating_classes=self.config.cheating_classes,
        )
        self.live_event: Optional[LiveEvent] = None
        self.cooldown_until_frame: int = 0
        self.consecutive_missing_frames: int = 0

    def update(
        self,
        detection: Optional[Detection],
        frame_idx: int,
        frame_image: Optional[np.ndarray] = None,
    ) -> Optional[ClassroomEvent]:
        """Update behavior state machine for this frame and return event if state changed."""
        # Cooldown guard
        if self.state == StudentState.COOLDOWN:
            if frame_idx >= self.cooldown_until_frame:
                self.state = StudentState.NORMAL
                self.score_accumulator.reset()
            else:
                return None

        # Missing detection (e.g., student temporarily occluded)
        if detection is None:
            self.consecutive_missing_frames += 1
            if self.consecutive_missing_frames >= 15:
                # Conclude ongoing event if student disappeared
                if self.state in (StudentState.SUSPICIOUS, StudentState.CONFIRMED) and self.live_event:
                    closed_event = self.live_event.close(frame_idx)
                    self._enter_cooldown(frame_idx)
                    return closed_event
            return None

        self.consecutive_missing_frames = 0
        behavior = detection.class_name
        confidence = detection.confidence
        bbox = detection.bbox

        # Accumulate score
        self.score_accumulator.add(behavior, confidence)

        is_cheating = behavior in self.config.cheating_classes

        # State Transitions
        if self.state == StudentState.NORMAL:
            if is_cheating and confidence >= self.config.confidence_threshold:
                self.state = StudentState.WATCHING
            return None

        elif self.state == StudentState.WATCHING:
            if self.score_accumulator.is_suspicious():
                self.state = StudentState.SUSPICIOUS
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
                escalated = self.live_event.update(frame_idx, confidence, bbox, frame_image)
                if escalated:
                    self.state = StudentState.CONFIRMED
                    return self.live_event.to_event()

            # Check if student stopped cheating
            if self.score_accumulator.is_cleared():
                if self.live_event is not None:
                    closed_event = self.live_event.close(frame_idx)
                    self._enter_cooldown(frame_idx)
                    return closed_event
            return None

        elif self.state == StudentState.CONFIRMED:
            if self.live_event is not None:
                self.live_event.update(frame_idx, confidence, bbox, frame_image)

            if self.score_accumulator.is_cleared():
                if self.live_event is not None:
                    closed_event = self.live_event.close(frame_idx)
                    self._enter_cooldown(frame_idx)
                    return closed_event
            return None

        return None

    def _enter_cooldown(self, frame_idx: int) -> None:
        """Place student in cooldown mode to prevent repeated alerts."""
        self.state = StudentState.COOLDOWN
        self.cooldown_until_frame = frame_idx + int(self.config.cooldown_seconds * self.fps)
        self.live_event = None
        self.score_accumulator.reset()
