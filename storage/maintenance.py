"""Database maintenance: remove rooms left behind by tests and experiments.

Before the test suite was isolated (tests/conftest.py), every test run wrote
rooms such as "Phòng ROOM-ac31af01" or "Scoped Room" into the real database.
They have no Seat ROIs, so they can never be analysed, but they fill the
operations dashboard and its totals.

A room is a cleanup candidate when it has no Seat ROI and is not one of the
configured demo rooms.  Its cameras, sessions, incidents, evidence records,
reviews, bookmarks, episodes and patterns go with it.  Audit-log rows are kept
(the audit trail is append-only) and evidence files on disk are not touched.
"""

from __future__ import annotations

import datetime
import os
import re
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from storage.db_models import (
    AuditLog,
    BehaviorEpisodeDB,
    BehaviorPatternDB,
    Camera,
    DetectionEvent,
    EventReview,
    EvidenceFile,
    ExamRoom,
    ExamSession,
    ProctorBookmark,
    ProctorBookmarkReview,
    SeatROI,
    StagedRecordingSession,
)


@dataclass
class CleanupPlan:
    rooms: List[ExamRoom] = field(default_factory=list)
    session_ids: List[str] = field(default_factory=list)
    event_ids: List[str] = field(default_factory=list)
    camera_ids: List[str] = field(default_factory=list)
    bookmark_ids: List[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"Rooms without Seat ROIs to remove: {len(self.rooms)}",
            f"  sessions {len(self.session_ids)}, incidents {len(self.event_ids)}, "
            f"cameras {len(self.camera_ids)}, proctor bookmarks {len(self.bookmark_ids)}",
        ]
        for room in self.rooms[:15]:
            lines.append(f"  - {room.room_code or room.id}: {room.name}")
        if len(self.rooms) > 15:
            lines.append(f"  ... and {len(self.rooms) - 15} more")
        return "\n".join(lines)


def plan_test_room_cleanup(db: Session, keep_room_codes: Iterable[str] = ()) -> CleanupPlan:
    """Rooms that can never be analysed (no Seat ROI) and are not demo rooms."""
    keep = {code.upper() for code in keep_room_codes}
    rooms_with_seats = {room_id for (room_id,) in db.query(SeatROI.room_id).distinct()}
    plan = CleanupPlan()
    for room in db.query(ExamRoom).order_by(ExamRoom.created_at).all():
        if room.id in rooms_with_seats or (room.room_code or "").upper() in keep:
            continue
        plan.rooms.append(room)
    room_ids = [room.id for room in plan.rooms]
    if not room_ids:
        return plan
    plan.session_ids = [sid for (sid,) in db.query(ExamSession.id).filter(ExamSession.room_id.in_(room_ids))]
    plan.camera_ids = [cid for (cid,) in db.query(Camera.id).filter(Camera.room_id.in_(room_ids))]
    events = db.query(DetectionEvent.id).filter(
        (DetectionEvent.room_id.in_(room_ids)) | (DetectionEvent.session_id.in_(plan.session_ids or [""]))
    )
    plan.event_ids = [eid for (eid,) in events]
    plan.bookmark_ids = [
        bid for (bid,) in db.query(ProctorBookmark.id).filter(ProctorBookmark.session_id.in_(plan.session_ids or [""]))
    ]
    return plan


def apply_cleanup(db: Session, plan: CleanupPlan) -> None:
    """Delete everything in ``plan`` in dependency order, in one transaction."""
    def chunks(ids: List[str], size: int = 500):
        for i in range(0, len(ids), size):
            yield ids[i:i + size]

    try:
        for part in chunks(plan.event_ids):
            db.query(EventReview).filter(EventReview.event_id.in_(part)).delete(synchronize_session=False)
            db.query(EvidenceFile).filter(EvidenceFile.event_id.in_(part)).delete(synchronize_session=False)
            db.query(DetectionEvent).filter(DetectionEvent.id.in_(part)).delete(synchronize_session=False)
        for part in chunks(plan.bookmark_ids):
            db.query(ProctorBookmarkReview).filter(ProctorBookmarkReview.bookmark_id.in_(part)).delete(synchronize_session=False)
            db.query(ProctorBookmark).filter(ProctorBookmark.id.in_(part)).delete(synchronize_session=False)
        for part in chunks(plan.session_ids):
            db.query(BehaviorEpisodeDB).filter(BehaviorEpisodeDB.session_id.in_(part)).delete(synchronize_session=False)
            db.query(BehaviorPatternDB).filter(BehaviorPatternDB.session_id.in_(part)).delete(synchronize_session=False)
            db.query(ExamSession).filter(ExamSession.id.in_(part)).delete(synchronize_session=False)
        room_ids = [room.id for room in plan.rooms]
        for part in chunks(room_ids):
            db.query(StagedRecordingSession).filter(StagedRecordingSession.room_id.in_(part)).update(
                {StagedRecordingSession.room_id: None}, synchronize_session=False
            )
        for part in chunks(plan.camera_ids):
            db.query(Camera).filter(Camera.id.in_(part)).delete(synchronize_session=False)
        for part in chunks(room_ids):
            db.query(ExamRoom).filter(ExamRoom.id.in_(part)).delete(synchronize_session=False)
        db.commit()
    except Exception:
        db.rollback()
        raise


def backup_sqlite(database_url: str) -> Optional[Path]:
    """Copy a SQLite database file next to itself before a destructive change."""
    if not database_url.startswith("sqlite:///"):
        return None
    source = Path(database_url.replace("sqlite:///", "", 1))
    if not source.is_file():
        return None
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = source.with_name(f"{source.stem}.backup-{stamp}{source.suffix}")
    # SQLite's online backup includes pages still in the WAL file (a plain file
    # copy of a WAL database can miss recent commits).
    src = sqlite3.connect(str(source))
    dst = sqlite3.connect(str(target))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return target


# --- Demo / test maintenance (System page) -----------------------------------

# Matches both separators, also as JSON-escaped "\\" in replay packages
_RUN_DIR_IN_PATH = re.compile(r"demo_runs[/\\]+([^/\\\"']+)")


def directory_size(path: Path) -> Tuple[int, int]:
    """(bytes, files) under ``path``; a missing directory is empty."""
    total = files = 0
    if not Path(path).exists():
        return 0, 0
    for root, _dirs, names in os.walk(path):
        for name in names:
            try:
                total += (Path(root) / name).stat().st_size
                files += 1
            except OSError:
                pass
    return total, files


def table_counts(db: Session) -> Dict[str, int]:
    models = {
        "rooms": ExamRoom, "seat_rois": SeatROI, "cameras": Camera, "sessions": ExamSession,
        "incidents": DetectionEvent, "evidence_files": EvidenceFile, "reviews": EventReview,
        "bookmarks": ProctorBookmark, "episodes": BehaviorEpisodeDB, "patterns": BehaviorPatternDB,
        "audit_log": AuditLog,
    }
    return {name: db.query(model).count() for name, model in models.items()}


def referenced_run_ids(db: Session) -> Set[str]:
    """Run directories that still hold evidence for incidents in the database."""
    ids: Set[str] = set()
    for (file_path,) in db.query(EvidenceFile.file_path):
        match = _RUN_DIR_IN_PATH.search(file_path or "")
        if match:
            ids.add(match.group(1))
    return ids


def run_ids_in_files(root: Path, pattern: str = "*.json") -> Set[str]:
    """Run directories named in files under ``root`` (replay packages point at run evidence)."""
    ids: Set[str] = set()
    root = Path(root)
    if not root.is_dir():
        return ids
    for path in root.rglob(pattern):
        try:
            ids.update(_RUN_DIR_IN_PATH.findall(path.read_text(encoding="utf-8", errors="ignore")))
        except OSError:
            continue
    return ids


def orphan_run_dirs(
    db: Session, runs_root: Path, active_run_id: str = "", also_keep: Iterable[str] = ()
) -> List[Path]:
    """Run directories nothing points to (safe to delete), oldest first."""
    runs_root = Path(runs_root)
    if not runs_root.is_dir():
        return []
    keep = referenced_run_ids(db) | set(also_keep) | ({active_run_id} if active_run_id else set())
    dirs = [d for d in runs_root.iterdir() if d.is_dir() and d.name not in keep]
    return sorted(dirs, key=lambda d: d.stat().st_mtime)


def remove_directories(paths: Iterable[Path]) -> int:
    removed = 0
    for path in paths:
        shutil.rmtree(path, ignore_errors=True)
        if not Path(path).exists():
            removed += 1
    return removed


def clear_directory(path: Path) -> int:
    """Delete everything inside ``path`` (the directory itself is kept)."""
    path = Path(path)
    if not path.is_dir():
        return 0
    removed = 0
    for child in path.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            try:
                child.unlink()
            except OSError:
                continue
        removed += 1
    return removed


def reset_incident_data(db: Session) -> Dict[str, int]:
    """Delete every session and what it produced; rooms, cameras and Seat ROIs stay.

    The audit log is kept.  Run directories on disk become orphans and can be
    purged afterwards with :func:`orphan_run_dirs`.
    """
    counts = {
        "incidents": db.query(DetectionEvent).count(),
        "sessions": db.query(ExamSession).count(),
        "bookmarks": db.query(ProctorBookmark).count(),
    }
    try:
        db.query(EventReview).delete(synchronize_session=False)
        db.query(EvidenceFile).delete(synchronize_session=False)
        db.query(DetectionEvent).delete(synchronize_session=False)
        db.query(ProctorBookmarkReview).delete(synchronize_session=False)
        db.query(ProctorBookmark).delete(synchronize_session=False)
        db.query(BehaviorEpisodeDB).delete(synchronize_session=False)
        db.query(BehaviorPatternDB).delete(synchronize_session=False)
        db.query(ExamSession).delete(synchronize_session=False)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return counts
