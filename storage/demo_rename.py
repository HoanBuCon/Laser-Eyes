"""One-time rename of the demo classrooms (china1..3 → classroom-01..03).

The demo videos, their rooms, cameras and seats used to carry "china" names
(``demo_video/china1_classroom.mp4``, ``ROOM-CHINA-01``,
``SEAT-ROOM-CHINA-01-15``, preset ``china1``).  They are now
``demo_video/classroom-01.mp4``, ``CLASSROOM-01``, ``SEAT-CLASSROOM-01-15``
and preset ``classroom-01``.

:func:`migrate_demo_names` brings an existing installation along: database
rows (rooms, cameras, Seat ROIs, sessions, evidence paths), the video files,
the replay packages and the run folders (names and the JSON inside).  It is
idempotent, does nothing once everything is renamed, backs the SQLite
database up before its first change and keeps the audit log untouched.  It
must run before ``server.ensure_calibration_sources``, which would otherwise
create empty rooms under the new codes next to the old ones.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from sqlalchemy import or_
from sqlalchemy.orm import Session

from storage.db_models import (
    BehaviorEpisodeDB,
    BehaviorPatternDB,
    Camera,
    DetectionEvent,
    EvidenceFile,
    ExamRoom,
    ExamSession,
    ProctorBookmark,
    SeatROI,
)

logger = logging.getLogger("DemoRename")
NUMBERS = (1, 2, 3)


def _pairs() -> List[Tuple[str, str]]:
    """Old → new text, most specific first (seat codes contain room codes)."""
    pairs: List[Tuple[str, str]] = []
    for n in NUMBERS:
        pairs += [
            (f"SEAT-ROOM-CHINA-0{n}-", f"SEAT-CLASSROOM-0{n}-"),
            (f"ROOM-CHINA-0{n}", f"CLASSROOM-0{n}"),
            (f"CAM-CHINA-0{n}", f"CAM-CLASSROOM-0{n}"),
            (f"china{n}_classroom.mp4", f"classroom-0{n}.mp4"),
            (f"China Classroom {n}", f"Classroom 0{n}"),
            (f"(CHINA{n})", f"(CLASSROOM-0{n})"),
            (f"ICTU-2026-CHINA{n}", f"ICTU-2026-CLASSROOM-0{n}"),
        ]
    return pairs


_PAIRS = _pairs()
# Preset ids inside paths and run ids: "demo_final/china1", "DEMO-1791514891-china1"
_PRESET_RE = re.compile(r"(?<=[-/\\\"' ])china([123])(?![0-9_])")
_OLD_MARKER = re.compile(r"china", re.IGNORECASE)


def rename_text(value: Optional[str]) -> Optional[str]:
    if not value or not _OLD_MARKER.search(value):
        return value
    for old, new in _PAIRS:
        value = value.replace(old, new)
    return _PRESET_RE.sub(lambda m: f"classroom-0{m.group(1)}", value)


def rename_preset(value: Optional[str]) -> Optional[str]:
    match = re.fullmatch(r"china([123])", value or "")
    return f"classroom-0{match.group(1)}" if match else value


# --- Database ------------------------------------------------------------------------

def _update_columns(db: Session, model, columns: List[str]) -> int:
    changed = 0
    filters = [getattr(model, c).ilike("%china%") for c in columns]
    for row in db.query(model).filter(or_(*filters)).all():
        for column in columns:
            old = getattr(row, column)
            new = rename_text(old)
            if new != old:
                setattr(row, column, new)
                changed += 1
    return changed


def _rename_rooms(db: Session) -> int:
    changed = 0
    for n in NUMBERS:
        old = db.query(ExamRoom).filter(ExamRoom.room_code == f"ROOM-CHINA-0{n}").first()
        if old is None:
            continue
        new_code = f"CLASSROOM-0{n}"
        existing = db.query(ExamRoom).filter(ExamRoom.room_code == new_code).first()
        if existing is not None:
            in_use = (
                db.query(SeatROI).filter(SeatROI.room_id == existing.id).count()
                + db.query(ExamSession).filter(ExamSession.room_id == existing.id).count()
            )
            if in_use:
                logger.error("Both %s and %s hold data; leaving %s for a person to merge", old.room_code, new_code, old.room_code)
                continue
            # An empty room created under the new code (seeding ran first): drop it
            db.query(Camera).filter(Camera.room_id == existing.id).delete(synchronize_session=False)
            db.delete(existing)
            db.flush()
        old.room_code = new_code
        old.name = rename_text(old.name)
        changed += 1
    return changed


def migrate_database(db: Session) -> Dict[str, int]:
    counts = {"rooms": _rename_rooms(db)}
    db.flush()
    counts["rooms"] += _update_columns(db, ExamRoom, ["name"])
    counts["cameras"] = _update_columns(db, Camera, ["name", "source_uri", "rtsp_url_protected"])
    counts["seats"] = _update_columns(db, SeatROI, ["seat_code", "seat_label", "context_json"])
    counts["sessions"] = _update_columns(db, ExamSession, ["exam_name", "subject_code", "run_id"])
    for session in db.query(ExamSession).filter(ExamSession.source_preset.ilike("china%")).all():
        session.source_preset = rename_preset(session.source_preset)
        counts["sessions"] += 1
    counts["evidence"] = _update_columns(db, EvidenceFile, ["file_path", "snapshot_path", "video_path"])
    counts["incidents"] = _update_columns(db, DetectionEvent, ["incident_metadata_json", "room_context", "bbox_json"])
    counts["bookmarks"] = _update_columns(db, ProctorBookmark, ["run_id", "source_ref", "subject_ref", "snapshot_path"])
    counts["episodes"] = _update_columns(db, BehaviorEpisodeDB, ["metadata_json"])
    counts["patterns"] = _update_columns(db, BehaviorPatternDB, ["target_neighbor_id", "metadata_json"])
    return counts


def database_needs_migration(db: Session) -> bool:
    return bool(
        db.query(ExamRoom).filter(ExamRoom.room_code.ilike("%CHINA%")).first()
        or db.query(Camera).filter(Camera.source_uri.ilike("%china%")).first()
        or db.query(SeatROI).filter(SeatROI.seat_code.ilike("%CHINA%")).first()
        or db.query(EvidenceFile).filter(EvidenceFile.file_path.ilike("%china%")).first()
        or db.query(ExamSession).filter(or_(ExamSession.exam_name.ilike("%china%"), ExamSession.source_preset.ilike("china%"))).first()
    )


# --- Files ------------------------------------------------------------------------------

def _rewrite_json_files(folder: Path) -> int:
    changed = 0
    for path in folder.rglob("*.json"):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        # JSON escapes backslashes in Windows paths; the patterns match both forms
        new = rename_text(text)
        if new != text:
            path.write_text(new, encoding="utf-8")
            changed += 1
    return changed


def _move(source: Path, target: Path) -> bool:
    if not source.exists() or target.exists():
        return False
    os.replace(source, target)
    return True


def migrate_files(video_dir: Path, final_root: Path, runs_root: Path) -> Dict[str, int]:
    counts = {"videos": 0, "replay_packages": 0, "runs": 0, "json_files": 0}
    for n in NUMBERS:
        counts["videos"] += _move(video_dir / f"china{n}_classroom.mp4", video_dir / f"classroom-0{n}.mp4")
        counts["replay_packages"] += _move(final_root / f"china{n}", final_root / f"classroom-0{n}")
    if runs_root.is_dir():
        for run in list(runs_root.iterdir()):
            new_name = rename_text(run.name)
            if run.is_dir() and new_name != run.name:
                counts["runs"] += _move(run, runs_root / new_name)
    for n in NUMBERS:
        package = final_root / f"classroom-0{n}"
        if package.is_dir():
            counts["json_files"] += _rewrite_json_files(package)
    if final_root.is_dir():
        for path in final_root.glob("*.json"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            if _OLD_MARKER.search(text):
                path.write_text(rename_text(text), encoding="utf-8")
                counts["json_files"] += 1
    if runs_root.is_dir():
        for run in runs_root.iterdir():
            if run.is_dir() and re.search(r"-classroom-0[123]$", run.name):
                counts["json_files"] += _rewrite_json_files(run)
    return counts


def migrate_demo_names(db: Session, database_url: str = "", video_dir: Path = Path("demo_video"),
                       final_root: Optional[Path] = None, runs_root: Optional[Path] = None) -> Dict[str, int]:
    """Rename everything from the china* names; safe to call on every start."""
    from classroom_monitor.demo.paths import demo_final_root, demo_runs_root
    from storage.maintenance import backup_sqlite

    counts = migrate_files(Path(video_dir), final_root or demo_final_root(), runs_root or demo_runs_root())
    if database_needs_migration(db):
        backup = backup_sqlite(database_url) if database_url else None
        if backup:
            logger.info("Database backed up to %s before renaming the demo classrooms", backup)
        try:
            counts.update(migrate_database(db))
            db.commit()
        except Exception:
            db.rollback()
            raise
    if any(counts.values()):
        logger.info("Demo classrooms renamed to classroom-01..03: %s", counts)
    return counts
