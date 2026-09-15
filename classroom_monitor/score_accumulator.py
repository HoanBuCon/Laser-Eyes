"""Anti-Flickering Time-Aware Sliding Window Score Accumulator.

Absorbs intermittent single-frame misclassifications, network RTSP jitter,
and camera frame rate fluctuations by accumulating confidence scores across
a wall-clock millisecond time window rather than brittle static frame counts.
"""

from __future__ import annotations

import time
from collections import Counter, deque
from typing import Deque, Dict, List, NamedTuple, Optional, Set, Tuple


class ObservationRecord(NamedTuple):
    """Single frame observation telemetry."""

    timestamp_ms: float
    behavior: str
    confidence: float
    score: float
    frame_idx: int


class ScoreAccumulator:
    """Time-aware sliding-window evidence score accumulator for temporal stability."""

    def __init__(
        self,
        window_size: int = 45,
        score_threshold: float = 4.5,
        cheating_ratio_threshold: float = 0.55,
        min_frames: int = 12,
        normal_penalty: float = -0.30,
        window_duration_ms: float = 1500.0,
        min_duration_ms: float = 400.0,
        cheating_classes: Optional[Set[str]] = None,
    ):
        self.window_size = window_size
        self.score_threshold = score_threshold
        self.cheating_ratio_threshold = cheating_ratio_threshold
        self.min_frames = min_frames
        self.normal_penalty = normal_penalty
        self.window_duration_ms = window_duration_ms
        self.min_duration_ms = min_duration_ms
        self.cheating_classes = cheating_classes or {
            "back peeking",
            "front peeking",
            "phone using",
            "side peeking",
        }

        # Sliding window buffer of observation records
        self._records: Deque[ObservationRecord] = deque()
        self._last_synth_ts: float = 0.0

    def add(
        self,
        behavior: str,
        confidence: float,
        timestamp_ms: Optional[float] = None,
        frame_idx: Optional[int] = None,
    ) -> None:
        """Append a frame observation to the time-aware accumulator."""
        # Determine timestamp in milliseconds
        if timestamp_ms is not None:
            ts = float(timestamp_ms)
        elif self._records:
            # Advance synthetic timestamp at default 30fps (~33.33ms)
            ts = self._records[-1].timestamp_ms + 33.333
        else:
            ts = time.time() * 1000.0

        f_idx = frame_idx if frame_idx is not None else len(self._records)

        # Compute point contribution
        if behavior in self.cheating_classes:
            score = float(confidence)
        else:
            score = float(self.normal_penalty)

        rec = ObservationRecord(
            timestamp_ms=ts,
            behavior=behavior,
            confidence=float(confidence),
            score=score,
            frame_idx=f_idx,
        )
        self._records.append(rec)

        # Prune records older than window_duration_ms
        cutoff_ts = ts - self.window_duration_ms
        while self._records and (self._records[0].timestamp_ms < cutoff_ts):
            # Also ensure we don't prune excessively if only 1 record remains
            if len(self._records) <= 1:
                break
            self._records.popleft()

        # Enforce maximum frame cap as secondary safety limit
        max_cap = max(self.window_size, int(self.window_duration_ms / 15.0))
        while len(self._records) > max_cap:
            self._records.popleft()

    def reset(self) -> None:
        """Flush all observations from the accumulator."""
        self._records.clear()

    @property
    def scores(self) -> List[float]:
        """List of all point scores in the active window."""
        return [r.score for r in self._records]

    @property
    def behaviors(self) -> List[str]:
        """List of all behavior class labels in the active window."""
        return [r.behavior for r in self._records]

    @property
    def confidences(self) -> List[float]:
        """List of all confidence values in the active window."""
        return [r.confidence for r in self._records]

    @property
    def time_span_ms(self) -> float:
        """Elapsed millisecond span currently enclosed by the sliding window."""
        if len(self._records) < 2:
            return 0.0
        return max(0.0, self._records[-1].timestamp_ms - self._records[0].timestamp_ms)

    @property
    def cumulative_score(self) -> float:
        """Sum of all scores currently within the sliding window."""
        return max(0.0, float(sum(r.score for r in self._records)))

    @property
    def cheating_ratio(self) -> float:
        """Fraction of observations in window indicating cheating."""
        if not self._records:
            return 0.0
        positives = sum(1 for r in self._records if r.score > 0.0)
        return float(positives / len(self._records))

    @property
    def dominant_behavior(self) -> str:
        """Identify the most frequent cheating behavior in the current window."""
        cheating_counts: Counter[str] = Counter(
            r.behavior for r in self._records if r.behavior in self.cheating_classes
        )
        if not cheating_counts:
            return "no cheating"
        return cheating_counts.most_common(1)[0][0]

    @property
    def average_confidence(self) -> float:
        """Calculate average confidence of positive cheating detections."""
        pos_confs = [
            r.confidence for r in self._records if r.behavior in self.cheating_classes
        ]
        if not pos_confs:
            return 0.0
        return float(sum(pos_confs) / len(pos_confs))

    @property
    def peak_confidence(self) -> float:
        """Find maximum confidence among cheating frames in window."""
        pos_confs = [
            r.confidence for r in self._records if r.behavior in self.cheating_classes
        ]
        if not pos_confs:
            return 0.0
        return float(max(pos_confs))

    def is_suspicious(self) -> bool:
        """Determine if accumulated evidence exceeds statistical significance thresholds."""
        if not self._records:
            return False

        # Sufficient observation requirement: either enough frames or enough duration
        has_min_frames = len(self._records) >= self.min_frames
        has_min_duration = self.time_span_ms >= self.min_duration_ms

        if not (has_min_frames or has_min_duration):
            return False

        return (
            self.cheating_ratio >= self.cheating_ratio_threshold
            and self.cumulative_score >= self.score_threshold
        )

    def is_cleared(self) -> bool:
        """Check if behavior has reliably returned to normal."""
        if not self._records:
            return True

        # Check the recent 15 records (or all records if fewer)
        recent = list(self._records)[-15:]
        normal_count = sum(1 for r in recent if r.behavior not in self.cheating_classes)
        return (normal_count / len(recent)) >= 0.80
