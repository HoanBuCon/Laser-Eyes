"""Transactional human-review service shared by all API surfaces."""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from storage.db_models import AuditLog, DetectionEvent, EventReview


class ReviewTargetMissing(LookupError):
    pass


@dataclass(frozen=True)
class ReviewCommand:
    event_id: str
    reviewer_id: str
    decision: str
    reason_code: Optional[str] = None
    note: str = ""


def submit_event_review(db: Session, command: ReviewCommand) -> EventReview:
    """Persist decision, event status and audit record in one transaction."""
    decision = command.decision.upper().strip()
    if decision not in {"CONFIRMED", "REJECTED", "INCONCLUSIVE"}:
        raise ValueError("Invalid review decision")

    event = (
        db.query(DetectionEvent)
        .filter(
            (DetectionEvent.id == command.event_id)
            | (DetectionEvent.event_id == command.event_id)
        )
        .first()
    )
    if event is None:
        raise ReviewTargetMissing(command.event_id)

    review = db.query(EventReview).filter(EventReview.event_id == event.id).first()
    if review is None:
        review = EventReview(event_id=event.id, reviewer_id=command.reviewer_id, decision=decision)
        db.add(review)

    review.reviewer_id = command.reviewer_id
    review.decision = decision
    review.reason_code = command.reason_code
    review.note = command.note
    review.reviewed_at = datetime.datetime.utcnow()

    event.review_status = decision
    # AI status is intentionally not overwritten by a human decision.
    event.reviewer_note = command.note

    db.add(
        AuditLog(
            actor_id=command.reviewer_id,
            action="REVIEW_EVENT",
            resource_type="EVENT",
            resource_id=event.id,
            metadata_json=json.dumps(
                {
                    "decision": decision,
                    "reason_code": command.reason_code,
                    "event_id": event.event_id,
                    "seat_id": event.seat_id,
                }
            ),
            timestamp=datetime.datetime.utcnow(),
        )
    )
    try:
        db.commit()
        db.refresh(review)
    except Exception:
        db.rollback()
        raise
    return review
