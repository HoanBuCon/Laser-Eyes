"""Versioned domain contracts for the VIGIL Classroom review workflow.

These contracts deliberately sit above perception.  They keep AI review
priority and human review decisions separate and provide explicit adapters for
the legacy ``ClassroomEvent`` and exported replay artifact shapes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from classroom_monitor.models import ClassroomEvent


CONTRACT_VERSION = "classroom-review-incident/v1"


class CapabilityMode(str, Enum):
    REAL = "REAL"
    DEGRADED = "DEGRADED"
    MOCK = "MOCK"
    ERROR = "ERROR"


class ReviewDecision(str, Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    INCONCLUSIVE = "INCONCLUSIVE"


class EvidenceStatus(str, Enum):
    PENDING = "PENDING"
    READY = "READY"
    FAILED = "FAILED"


class HashStatus(str, Enum):
    VERIFIED = "HASH_VERIFIED"
    MISMATCH = "HASH_MISMATCH"
    AVAILABLE_NOT_CHECKED = "HASH_AVAILABLE_NOT_CHECKED"
    NOT_AVAILABLE = "HASH_NOT_AVAILABLE"


@dataclass
class EvidenceRef:
    status: str = EvidenceStatus.PENDING.value
    snapshot_path: Optional[str] = None
    video_path: Optional[str] = None
    snapshot_url: Optional[str] = None
    video_url: Optional[str] = None
    sha256: Optional[str] = None
    hash_status: str = HashStatus.NOT_AVAILABLE.value
    error: Optional[str] = None


@dataclass
class ReviewIncident:
    incident_id: str
    session_id: str
    subject_ref: str
    primary_signal: str
    severity: str
    review_priority_score: float
    first_seen_ms: float
    last_seen_ms: float
    occurrence_count: int = 1
    supporting_signals: List[str] = field(default_factory=list)
    component_episode_ids: List[str] = field(default_factory=list)
    source_product: str = "CLASSROOM_SRS_V2"
    ai_status: str = "FLAGGED_FOR_REVIEW"
    review_decision: str = ReviewDecision.PENDING.value
    evidence: EvidenceRef = field(default_factory=EvidenceRef)
    capability_health: Dict[str, str] = field(default_factory=dict)
    model_version: str = "yolo11n-pose"
    config_version: str = "school-prototype-v1"
    contract_version: str = CONTRACT_VERSION
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        # Compatibility aliases are emitted only at the API boundary.  Their
        # meaning remains review priority, never probability.
        data.update(
            {
                "event_id": self.incident_id,
                "seat_id": self.subject_ref,
                "seat_code": self.subject_ref,
                "behavior": self.primary_signal,
                "primary_pattern": self.primary_signal,
                "peak_risk_score": self.review_priority_score,
                "risk_score": self.review_priority_score,
                "review_status": self.review_decision,
                "status": self.ai_status,
                "snapshot_url": self.evidence.snapshot_url,
                "video_url": self.evidence.video_url,
                "video_sha256": self.evidence.sha256,
                "hash_status": self.evidence.hash_status,
                "evidence_status": self.evidence.status,
                "evidence_error": self.evidence.error,
            }
        )
        return data


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def review_incident_from_classroom_event(
    event: ClassroomEvent,
    *,
    session_id: str,
    capability_health: Optional[Mapping[str, str]] = None,
) -> ReviewIncident:
    """Adapt the perception-domain event without adding fake event fields."""
    metadata = dict(event.metadata or {})
    timestamp_ms = _number(
        event.timestamp_ms,
        _number(event.timestamp, 0.0) * 1000.0,
    )
    priority = _number(
        metadata.get("peak_risk_score", metadata.get("risk_score")),
        0.0,
    )
    return ReviewIncident(
        incident_id=event.event_id,
        session_id=session_id,
        subject_ref=event.seat_id or "UNASSIGNED",
        primary_signal=metadata.get("primary_pattern") or event.primary_pattern or event.behavior,
        supporting_signals=list(metadata.get("supporting_pattern_ids") or event.supporting_patterns or []),
        component_episode_ids=list(metadata.get("component_episode_ids") or []),
        severity=str(event.severity.value if hasattr(event.severity, "value") else event.severity),
        review_priority_score=priority,
        first_seen_ms=_number(metadata.get("first_seen_ms"), timestamp_ms),
        last_seen_ms=_number(metadata.get("last_seen_ms", metadata.get("last_seen_timestamp_ms")), timestamp_ms),
        occurrence_count=max(1, int(metadata.get("occurrence_count", 1))),
        evidence=EvidenceRef(
            status=EvidenceStatus.PENDING.value,
            snapshot_path=event.evidence_path,
            video_path=event.evidence_video_path,
        ),
        capability_health=dict(capability_health or {}),
        metadata=metadata,
    )


def classroom_event_from_replay(data: Mapping[str, Any]) -> ClassroomEvent:
    """Deserialize both current exporter and older replay event shapes."""
    metadata = dict(data.get("metadata") or {})
    priority = _number(
        data.get("peak_risk_score", metadata.get("peak_risk_score", data.get("risk_score"))),
        0.0,
    )
    metadata.setdefault("peak_risk_score", priority)
    metadata.setdefault("risk_score", priority)
    metadata.setdefault("occurrence_count", int(data.get("occurrence_count", 1)))
    metadata.setdefault("first_seen_ms", _number(data.get("first_seen_ms", data.get("start_ms")), 0.0))
    metadata.setdefault("last_seen_ms", _number(data.get("last_seen_ms", data.get("timestamp_ms")), metadata["first_seen_ms"]))
    metadata.setdefault("primary_pattern", data.get("primary_pattern") or data.get("title") or data.get("behavior"))
    metadata.setdefault("component_episode_ids", list(data.get("component_episode_ids") or []))
    metadata.setdefault("supporting_pattern_ids", list(data.get("supporting_pattern_ids") or []))

    timestamp_ms = _number(data.get("timestamp_ms", data.get("start_timestamp_ms", data.get("start_ms"))), metadata["first_seen_ms"])
    return ClassroomEvent(
        event_id=str(data.get("event_id") or data.get("id") or ""),
        track_id=int(data.get("track_id") or data.get("actor_track_id") or 0),
        seat_id=str(data.get("seat_id") or data.get("seat_code") or "UNASSIGNED"),
        behavior=str(data.get("behavior") or data.get("title") or metadata.get("primary_pattern") or "REVIEW_SIGNAL"),
        primary_pattern=str(metadata.get("primary_pattern") or "REVIEW_SIGNAL"),
        severity=str(data.get("severity") or "MEDIUM"),
        timestamp_ms=timestamp_ms,
        evidence_path=data.get("evidence_path") or data.get("evidence_snapshot_path"),
        evidence_video_path=data.get("evidence_video_path") or data.get("evidence_clip_path"),
        metadata=metadata,
        status="flagged_for_human_review",
        requires_human_review=True,
    )


def evidence_basename(path: Optional[str]) -> Optional[str]:
    return Path(path).name if path else None
