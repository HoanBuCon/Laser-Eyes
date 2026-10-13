"""Exam sessions from scheduling to closing: readiness, video source, run data.

A session is scheduled as DRAFT, becomes READY when its room can be analysed
(Seat ROIs, a camera/video source), RUNNING while an analysis runs, then
COMPLETED / STOPPED / FAILED / INTERRUPTED (see ``session_lifecycle``), and
CLOSED once a chief proctor approves its report.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from classroom_monitor.demo.config import DEMO_PRESETS
from classroom_monitor.demo.paths import demo_final_root, demo_runs_root, replay_package_dir
from storage.db_models import DetectionEvent, EvidenceFile, ExamRoom, ExamSession, SeatROI

SCHEDULED_STATES = ("DRAFT", "READY")
ACTIVE_STATES = ("RUNNING", "STOPPING")
FINISHED_STATES = ("COMPLETED", "STOPPED", "FAILED", "INTERRUPTED")
CLOSED_STATE = "CLOSED"
MIN_COVERAGE = 0.5
MIN_FREE_DISK_BYTES = 2 * 1024 ** 3
_RUN_IN_TEXT = re.compile(r"(DEMO-\d+-[A-Za-z0-9_]+)")


def preset_for_room(room: Optional[ExamRoom]) -> Optional[str]:
    """The demo preset whose camera video belongs to this room, if any."""
    if room is None:
        return None
    for name, preset in DEMO_PRESETS.items():
        if (preset.get("room_code") or "").upper() == (room.room_code or "").upper():
            return name
    return None


def session_preset(db: Session, session: ExamSession) -> Optional[str]:
    if session.source_preset and session.source_preset in DEMO_PRESETS:
        return session.source_preset
    return preset_for_room(db.get(ExamRoom, session.room_id))


def session_run_id(db: Session, session: ExamSession) -> Optional[str]:
    """The analysis run of the session (stored since phase 2, derived for older sessions)."""
    if session.run_id:
        return session.run_id
    match = _RUN_IN_TEXT.search(session.exam_name or "")
    if match:
        return match.group(1)
    paths = (
        db.query(EvidenceFile.file_path)
        .join(DetectionEvent, DetectionEvent.id == EvidenceFile.event_id)
        .filter(DetectionEvent.session_id == session.id)
        .limit(5)
        .all()
    )
    for (path,) in paths:
        match = _RUN_IN_TEXT.search(path or "")
        if match:
            return match.group(1)
    return None


def session_artifact_dir(db: Session, session: ExamSession) -> Optional[Path]:
    """Folder holding the run's result video and timeline; the replay package as fallback."""
    run_id = session_run_id(db, session)
    if run_id and (demo_runs_root() / run_id).is_dir():
        return demo_runs_root() / run_id
    preset = session_preset(db, session)
    if preset and replay_package_dir(preset).is_dir():
        return replay_package_dir(preset)
    return None


def review_counts(db: Session, session_ids: List[str]) -> Dict[str, Dict[str, int]]:
    counts: Dict[str, Dict[str, int]] = {sid: {} for sid in session_ids}
    if not session_ids:
        return counts
    rows = (
        db.query(DetectionEvent.session_id, DetectionEvent.review_status, func.count(DetectionEvent.id))
        .filter(DetectionEvent.session_id.in_(session_ids))
        .group_by(DetectionEvent.session_id, DetectionEvent.review_status)
        .all()
    )
    for session_id, status, n in rows:
        counts[session_id][status or "PENDING"] = int(n)
    return counts


def readiness(db: Session, session: ExamSession) -> Dict[str, Any]:
    """Checklist before a session can run; ``ready`` when no blocking item fails."""
    room = db.get(ExamRoom, session.room_id)
    seats = db.query(SeatROI).filter(SeatROI.room_id == session.room_id, SeatROI.enabled == True).count()  # noqa: E712
    capacity = int(room.capacity or 0) if room else 0
    preset = session_preset(db, session)
    video = Path(DEMO_PRESETS[preset]["video_path"]) if preset else None
    try:
        free = shutil.disk_usage(demo_runs_root().resolve().anchor or ".").free
    except OSError:
        free = None
    coverage = seats / capacity if capacity else None
    items = [
        {"key": "seat_rois", "label": "Seat ROIs drawn", "ok": seats > 0, "blocking": True,
         "detail": f"{seats} seats" if seats else "Draw seats on the calibration page"},
        {"key": "coverage", "label": f"ROI coverage ≥ {int(MIN_COVERAGE * 100)} %", "ok": coverage is None or coverage >= MIN_COVERAGE,
         "blocking": False, "detail": f"{seats}/{capacity} seats" if capacity else "Room capacity not set"},
        {"key": "video_source", "label": "Camera / video source", "ok": bool(video and video.is_file()), "blocking": True,
         "detail": str(video) if video else "No video source configured for this room"},
        {"key": "disk", "label": "Free disk ≥ 2 GB", "ok": free is None or free >= MIN_FREE_DISK_BYTES, "blocking": False,
         "detail": f"{free / 1024 ** 3:.1f} GB free" if free is not None else "unknown"},
    ]
    return {"ready": all(i["ok"] for i in items if i["blocking"]), "items": items, "preset": preset}


def refresh_scheduled_status(db: Session, session: ExamSession) -> None:
    """DRAFT ⇄ READY follows the checklist; other states are left alone."""
    if session.status in SCHEDULED_STATES:
        session.status = "READY" if readiness(db, session)["ready"] else "DRAFT"


def incident_meta(event: DetectionEvent) -> Dict[str, Any]:
    try:
        return json.loads(event.incident_metadata_json) if event.incident_metadata_json else {}
    except ValueError:
        return {}
