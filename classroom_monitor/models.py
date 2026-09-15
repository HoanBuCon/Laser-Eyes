"""Domain Data Models and Data Classes for VIGIL AI Classroom Proctoring."""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


class SeverityLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class EventStatus(str, Enum):
    SUSPICIOUS = "suspicious"
    CONFIRMED = "confirmed"
    SUPPRESSED = "suppressed"
    REVIEWED = "reviewed"
    DISMISSED = "dismissed"


@dataclass
class Detection:
    """Raw single-frame object detection output."""

    class_id: int
    class_name: str
    confidence: float
    bbox: Tuple[int, int, int, int]  # (x1, y1, x2, y2) in absolute pixel coordinates
    frame_index: int
    timestamp: float = field(default_factory=time.time)

    @property
    def center(self) -> Tuple[float, float]:
        """Compute bounding box center point."""
        return (
            (self.bbox[0] + self.bbox[2]) / 2.0,
            (self.bbox[1] + self.bbox[3]) / 2.0,
        )

    @property
    def area(self) -> int:
        """Compute pixel area of detection box."""
        w = max(0, self.bbox[2] - self.bbox[0])
        h = max(0, self.bbox[3] - self.bbox[1])
        return w * h

    def to_dict(self) -> Dict[str, Any]:
        return {
            "class_id": self.class_id,
            "class_name": self.class_name,
            "confidence": round(self.confidence, 4),
            "bbox": list(self.bbox),
            "frame_index": self.frame_index,
            "timestamp": self.timestamp,
        }


# Alias for backward compatibility
ClassroomDetection = Detection


@dataclass
class TrackedDetection:
    """Detection associated with a persistent spatial person track ID."""

    track_id: int
    detection: Detection


@dataclass
class ContextSignal:
    """Room-level contextual inference signal."""

    suppress: bool = False
    boost_severity: bool = False
    reason: str = ""
    affected_tracks: List[int] = field(default_factory=list)


@dataclass
class ClassroomEvent:
    """Verified suspicious or cheating behavioral event emitted by EventEngine."""

    event_id: str
    track_id: int
    behavior: str
    severity: str  # "HIGH", "MEDIUM", "LOW"
    confidence_avg: float
    confidence_peak: float
    start_frame: int
    end_frame: int
    duration_seconds: float
    status: str = EventStatus.SUSPICIOUS.value
    peak_frame_idx: int = 0
    bbox: Optional[Tuple[int, int, int, int]] = None
    evidence_path: Optional[str] = None
    evidence_frame: Optional[np.ndarray] = None
    room_context: Optional[str] = None
    reviewer_note: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self, include_frame: bool = False) -> Dict[str, Any]:
        """Serialize event to dictionary for REST APIs or JSON logs."""
        data = {
            "event_id": self.event_id,
            "track_id": self.track_id,
            "behavior": self.behavior,
            "severity": self.severity,
            "confidence_avg": round(self.confidence_avg, 4),
            "confidence_peak": round(self.confidence_peak, 4),
            "start_frame": self.start_frame,
            "end_frame": self.end_frame,
            "duration_seconds": round(self.duration_seconds, 2),
            "status": self.status,
            "peak_frame_idx": self.peak_frame_idx,
            "bbox": list(self.bbox) if self.bbox else None,
            "evidence_path": self.evidence_path,
            "room_context": self.room_context,
            "reviewer_note": self.reviewer_note,
            "created_at": self.created_at,
        }
        if include_frame and self.evidence_frame is not None:
            data["evidence_frame_shape"] = list(self.evidence_frame.shape)
        return data


@dataclass
class RoomRiskSummary:
    """Statistical summary of risk metrics for a monitored classroom."""

    room_id: str
    session_id: str
    total_persons_detected: int
    active_events_count: int
    cumulative_risk_score: int  # 0 to 100
    risk_level: str  # "NORMAL", "CAUTION", "CRITICAL"
    violations_by_behavior: Dict[str, int] = field(default_factory=dict)
