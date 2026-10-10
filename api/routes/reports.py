"""Session playback, session reports (approve → frozen version) and summaries."""

from __future__ import annotations

import csv
import datetime
import io
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.auth import actor_name, require_roles
from api.routes.proctor import _serialize_ai_event
from classroom_monitor.demo.config import DEMO_PRESETS
from classroom_monitor.demo.paths import demo_final_root, demo_runs_root
from classroom_monitor.demo.seat_timeline import load_timeline
from classroom_monitor.evidence_playback import PlaybackUnavailable, ensure_browser_playback
from storage.database import get_db
from storage.db_models import AuditLog, DetectionEvent, Exam, ExamRoom, ExamSession, SessionReportVersion, User
from storage.exam_workflow import FINISHED_STATES, review_counts, session_artifact_dir, session_preset
from storage.session_report import ai_quality, build_session_report, canonical_sha256, latest_version

router = APIRouter(tags=["Playback & reports"])


class ApproveIn(BaseModel):
    note: Optional[str] = None


class ReopenIn(BaseModel):
    reason: str = ""


def _session(db: Session, session_id: str) -> ExamSession:
    session = db.get(ExamSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


# --- Playback ----------------------------------------------------------------------

@router.get("/exam-sessions/{session_id}/playback")
def session_playback(session_id: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Everything the playback page draws: video sources, incidents and the seat timeline."""
    session = _session(db, session_id)
    preset = session_preset(db, session)
    artifacts = session_artifact_dir(db, session)
    events = (
        db.query(DetectionEvent)
        .filter(DetectionEvent.session_id == session.id)
        .order_by(DetectionEvent.created_at)
        .all()
    )
    incidents = []
    frame_size = None
    for event in events:
        item = _serialize_ai_event(event)
        focus = item.get("focus") or {}
        frame_size = frame_size or focus.get("frame_size")
        incidents.append({
            "id": event.id,
            "event_id": event.event_id,
            "seat_code": item["subject_ref"],
            "pattern": event.primary_pattern or item["primary_signal"],
            "severity": event.severity,
            "risk": event.risk_score,
            "review_status": item["review_status"],
            "reviewer_id": item["reviewer_id"],
            "behavior_start_ms": item["behavior_start_ms"],
            "flagged_ms": item["first_seen_ms"],
            "last_seen_ms": item["last_seen_ms"],
            "focus": focus or None,
            "snapshot_url": item["snapshot_url"],
            "video_url": item["video_url"],
        })
    if frame_size is None and preset:
        res = str(DEMO_PRESETS[preset].get("resolution") or "")
        if "x" in res:
            frame_size = [int(v) for v in res.split("x")]
    source_ok = bool(preset and Path(DEMO_PRESETS[preset]["video_path"]).is_file())
    rendered = artifacts / "result.mp4" if artifacts else None
    room = db.get(ExamRoom, session.room_id)
    return {
        "session": {"id": session.id, "name": session.exam_name, "status": session.status,
                    "room_code": room.room_code if room else None,
                    "started_at": session.started_at.isoformat() if session.started_at else None},
        "video": {
            "source_url": f"/api/v1/demo/presets/{preset}/source" if source_ok else None,
            "rendered_url": f"/api/v1/exam-sessions/{session.id}/playback/rendered" if rendered and rendered.is_file() else None,
        },
        "frame_size": frame_size,
        "incidents": incidents,
        "timeline": load_timeline(artifacts) if artifacts else None,
    }


@router.get("/exam-sessions/{session_id}/playback/rendered")
def session_rendered_video(session_id: str, db: Session = Depends(get_db)):
    """The analysis video with its drawn overlay (converted to H.264 once, then cached)."""
    artifacts = session_artifact_dir(db, _session(db, session_id))
    path = (artifacts / "result.mp4").resolve() if artifacts else None
    roots = [demo_runs_root().resolve(), demo_final_root().resolve()]
    if path is None or not path.is_file() or not any(root in path.parents for root in roots):
        raise HTTPException(status_code=404, detail="No rendered video for this session")
    try:
        playable, _ = ensure_browser_playback(path)
    except PlaybackUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return FileResponse(str(playable), media_type="video/mp4")


# --- Session report --------------------------------------------------------------------

@router.get("/reports/sessions/{session_id}")
def session_report(session_id: str, version: Optional[int] = None, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """The live report, or a frozen approved version with ``?version=N``."""
    session = _session(db, session_id)
    versions = (
        db.query(SessionReportVersion)
        .filter(SessionReportVersion.session_id == session.id)
        .order_by(SessionReportVersion.version)
        .all()
    )
    listing = [{"version": v.version, "approved_by": v.approved_by, "approved_at": v.approved_at.isoformat(),
                "sha256": v.content_sha256} for v in versions]
    if version is not None:
        frozen = next((v for v in versions if v.version == version), None)
        if frozen is None:
            raise HTTPException(status_code=404, detail="No such report version")
        return {"report": json.loads(frozen.content_json), "frozen": listing[versions.index(frozen)], "versions": listing}
    return {"report": build_session_report(db, session, _serialize_ai_event), "frozen": None, "versions": listing}


@router.post("/reports/sessions/{session_id}/approve")
def approve_session_report(session_id: str, payload: ApproveIn, db: Session = Depends(get_db),
                           user: User = Depends(require_roles("CHIEF", always=True))) -> Dict[str, Any]:
    """Freeze the report (hash) and close the session.  Pending incidents need a written reason."""
    session = _session(db, session_id)
    if session.status == "CLOSED":
        raise HTTPException(status_code=409, detail="This session is already approved and closed. Reopen it to change decisions, then approve again.")
    if session.status not in FINISHED_STATES:
        raise HTTPException(status_code=409, detail="Only a finished session can be approved.")
    report = build_session_report(db, session, _serialize_ai_event)
    pending = report["incidents"]["by_review_status"].get("PENDING", 0)
    note = (payload.note or "").strip()
    if pending and not note:
        raise HTTPException(status_code=409, detail=f"{pending} incidents are still pending. Review them or write why the session is closed anyway.")
    now = datetime.datetime.utcnow()
    report["approval"] = {"approved_by": actor_name(user), "approved_at": now.isoformat(), "note": note,
                          "pending_at_approval": pending}
    previous = latest_version(db, session.id)
    sha = canonical_sha256(report)
    record = SessionReportVersion(session_id=session.id, version=(previous.version + 1) if previous else 1,
                                  content_json=json.dumps(report, default=str), content_sha256=sha,
                                  approved_by=actor_name(user), approved_at=now)
    db.add(record)
    session.status = "CLOSED"
    session.closed_at = now
    session.closed_by = actor_name(user)
    session.close_note = note or None
    db.add(AuditLog(actor_id=actor_name(user), action="APPROVE_SESSION_REPORT", resource_type="SESSION",
                    resource_id=session.id, metadata_json=json.dumps({"version": record.version, "sha256": sha,
                                                                      "pending": pending, "note": note})))
    db.commit()
    return {"version": record.version, "sha256": sha, "status": session.status}


@router.post("/reports/sessions/{session_id}/reopen")
def reopen_session(session_id: str, payload: ReopenIn, db: Session = Depends(get_db),
                   user: User = Depends(require_roles("CHIEF", always=True))) -> Dict[str, Any]:
    """Unlock a closed session so decisions can change (e.g. an appeal).

    Approved versions stay as they are; approving again creates the next one.
    The session returns to the state it had when it was first approved.
    """
    session = _session(db, session_id)
    if session.status != "CLOSED":
        raise HTTPException(status_code=409, detail="Only a closed session can be reopened.")
    reason = (payload.reason or "").strip()
    if len(reason) < 5:
        raise HTTPException(status_code=400, detail="Write why the session is reopened (e.g. the appeal it answers).")
    first = (
        db.query(SessionReportVersion)
        .filter(SessionReportVersion.session_id == session.id)
        .order_by(SessionReportVersion.version)
        .first()
    )
    previous = json.loads(first.content_json).get("session", {}).get("status") if first else None
    session.status = previous if previous in FINISHED_STATES else "COMPLETED"
    closed_by, closed_at = session.closed_by, session.closed_at
    session.closed_at = None
    session.closed_by = None
    session.close_note = None
    db.add(AuditLog(actor_id=actor_name(user), action="REOPEN_SESSION", resource_type="SESSION", resource_id=session.id,
                    metadata_json=json.dumps({"reason": reason, "status": session.status, "was_closed_by": closed_by,
                                              "was_closed_at": closed_at.isoformat() if closed_at else None})))
    db.commit()
    return {"status": session.status}


@router.get("/reports/sessions/{session_id}/incidents.csv")
def session_incidents_csv(session_id: str, db: Session = Depends(get_db)) -> Response:
    session = _session(db, session_id)
    events = db.query(DetectionEvent).filter(DetectionEvent.session_id == session.id).order_by(DetectionEvent.created_at).all()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["incident_id", "seat", "pattern", "severity", "risk", "behavior_start_s", "flagged_s",
                     "last_seen_s", "review_status", "reviewer", "reason_code", "note", "reviewed_at", "evidence_sha256"])
    for event in events:
        item = _serialize_ai_event(event)
        secs = lambda ms: f"{ms / 1000:.1f}" if isinstance(ms, (int, float)) else ""  # noqa: E731
        writer.writerow([
            event.event_id, item["subject_ref"] or "", event.primary_pattern or item["primary_signal"], event.severity,
            event.risk_score, secs(item["behavior_start_ms"]), secs(item["first_seen_ms"]), secs(item["last_seen_ms"]),
            item["review_status"], item["reviewer_id"] or "", item["reason_code"] or "", item["review_note"] or "",
            item["reviewed_at"] or "", item["sha256"] or "",
        ])
    name = f"session-{(session.exam_name or session.id)[:40]}".replace(" ", "_").replace("/", "-")
    return Response(content="﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})


@router.get("/reports/incidents/{incident_id}/record")
def incident_record(incident_id: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Data for the printable record of one confirmed incident."""
    event = db.get(DetectionEvent, incident_id) or db.query(DetectionEvent).filter(DetectionEvent.event_id == incident_id).first()
    if event is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    if event.review_status != "CONFIRMED":
        raise HTTPException(status_code=409, detail="A record is made only for an incident a proctor confirmed.")
    session = db.get(ExamSession, event.session_id)
    report = build_session_report(db, session, _serialize_ai_event)
    item = next(c for c in report["confirmed"] if c["id"] == event.id)
    return {"incident": item, "session": report["session"], "exam": report["exam"], "room": report["room"],
            "proctor": report["proctor"]}


# --- Exam summary and AI quality ------------------------------------------------------------

@router.get("/reports/exams/{exam_id}")
def exam_summary(exam_id: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    exam = db.get(Exam, exam_id)
    if exam is None:
        raise HTTPException(status_code=404, detail="Exam not found")
    sessions = db.query(ExamSession).filter(ExamSession.exam_id == exam.id).all()
    counts = review_counts(db, [s.id for s in sessions])
    rows = []
    for s in sessions:
        room = db.get(ExamRoom, s.room_id)
        c = counts[s.id]
        version = latest_version(db, s.id)
        rows.append({"session_id": s.id, "name": s.exam_name, "room_code": room.room_code if room else None,
                     "status": s.status, "total": sum(c.values()), "pending": c.get("PENDING", 0),
                     "confirmed": c.get("CONFIRMED", 0), "rejected": c.get("REJECTED", 0),
                     "inconclusive": c.get("INCONCLUSIVE", 0), "report_version": version.version if version else None})
    events = db.query(DetectionEvent).filter(DetectionEvent.session_id.in_([s.id for s in sessions] or [""])).all()
    totals = {k: sum(r[k] for r in rows) for k in ("total", "pending", "confirmed", "rejected", "inconclusive")}
    return {"exam": {"id": exam.id, "name": exam.name, "subject_code": exam.subject_code, "exam_date": exam.exam_date},
            "sessions": rows, "totals": totals, "ai_quality": ai_quality(events)}


@router.get("/reports/ai-quality")
def ai_quality_report(exam_id: Optional[str] = Query(None), db: Session = Depends(get_db)) -> Dict[str, Any]:
    """How proctors decided on each behaviour pattern; rejected ones are candidates for relabelling."""
    query = db.query(DetectionEvent)
    if exam_id:
        ids = [sid for (sid,) in db.query(ExamSession.id).filter(ExamSession.exam_id == exam_id)]
        query = query.filter(DetectionEvent.session_id.in_(ids or [""]))
    events = query.all()
    rejected = sorted((e for e in events if e.review_status == "REJECTED"), key=lambda e: e.created_at or datetime.datetime.min, reverse=True)[:25]
    return {
        "patterns": ai_quality(events),
        "recent_rejected": [
            {"id": e.id, "event_id": e.event_id, "pattern": e.primary_pattern or e.behavior, "risk": e.risk_score,
             "session_id": e.session_id, "reason_code": e.review.reason_code if e.review else None,
             "created_at": e.created_at.isoformat() if e.created_at else None}
            for e in rejected
        ],
    }
