"""Exam-session lifecycle in the database.

A session is created RUNNING when an analysis starts.  It must be closed when
the analysis ends, otherwise every session ever started stays RUNNING and the
dashboards cannot tell live sessions from finished ones.

Closing states:
* ``COMPLETED``    the whole video/stream was analysed
* ``STOPPED``      a person stopped the analysis early
* ``FAILED``       the analysis ended with an error
* ``INTERRUPTED``  the server stopped while the session was running (found at
                   the next start-up)
"""

from __future__ import annotations

import datetime
import logging
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from storage.db_models import DetectionEvent, ExamSession

logger = logging.getLogger("SessionLifecycle")

OPEN_STATES = ("RUNNING", "STOPPING")
CLOSED_STATES = ("COMPLETED", "STOPPED", "FAILED", "INTERRUPTED")


def _summarise(db: Session, session: ExamSession) -> None:
    """Event count and the highest incident priority of the session."""
    total, peak = (
        db.query(func.count(DetectionEvent.id), func.max(DetectionEvent.risk_score))
        .filter(DetectionEvent.session_id == session.id)
        .one()
    )
    session.total_events = int(total or 0)
    session.risk_score = int(peak or 0)


def finish_session(db: Session, session_id: Optional[str], outcome: str) -> bool:
    """Close one session with ``outcome`` (one of CLOSED_STATES). Returns True if changed."""
    if not session_id:
        return False
    if outcome not in CLOSED_STATES:
        raise ValueError(f"outcome must be one of {CLOSED_STATES}, got {outcome!r}")
    session = db.get(ExamSession, session_id)
    if session is None or session.status not in OPEN_STATES:
        return False
    session.status = outcome
    session.ended_at = datetime.datetime.utcnow()
    _summarise(db, session)
    db.commit()
    return True


def close_interrupted_sessions(db: Session) -> int:
    """Mark sessions left RUNNING by a previous server process as INTERRUPTED.

    Called once at start-up, before any analysis can run, so every open
    session found then belongs to a process that no longer exists.
    """
    sessions = db.query(ExamSession).filter(ExamSession.status.in_(OPEN_STATES)).all()
    for session in sessions:
        session.status = "INTERRUPTED"
        # The real end time is unknown; the start time keeps durations honest
        session.ended_at = session.ended_at or session.started_at or datetime.datetime.utcnow()
        _summarise(db, session)
    if sessions:
        db.commit()
        logger.info("Closed %d session(s) left running by a previous server process", len(sessions))
    return len(sessions)
