"""Small, AI-neutral contracts for human-created review bookmarks."""

from __future__ import annotations

from enum import Enum


class SourceProduct(str, Enum):
    CLASSROOM = "CLASSROOM"
    LOCAL_GAZE = "LOCAL_GAZE"


class ReviewDecision(str, Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    INCONCLUSIVE = "INCONCLUSIVE"


class EvidenceState(str, Enum):
    PENDING = "PENDING"
    READY = "READY"
    FAILED = "FAILED"


REVIEW_DECISIONS = {item.value for item in ReviewDecision}
