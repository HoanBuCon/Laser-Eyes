"""Session report and exam summary built from the database.

The report states what was observed and what people decided.  It never turns
an AI flag into a verdict: only incidents a proctor CONFIRMED are listed as
recorded violations, with the reviewer who decided.
"""

from __future__ import annotations

import datetime
import hashlib
import json
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

from storage.db_models import DetectionEvent, Exam, ExamRoom, ExamSession, SeatROI, SessionReportVersion, User
from storage.exam_workflow import incident_meta, session_run_id

REVIEW_STATES = ("PENDING", "CONFIRMED", "REJECTED", "INCONCLUSIVE")


def _iso(value: Optional[datetime.datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _pattern(event: DetectionEvent) -> str:
    return event.primary_pattern or event.primary_signal or event.behavior


def build_session_report(db: Session, session: ExamSession, serialize: Callable[[DetectionEvent], Dict[str, Any]]) -> Dict[str, Any]:
    room = db.get(ExamRoom, session.room_id)
    exam = db.get(Exam, session.exam_id) if session.exam_id else None
    proctor = db.get(User, session.proctor_user_id) if session.proctor_user_id else None
    events = (
        db.query(DetectionEvent)
        .filter(DetectionEvent.session_id == session.id)
        .order_by(DetectionEvent.created_at)
        .all()
    )
    seats = db.query(SeatROI).filter(SeatROI.room_id == session.room_id, SeatROI.enabled == True).count()  # noqa: E712
    capacity = int(room.capacity or 0) if room else 0

    by_status = {state: 0 for state in REVIEW_STATES}
    by_pattern: Dict[str, Dict[str, int]] = {}
    review_minutes: List[float] = []
    confirmed: List[Dict[str, Any]] = []
    for event in events:
        status = event.review_status or "PENDING"
        by_status[status] = by_status.get(status, 0) + 1
        row = by_pattern.setdefault(_pattern(event), {state: 0 for state in REVIEW_STATES})
        row[status] = row.get(status, 0) + 1
        review = event.review
        if review and review.reviewed_at and event.created_at:
            review_minutes.append(max(0.0, (review.reviewed_at - event.created_at).total_seconds() / 60.0))
        if status == "CONFIRMED":
            item = serialize(event)
            meta = incident_meta(event)
            confirmed.append({
                "id": event.id,
                "event_id": event.event_id,
                "seat_code": item.get("subject_ref"),
                "pattern": _pattern(event),
                "severity": event.severity,
                "risk": event.risk_score,
                "behavior_start_ms": meta.get("behavior_start_ms"),
                "flagged_ms": meta.get("first_seen_ms"),
                "last_seen_ms": meta.get("last_seen_ms"),
                "supporting_cues": meta.get("supporting_cues") or [],
                "focus": meta.get("focus"),
                "reviewer": review.reviewer_id if review else None,
                "reason_code": review.reason_code if review else None,
                "note": review.note if review else "",
                "reviewed_at": _iso(review.reviewed_at) if review else None,
                "sha256": item.get("sha256"),
                "snapshot_url": item.get("snapshot_url"),
                "video_url": item.get("video_url"),
            })

    return {
        "generated_at": datetime.datetime.utcnow().isoformat(),
        "session": {
            "id": session.id,
            "name": session.exam_name,
            "status": session.status,
            "scheduled_start": _iso(session.scheduled_start),
            "scheduled_end": _iso(session.scheduled_end),
            "started_at": _iso(session.started_at),
            "ended_at": _iso(session.ended_at),
            "run_id": session_run_id(db, session),
        },
        "exam": {"id": exam.id, "name": exam.name, "subject_code": exam.subject_code, "exam_date": exam.exam_date} if exam else None,
        "room": {"id": room.id, "code": room.room_code, "name": room.name, "capacity": capacity} if room else None,
        "proctor": proctor.display_name if proctor else None,
        "coverage": {"seats_with_roi": seats, "capacity": capacity,
                     "ratio": round(seats / capacity, 3) if capacity else None},
        "incidents": {
            "total": len(events),
            "by_review_status": by_status,
            "by_pattern": by_pattern,
            "avg_review_minutes": round(sum(review_minutes) / len(review_minutes), 1) if review_minutes else None,
        },
        "confirmed": confirmed,
    }


def canonical_sha256(content: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def latest_version(db: Session, session_id: str) -> Optional[SessionReportVersion]:
    return (
        db.query(SessionReportVersion)
        .filter(SessionReportVersion.session_id == session_id)
        .order_by(SessionReportVersion.version.desc())
        .first()
    )


def ai_quality(events: List[DetectionEvent]) -> List[Dict[str, Any]]:
    """Per pattern: how proctors decided.  Precision = confirmed / (confirmed + rejected)."""
    rows: Dict[str, Dict[str, int]] = {}
    for event in events:
        row = rows.setdefault(_pattern(event), {state: 0 for state in REVIEW_STATES})
        row[event.review_status or "PENDING"] = row.get(event.review_status or "PENDING", 0) + 1
    result = []
    for pattern, row in sorted(rows.items(), key=lambda kv: -sum(kv[1].values())):
        decided = row["CONFIRMED"] + row["REJECTED"]
        result.append({
            "pattern": pattern,
            **{k.lower(): v for k, v in row.items()},
            "total": sum(row.values()),
            "precision": round(row["CONFIRMED"] / decided, 3) if decided else None,
        })
    return result
