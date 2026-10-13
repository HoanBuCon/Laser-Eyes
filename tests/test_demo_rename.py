"""Existing installations move from the china* names to classroom-01..03."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from classroom_monitor.demo.config import DEMO_PRESETS
from storage.database import Base
from storage.db_models import AuditLog, Camera, EvidenceFile, DetectionEvent, ExamRoom, ExamSession, SeatROI
from storage.demo_rename import migrate_demo_names, rename_text

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture
def db():
    Base.metadata.create_all(bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def test_presets_use_the_new_names():
    assert list(DEMO_PRESETS) == ["classroom-01", "classroom-02", "classroom-03"]
    first = DEMO_PRESETS["classroom-01"]
    assert (first["title"], first["video_path"], first["room_code"], first["camera_id"]) == (
        "Classroom 01", "demo_video/classroom-01.mp4", "CLASSROOM-01", "CAM-CLASSROOM-01")


def test_text_mapping_covers_codes_paths_and_run_ids():
    assert rename_text("SEAT-ROOM-CHINA-03-15") == "SEAT-CLASSROOM-03-15"
    assert rename_text(r"data\demo_runs\DEMO-17-china1\evidence\a.mp4") == r"data\demo_runs\DEMO-17-classroom-01\evidence\a.mp4"
    assert rename_text("VIGIL AI LIVE Demo (CHINA2) - DEMO-1-china2") == "VIGIL AI LIVE Demo (CLASSROOM-02) - DEMO-1-classroom-02"
    assert rename_text("VIGIL China Classroom 3 Video") == "VIGIL Classroom 03 Video"
    assert rename_text("china10 untouched") == "china10 untouched"


def test_database_and_files_are_renamed_once_and_audit_is_kept(db, tmp_path):
    room = ExamRoom(room_code="ROOM-CHINA-01", name="China Classroom 1")
    db.add(room)
    db.flush()
    # An empty room under the new code (seeding ran before the rename) is replaced
    db.add(ExamRoom(room_code="CLASSROOM-01", name="Classroom 01"))
    db.add(Camera(room_id=room.id, name="VIGIL China Classroom 1 Video", source_uri="demo_video/china1_classroom.mp4"))
    db.add(SeatROI(room_id=room.id, seat_code="SEAT-ROOM-CHINA-01-07", polygon_json="[[0,0],[1,0],[1,1]]"))
    session = ExamSession(room_id=room.id, exam_name="VIGIL AI LIVE Demo (CHINA1) - DEMO-5-china1",
                          run_id="DEMO-5-china1", source_preset="china1", status="COMPLETED")
    db.add(session)
    db.flush()
    event = DetectionEvent(session_id=session.id, room_id=room.id, event_id="e1", behavior="X")
    db.add(event)
    db.flush()
    db.add(EvidenceFile(event_id=event.id, file_path=r"data\demo_runs\DEMO-5-china1\evidence\e1.mp4"))
    db.add(AuditLog(actor_id="x", action="REVIEW_EVENT", resource_type="EVENT", resource_id="ROOM-CHINA-01"))
    db.commit()

    videos = tmp_path / "demo_video"
    videos.mkdir()
    (videos / "china1_classroom.mp4").write_bytes(b"v")
    final = tmp_path / "demo_final" / "china1"
    final.mkdir(parents=True)
    (final / "events.json").write_text(json.dumps([{"seat_code": "SEAT-ROOM-CHINA-01-07", "room_id": "ROOM-CHINA-01",
                                                     "evidence_clip_path": r"data\demo_runs\DEMO-5-china1\evidence\e1.mp4"}]), encoding="utf-8")
    runs = tmp_path / "demo_runs"
    (runs / "DEMO-5-china1").mkdir(parents=True)
    (runs / "DEMO-5-china1" / "seat_timeline.json").write_text('{"seats": {"SEAT-ROOM-CHINA-01-07": [1]}}', encoding="utf-8")

    migrate_demo_names(db, "", videos, tmp_path / "demo_final", runs)
    db.expire_all()
    assert [r.room_code for r in db.query(ExamRoom).all()] == ["CLASSROOM-01"]
    assert db.query(ExamRoom).one().name == "Classroom 01"
    cam = db.query(Camera).one()
    assert (cam.name, cam.source_uri) == ("VIGIL Classroom 01 Video", "demo_video/classroom-01.mp4")
    assert db.query(SeatROI).one().seat_code == "SEAT-CLASSROOM-01-07"
    s = db.query(ExamSession).one()
    assert (s.exam_name, s.run_id, s.source_preset) == ("VIGIL AI LIVE Demo (CLASSROOM-01) - DEMO-5-classroom-01", "DEMO-5-classroom-01", "classroom-01")
    assert db.query(EvidenceFile).one().file_path == r"data\demo_runs\DEMO-5-classroom-01\evidence\e1.mp4"
    assert db.query(AuditLog).one().resource_id == "ROOM-CHINA-01"  # history is not rewritten

    assert (videos / "classroom-01.mp4").is_file() and not (videos / "china1_classroom.mp4").exists()
    package = json.loads((tmp_path / "demo_final" / "classroom-01" / "events.json").read_text(encoding="utf-8"))
    assert package[0]["seat_code"] == "SEAT-CLASSROOM-01-07" and "DEMO-5-classroom-01" in package[0]["evidence_clip_path"]
    timeline = (runs / "DEMO-5-classroom-01" / "seat_timeline.json").read_text(encoding="utf-8")
    assert "SEAT-CLASSROOM-01-07" in timeline

    # Running again changes nothing
    assert not any(migrate_demo_names(db, "", videos, tmp_path / "demo_final", runs).values())
