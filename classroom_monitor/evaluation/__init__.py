"""Evaluation and benchmark utilities for VIGIL AI SRS v2."""

from classroom_monitor.evaluation.temporal_matcher import (
    CanonicalTemporalMatcher,
    EvaluationResult,
    compute_temporal_iou,
    match_temporal_episodes,
    normalize_label,
    normalize_seat_code,
)

__all__ = [
    "CanonicalTemporalMatcher",
    "EvaluationResult",
    "compute_temporal_iou",
    "match_temporal_episodes",
    "normalize_label",
    "normalize_seat_code",
]
