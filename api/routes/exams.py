"""Exams and their room sessions: schedule, check, run, list.

Management actions need the CHIEF role when sign-in is required
(``VIGIL_AUTH_MODE=required``); in the open demo mode anyone may use them.
"""

from __future__ import annotations

import datetime
import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.auth import actor_name, current_user, require_roles
from api.realtime import realtime_manager
from classroom_monitor.demo.runtime import DemoRuntime, DemoState
from classroom_monitor.demo.seat_layout import room_seat_layout
from storage.database import get_db
from storage.db_models import AuditLog, DetectionEvent, Exam, ExamRoom, ExamSession, SeatROI, SessionReportVersion, User
from storage.exam_workflow import (
    SCHEDULED_STATES,
    preset_for_room,
    readiness,
    refresh_scheduled_status,
    review_counts,
    session_preset,
    session_run_id,
)

router = APIRouter(tags=["Exams & sessions"])
manage = require_roles("CHIEF")


class ExamIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    subject_code: Optional[str] = None
    exam_date: Optional[str] = Field(None, description="YYYY-MM-DD")
    description: Optional[str] = None


class ExamPatch(BaseModel):
    name: Optional[str] = None
    subject_code: Optional[str] = None
    exam_date: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None


class ScheduleIn(BaseModel):
    room_ids: List[str] = Field(..., min_length=1)
    scheduled_start: Optional[datetime.datetime] = None
    scheduled_end: Optional[datetime.datetime] = None
    proctor_user_id: Optional[str] = None
    name: Optional[str] = Field(None, description="Session name; defaults to the exam and room")


class SessionPatch(BaseModel):
    exam_id: Optional[str] = None
    name: Optional[str] = None
    scheduled_start: Optional[datetime.datetime] = None
    scheduled_end: Optional[datetime.datetime] = None
    proctor_user_id: Optional[str] = None


class StartIn(BaseModel):
    # A recorded replay re-emits incidents that already belong to the run that
    # recorded it, so a session is always analysed live.
    mode: str = Field("LIVE", description="Only LIVE: a session's incidents come from its own analysis")


def _iso(value: Optional[datetime.datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _audit(db: Session, actor: str, action: str, resource_type: str, resource_id: str, details: Dict[str, Any]) -> None:
    db.add(AuditLog(actor_id=actor[:100], action=action, resource_type=resource_type,
                    resource_id=resource_id, metadata_json=json.dumps(details, default=str)))


def _live_session_id() -> Optional[str]:
    status = DemoRuntime.get_instance().get_status()
    if status.get("state") in (DemoState.RUNNING.value, DemoState.PAUSED.value):
        return status.get("session_id")
    return None


def serialize_session(db: Session, s: ExamSession, counts: Optional[Dict[str, int]] = None,
                      live_id: Optional[str] = None) -> Dict[str, Any]:
    room = db.get(ExamRoom, s.room_id)
    exam = db.get(Exam, s.exam_id) if s.exam_id else None
    proctor = db.get(User, s.proctor_user_id) if s.proctor_user_id else None
    counts = counts if counts is not None else review_counts(db, [s.id]).get(s.id, {})
    approved = (
        db.query(SessionReportVersion)
        .filter(SessionReportVersion.session_id == s.id)
        .order_by(SessionReportVersion.version.desc())
        .first()
    )
    return {
        "id": s.id,
        "name": s.exam_name,
        "exam_id": s.exam_id,
        "exam_name": exam.name if exam else None,
        "room_id": s.room_id,
        "room_code": room.room_code if room else None,
        "room_name": room.name if room else None,
        "status": s.status,
        "is_live": live_id == s.id,
        "scheduled_start": _iso(s.scheduled_start),
        "scheduled_end": _iso(s.scheduled_end),
        "started_at": _iso(s.started_at),
        "ended_at": _iso(s.ended_at),
        "proctor": {"id": proctor.id, "display_name": proctor.display_name} if proctor else None,
        "total_events": sum(counts.values()),
        "review_counts": counts,
        "pending": counts.get("PENDING", 0),
        "run_id": session_run_id(db, s),
        "preset": session_preset(db, s),
        "report_version": approved.version if approved else None,
        "closed_at": _iso(s.closed_at),
        "closed_by": s.closed_by,
    }


def _get_session(db: Session, session_id: str) -> ExamSession:
    session = db.get(ExamSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


def _proctor_id(db: Session, user_id: Optional[str]) -> Optional[str]:
    if not user_id:
        return None
    if db.get(User, user_id) is None:
        raise HTTPException(status_code=400, detail="Unknown proctor")
    return user_id


# --- Exams -----------------------------------------------------------------------

@router.get("/exams")
def list_exams(db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    exams = db.query(Exam).order_by(Exam.exam_date.desc(), Exam.created_at.desc()).all()
    result = []
    for exam in exams:
        sessions = db.query(ExamSession).filter(ExamSession.exam_id == exam.id).all()
        by_status: Dict[str, int] = {}
        for s in sessions:
            by_status[s.status] = by_status.get(s.status, 0) + 1
        open_ids = [s.id for s in sessions if s.status != "CLOSED"]
        pending = (
            db.query(DetectionEvent)
            .filter(DetectionEvent.session_id.in_(open_ids or [""]), DetectionEvent.review_status == "PENDING")
            .count()
        )
        result.append({
            "id": exam.id, "name": exam.name, "subject_code": exam.subject_code, "exam_date": exam.exam_date,
            "description": exam.description, "status": exam.status, "session_count": len(sessions),
            "sessions_by_status": by_status, "pending": pending,
        })
    return result


@router.post("/exams", status_code=201)
def create_exam(payload: ExamIn, db: Session = Depends(get_db), user=Depends(manage),
                me: Optional[User] = Depends(current_user)) -> Dict[str, Any]:
    exam = Exam(name=payload.name.strip(), subject_code=payload.subject_code, exam_date=payload.exam_date,
                description=payload.description, created_by=actor_name(me))
    db.add(exam)
    db.flush()
    _audit(db, actor_name(me), "CREATE_EXAM", "EXAM", exam.id, payload.model_dump())
    db.commit()
    return {"id": exam.id, "name": exam.name}


@router.patch("/exams/{exam_id}")
def update_exam(exam_id: str, payload: ExamPatch, db: Session = Depends(get_db), user=Depends(manage),
                me: Optional[User] = Depends(current_user)) -> Dict[str, Any]:
    exam = db.get(Exam, exam_id)
    if exam is None:
        raise HTTPException(status_code=404, detail="Exam not found")
    changes = payload.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(exam, key, value)
    _audit(db, actor_name(me), "UPDATE_EXAM", "EXAM", exam.id, changes)
    db.commit()
    return {"id": exam.id, "name": exam.name, "status": exam.status}


@router.post("/exams/{exam_id}/sessions", status_code=201)
def schedule_sessions(exam_id: str, payload: ScheduleIn, db: Session = Depends(get_db), user=Depends(manage),
                      me: Optional[User] = Depends(current_user)) -> List[Dict[str, Any]]:
    """One session per selected room for the same time slot."""
    exam = db.get(Exam, exam_id)
    if exam is None:
        raise HTTPException(status_code=404, detail="Exam not found")
    if payload.scheduled_start and payload.scheduled_end and payload.scheduled_end <= payload.scheduled_start:
        raise HTTPException(status_code=400, detail="The session must end after it starts.")
    proctor_id = _proctor_id(db, payload.proctor_user_id)
    created = []
    for room_id in dict.fromkeys(payload.room_ids):
        room = db.get(ExamRoom, room_id)
        if room is None:
            raise HTTPException(status_code=400, detail=f"Unknown room {room_id}")
        session = ExamSession(
            room_id=room.id,
            exam_id=exam.id,
            exam_name=(payload.name or f"{exam.name} · {room.room_code or room.name}")[:255],
            subject_code=exam.subject_code,
            scheduled_start=payload.scheduled_start,
            scheduled_end=payload.scheduled_end,
            proctor_user_id=proctor_id,
            status="DRAFT",
            started_at=None,
        )
        db.add(session)
        db.flush()
        refresh_scheduled_status(db, session)
        _audit(db, actor_name(me), "SCHEDULE_SESSION", "SESSION", session.id,
               {"exam_id": exam.id, "room": room.room_code, "start": payload.scheduled_start})
        created.append(session)
    db.commit()
    return [serialize_session(db, s, {}) for s in created]


# --- Sessions --------------------------------------------------------------------

@router.get("/exam-sessions")
def list_sessions(
    exam_id: Optional[str] = Query(None, description="Exam id, or 'none' for sessions outside any exam"),
    status: Optional[str] = Query(None),
    room_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    query = db.query(ExamSession)
    if exam_id == "none":
        query = query.filter(ExamSession.exam_id.is_(None))
    elif exam_id:
        query = query.filter(ExamSession.exam_id == exam_id)
    if status:
        query = query.filter(ExamSession.status.in_(status.upper().split(",")))
    if room_id:
        query = query.filter(ExamSession.room_id == room_id)
    total = query.count()
    sessions = (
        query.order_by(ExamSession.scheduled_start.is_(None), ExamSession.scheduled_start.desc(),
                       ExamSession.started_at.desc())
        .limit(limit)
        .all()
    )
    counts = review_counts(db, [s.id for s in sessions])
    live_id = _live_session_id()
    return {"total": total, "items": [serialize_session(db, s, counts[s.id], live_id) for s in sessions]}


@router.get("/exam-sessions/{session_id}")
def get_session_detail(session_id: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    session = _get_session(db, session_id)
    return {**serialize_session(db, session, live_id=_live_session_id()), "readiness": readiness(db, session)}


@router.patch("/exam-sessions/{session_id}")
def update_session(session_id: str, payload: SessionPatch, db: Session = Depends(get_db), user=Depends(manage),
                   me: Optional[User] = Depends(current_user)) -> Dict[str, Any]:
    session = _get_session(db, session_id)
    if session.status == "CLOSED":
        raise HTTPException(status_code=409, detail="A closed session cannot be changed.")
    changes = payload.model_dump(exclude_unset=True)
    if "exam_id" in changes and changes["exam_id"] and db.get(Exam, changes["exam_id"]) is None:
        raise HTTPException(status_code=400, detail="Unknown exam")
    if "proctor_user_id" in changes:
        changes["proctor_user_id"] = _proctor_id(db, changes["proctor_user_id"])
    if "name" in changes:
        session.exam_name = (changes.pop("name") or session.exam_name)[:255]
    for key, value in changes.items():
        setattr(session, key, value)
    refresh_scheduled_status(db, session)
    _audit(db, actor_name(me), "UPDATE_SESSION", "SESSION", session.id, payload.model_dump(exclude_unset=True))
    db.commit()
    return serialize_session(db, session, live_id=_live_session_id())


@router.delete("/exam-sessions/{session_id}", status_code=204)
def delete_session(session_id: str, db: Session = Depends(get_db), user=Depends(manage),
                   me: Optional[User] = Depends(current_user)) -> None:
    session = _get_session(db, session_id)
    if session.status not in SCHEDULED_STATES or db.query(DetectionEvent).filter(DetectionEvent.session_id == session.id).count():
        raise HTTPException(status_code=409, detail="Only a session that has not run yet can be deleted.")
    _audit(db, actor_name(me), "DELETE_SESSION", "SESSION", session.id, {"name": session.exam_name})
    db.delete(session)
    db.commit()


@router.get("/exam-sessions/{session_id}/checklist")
def session_checklist(session_id: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    return readiness(db, _get_session(db, session_id))


@router.post("/exam-sessions/{session_id}/start")
def start_session_analysis(session_id: str, payload: StartIn, db: Session = Depends(get_db), user=Depends(manage),
                           me: Optional[User] = Depends(current_user)) -> Dict[str, Any]:
    """Run the analysis of this session's room video; its incidents are filed under the session."""
    session = _get_session(db, session_id)
    if session.status not in SCHEDULED_STATES:
        raise HTTPException(status_code=409, detail=f"The session is {session.status}; only a scheduled session can start.")
    check = readiness(db, session)
    if not check["ready"]:
        failed = [i["label"] for i in check["items"] if i["blocking"] and not i["ok"]]
        raise HTTPException(status_code=409, detail="Not ready: " + ", ".join(failed))
    if _live_session_id():
        raise HTTPException(status_code=409, detail="Another analysis is running. Stop it first.")
    mode = payload.mode.upper()
    if mode != "LIVE":
        raise HTTPException(status_code=400, detail="A session is analysed live; recorded replays are on the Live monitor page.")
    _audit(db, actor_name(me), "START_SESSION", "SESSION", session.id, {"mode": mode, "preset": check["preset"]})
    db.commit()
    try:
        status = DemoRuntime.get_instance().start(preset=check["preset"], mode=mode, session_id=session.id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    realtime_manager.broadcast_threadsafe({"type": "DEMO_STARTED", "status": status})
    return status


@router.post("/exam-sessions/{session_id}/stop")
def stop_session_analysis(session_id: str, db: Session = Depends(get_db), user=Depends(manage),
                          me: Optional[User] = Depends(current_user)) -> Dict[str, Any]:
    if _live_session_id() != session_id:
        raise HTTPException(status_code=409, detail="This session is not running.")
    status = DemoRuntime.get_instance().stop()
    _audit(db, actor_name(me), "STOP_SESSION", "SESSION", session_id, {})
    db.commit()
    realtime_manager.broadcast_threadsafe({"type": "DEMO_STATUS", "status": status})
    return status


@router.get("/rooms-overview")
def rooms_overview(db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    """Rooms with their Seat ROI count and video source, rooms that can be analysed first."""
    seat_counts = dict(
        db.query(SeatROI.room_id, func.count(SeatROI.id)).filter(SeatROI.enabled == True).group_by(SeatROI.room_id).all()  # noqa: E712
    )
    rooms = [
        {"id": r.id, "room_code": r.room_code, "name": r.name, "capacity": r.capacity,
         "seat_count": int(seat_counts.get(r.id, 0)), "preset": preset_for_room(r)}
        for r in db.query(ExamRoom).all()
    ]
    return sorted(rooms, key=lambda r: (-(r["seat_count"] > 0), r["room_code"] or ""))


@router.get("/rooms/{room_id}/seat-layout")
def seat_layout(room_id: str, camera_id: Optional[str] = None, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Seat ROI coverage, inferred neighbour links and overlapping ROIs of a room."""
    room = db.get(ExamRoom, room_id)
    if room is None:
        raise HTTPException(status_code=404, detail="Room not found")
    return room_seat_layout(db, room, camera_id)
