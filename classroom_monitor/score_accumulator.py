"""Anti-Flickering Sliding Window Score Accumulator.

Absorbs intermittent single-frame misclassifications and detection dropouts
by accumulating confidence scores rather than relying on brittle consecutive frame counters.
"""

from __future__ import annotations

from collections import Counter, deque
from typing import Dict, List, Optional, Set, Tuple


class ScoreAccumulator:
    """Sliding-window evidence score accumulator for temporal stability."""

    def __init__(
        self,
        window_size: int = 45,
        score_threshold: float = 4.5,
        cheating_ratio_threshold: float = 0.55,
        min_frames: int = 12,
        normal_penalty: float = -0.30,
        cheating_classes: Optional[Set[str]] = None,
    ):
        self.window_size = window_size
        self.score_threshold = score_threshold
        self.cheating_ratio_threshold = cheating_ratio_threshold
        self.min_frames = min_frames
        self.normal_penalty = normal_penalty
        self.cheating_classes = cheating_classes or {
            "back peeking",
            "front peeking",
            "phone using",
            "side peeking",
        }

        # Sliding buffers
        self.scores: deque[float] = deque(maxlen=window_size)
        self.behaviors: deque[str] = deque(maxlen=window_size)
        self.confidences: deque[float] = deque(maxlen=window_size)

    def add(self, behavior: str, confidence: float) -> None:
        """Append a frame observation to the accumulator."""
        self.behaviors.append(behavior)
        self.confidences.append(confidence)

        if behavior in self.cheating_classes:
            # Positive score weighted by model confidence
            self.scores.append(float(confidence))
        else:
            # Mild negative penalty to tolerate flickering
            self.scores.append(self.normal_penalty)

    def reset(self) -> None:
        """Flush the sliding window accumulator."""
        self.scores.clear()
        self.behaviors.clear()
        self.confidences.clear()

    @property
    def cumulative_score(self) -> float:
        """Sum of scores currently within the sliding window."""
        return max(0.0, float(sum(self.scores)))

    @property
    def cheating_ratio(self) -> float:
        """Fraction of frames in window that detected cheating behaviors."""
        if not self.scores:
            return 0.0
        positives = sum(1 for s in self.scores if s > 0.0)
        return float(positives / len(self.scores))

    @property
    def dominant_behavior(self) -> str:
        """Find the most frequent cheating behavior in the current window."""
        cheating_counts: Counter[str] = Counter(
            b for b in self.behaviors if b in self.cheating_classes
        )
        if not cheating_counts:
            return "no cheating"
        return cheating_counts.most_common(1)[0][0]

    @property
    def average_confidence(self) -> float:
        """Calculate average confidence of positive cheating detections."""
        pos_confs = [
            c for b, c in zip(self.behaviors, self.confidences) if b in self.cheating_classes
        ]
        if not pos_confs:
            return 0.0
        return float(sum(pos_confs) / len(pos_confs))

    @property
    def peak_confidence(self) -> float:
        """Find maximum confidence among cheating frames in window."""
        pos_confs = [
            c for b, c in zip(self.behaviors, self.confidences) if b in self.cheating_classes
        ]
        if not pos_confs:
            return 0.0
        return float(max(pos_confs))

    def is_suspicious(self) -> bool:
        """Determine if accumulated evidence exceeds statistical significance thresholds."""
        if len(self.scores) < self.min_frames:
            return False

        return (
            self.cheating_ratio >= self.cheating_ratio_threshold
            and self.cumulative_score >= self.score_threshold
        )

    def is_cleared(self) -> bool:
        """Check if behavior has reliably returned to normal."""
        if not self.scores:
            return True
        # If over 80% of recent 15 frames are normal, consider cleared
        recent_behaviors = list(self.behaviors)[-15:]
        normal_count = sum(1 for b in recent_behaviors if b not in self.cheating_classes)
        return (normal_count / len(recent_behaviors)) >= 0.80
