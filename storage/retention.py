"""Retention: the media of an exam session is deleted a fixed time after the session ended.

What is deleted (the personal data): VIGIL One whole-exam videos, uploaded segments, clips,
snapshots and analysis traces; the Classroom run folder of the session (result video, evidence
clips, per-frame trace); proctor bookmark pictures.

What stays (the record): sessions, incidents, review decisions and their reasons, SHA-256
digests, reports and the audit log. Evidence rows are marked ``PURGED`` so the review page says
why a clip is gone.

A session holding a **confirmed** incident or bookmark is not purged: its evidence may be needed
for an appeal. It stays on hold until an administrator deals with it.

``VIGIL_RETENTION_DAYS`` (default 90; ``0`` or ``off`` = keep everything). Only files inside the
known data roots are touched; replay packages and demo videos never are.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from sqlalchemy.orm import Session

from storage.db_models import AuditLog, DetectionEvent, EvidenceFile, ExamSession, ProctorBookmark

logger = logging.getLogger(__name__)

DEFAULT_DAYS = 90
CLOSED_STATES = ("COMPLETED", "STOPPED", "FAILED", "INTERRUPTED", "CLOSED")
PURGED_NOTE = "Đã xóa theo thời hạn lưu trữ ({days} ngày sau khi phiên thi kết thúc)."


def retention_days() -> Optional[int]:
    """Days the media is kept; None = retention switched off."""
    raw = os.getenv("VIGIL_RETENTION_DAYS", "").strip().lower()
    if raw in ("0", "off", "none", "never"):
        return None
    try:
        days = int(raw) if raw else DEFAULT_DAYS
    except ValueError:
        logger.warning("VIGIL_RETENTION_DAYS=%r is not a number: using %d days", raw, DEFAULT_DAYS)
        days = DEFAULT_DAYS
    return days if days > 0 else None


def _roots() -> List[Path]:
    """The folders retention may delete from (never the replay packages or demo videos)."""
    from classroom_monitor.demo.paths import demo_runs_root
    from storage.one_service import one_evidence_root
    from storage.proctor_service import evidence_root as bookmark_root

    return [one_evidence_root(), demo_runs_root().resolve(), bookmark_root()]


def _inside(path: Path, roots: Iterable[Path]) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    return any(resolved != root and root in resolved.parents for root in roots)


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


@dataclass
class SessionPurge:
    session_id: str
    name: str
    ended_at: datetime.datetime
    paths: List[Path] = field(default_factory=list)
    bytes: int = 0


@dataclass
class RetentionPlan:
    days: Optional[int]
    cutoff: Optional[datetime.datetime]
    due: List[SessionPurge] = field(default_factory=list)
    held: List[str] = field(default_factory=list)   # sessions kept for a confirmed incident

    @property
    def bytes(self) -> int:
        return sum(item.bytes for item in self.due)

    def summary(self) -> Dict[str, object]:
        return {
            "days": self.days,
            "cutoff": self.cutoff.isoformat() if self.cutoff else None,
            "due_sessions": len(self.due),
            "due_bytes": self.bytes,
            "held_sessions": len(self.held),
            "sessions": [{"id": d.session_id, "name": d.name, "ended_at": d.ended_at.isoformat(),
                          "files": len(d.paths), "bytes": d.bytes} for d in self.due[:50]],
        }


def _session_paths(db: Session, session: ExamSession, roots: List[Path]) -> List[Path]:
    from classroom_monitor.demo.paths import demo_runs_root
    from storage.exam_workflow import session_run_id
    from storage.one_service import one_evidence_root
    from storage.proctor_service import evidence_root as bookmark_root

    candidates: List[Path] = [one_evidence_root() / session.id, bookmark_root() / session.id]
    run_id = session_run_id(db, session)
    if run_id:
        candidates.append(demo_runs_root() / run_id)
    if session.video_path:
        candidates.append(Path(session.video_path))
    rows = (
        db.query(EvidenceFile)
        .join(DetectionEvent, DetectionEvent.id == EvidenceFile.event_id)
        .filter(DetectionEvent.session_id == session.id)
        .all()
    )
    for row in rows:
        candidates.extend(Path(p) for p in (row.file_path, row.snapshot_path, row.video_path) if p)
    for bookmark in db.query(ProctorBookmark).filter(ProctorBookmark.session_id == session.id):
        if bookmark.snapshot_path:
            candidates.append(Path(bookmark.snapshot_path))
    found: List[Path] = []
    for path in candidates:
        if path.exists() and _inside(path, roots) and not any(path.resolve() == p.resolve() or p in path.parents
                                                                for p in found):
            found.append(path)
    # a folder already listed covers the files inside it
    return [p for p in found if not any(other != p and other in p.parents for other in found)]


def _held(db: Session, session_id: str) -> bool:
    confirmed = (db.query(DetectionEvent)
                 .filter(DetectionEvent.session_id == session_id, DetectionEvent.review_status == "CONFIRMED")
                 .first())
    if confirmed is not None:
        return True
    return db.query(ProctorBookmark).filter(ProctorBookmark.session_id == session_id,
                                            ProctorBookmark.review_decision == "CONFIRMED").first() is not None


def plan_retention(db: Session, now: Optional[datetime.datetime] = None) -> RetentionPlan:
    days = retention_days()
    if days is None:
        return RetentionPlan(days=None, cutoff=None)
    now = now or datetime.datetime.utcnow()
    cutoff = now - datetime.timedelta(days=days)
    plan = RetentionPlan(days=days, cutoff=cutoff)
    roots = _roots()
    sessions = (
        db.query(ExamSession)
        .filter(ExamSession.media_purged_at.is_(None), ExamSession.status.in_(CLOSED_STATES))
        .all()
    )
    for session in sessions:
        # the latest end the session has (its report may be closed after it ended); the start only
        # for a session that never recorded an end
        ends = [t for t in (session.ended_at, session.closed_at) if t]
        ended = max(ends) if ends else session.started_at
        if ended is None or ended >= cutoff:
            continue
        if _held(db, session.id):
            plan.held.append(session.id)
            continue
        paths = _session_paths(db, session, roots)
        item = SessionPurge(session.id, session.exam_name or session.id, ended, paths,
                            sum(_size(p) for p in paths))
        plan.due.append(item)
    return plan


def apply_retention(db: Session, plan: RetentionPlan, actor: str = "system") -> Dict[str, int]:
    """Delete the plan's files and mark what was deleted. Rows stay; the audit log records it."""
    if plan.days is None:
        return {"sessions": 0, "files": 0, "bytes": 0}
    roots = _roots()
    note = PURGED_NOTE.format(days=plan.days)
    now = datetime.datetime.utcnow()
    done = {"sessions": 0, "files": 0, "bytes": 0}
    for item in plan.due:
        removed = 0
        for path in item.paths:
            if not _inside(path, roots):  # checked again: never outside the data roots
                continue
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                elif path.exists():
                    path.unlink()
                removed += 1
            except OSError as exc:
                logger.warning("Retention: could not delete %s: %s", path, exc)
        session = db.get(ExamSession, item.session_id)
        if session is None:
            continue
        session.media_purged_at = now
        if session.video_path or session.video_status:
            session.video_status, session.video_path = "PURGED", None
        rows = (
            db.query(EvidenceFile)
            .join(DetectionEvent, DetectionEvent.id == EvidenceFile.event_id)
            .filter(DetectionEvent.session_id == session.id)
            .all()
        )
        for row in rows:
            row.status, row.error_message = "PURGED", note
        for bookmark in db.query(ProctorBookmark).filter(ProctorBookmark.session_id == session.id):
            if bookmark.snapshot_path:
                bookmark.evidence_status, bookmark.error_message = "PURGED", note
        db.add(AuditLog(actor_id=actor, action="RETENTION_PURGE", resource_type="EXAM_SESSION",
                        resource_id=session.id, metadata_json=json.dumps(
                            {"days": plan.days, "ended_at": item.ended_at.isoformat(), "paths": removed,
                             "bytes": item.bytes}, ensure_ascii=False)))
        db.commit()
        done["sessions"] += 1
        done["files"] += removed
        done["bytes"] += item.bytes
    if done["sessions"]:
        _clear_playback_cache()
    return done


def _clear_playback_cache() -> None:
    """Browser copies of evidence clips (a cache): rebuilt on demand, so cleared after a purge."""
    from classroom_monitor.evidence_playback import playback_cache_root

    root = playback_cache_root()
    if not root.is_dir():
        return
    for child in root.iterdir():
        try:
            shutil.rmtree(child) if child.is_dir() else child.unlink()
        except OSError:
            pass


def run_retention(db: Session, actor: str = "system") -> Dict[str, int]:
    plan = plan_retention(db)
    result = apply_retention(db, plan, actor=actor)
    if result["sessions"] or plan.held:
        logger.info("Retention (%s days): purged %d session(s), %d path(s), %.1f MB; %d held for a confirmed incident",
                    plan.days, result["sessions"], result["files"], result["bytes"] / 1e6, len(plan.held))
    return result
