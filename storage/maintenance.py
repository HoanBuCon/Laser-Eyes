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
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

from sqlalchemy.orm import Session

from storage.db_models import (
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
    shutil.copy2(source, target)
    return target
