"""Retention: a session's media is deleted VIGIL_RETENTION_DAYS after it ended; its records stay,
a session with a confirmed incident is held, and nothing outside the data roots is touched."""

from __future__ import annotations

import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from storage.database import Base
from storage.db_models import AuditLog, DetectionEvent, EvidenceFile, ExamRoom, ExamSession
from storage import retention

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
NOW = datetime.datetime(2026, 10, 24, 12, 0)


@pytest.fixture
def db():
    Base.metadata.create_all(bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def roots(tmp_path, monkeypatch):
    paths = {name: tmp_path / name for name in ("one", "runs", "bookmarks", "final", "cache")}
    monkeypatch.setenv("VIGIL_ONE_EVIDENCE_ROOT", str(paths["one"]))
    monkeypatch.setenv("VIGIL_DEMO_RUNS_ROOT", str(paths["runs"]))
    monkeypatch.setenv("VIGIL_PROCTOR_EVIDENCE_ROOT", str(paths["bookmarks"]))
    monkeypatch.setenv("VIGIL_DEMO_FINAL_ROOT", str(paths["final"]))
    monkeypatch.setenv("VIGIL_PLAYBACK_CACHE_ROOT", str(paths["cache"]))
    monkeypatch.delenv("VIGIL_RETENTION_DAYS", raising=False)
    return paths


def _session(db, roots, *, ended_days_ago: int, product="ONE", review="PENDING", outside=None):  # noqa: F811
    room = db.query(ExamRoom).first() or ExamRoom(room_code="R-RET", name="Phòng lưu trữ")
    db.add(room)
    db.flush()
    session = ExamSession(room_id=room.id, exam_name="Thi lưu trữ", status="COMPLETED", product=product,
                          ended_at=NOW - datetime.timedelta(days=ended_days_ago))
    db.add(session)
    db.flush()
    folder = roots["one"] / session.id
    (folder / "video").mkdir(parents=True)
    (folder / "video" / "session.mp4").write_bytes(b"\0" * 2000)
    clip = folder / "clip.mp4"
    clip.write_bytes(b"\0" * 500)
    session.video_path, session.video_status = str(folder / "video" / "session.mp4"), "READY"
    event = DetectionEvent(session_id=session.id, room_id=room.id, event_id=f"e-{session.id[:8]}",
                           behavior="LOOK_AWAY", primary_signal="LOOK_AWAY", review_status=review)
    db.add(event)
    db.flush()
    db.add(EvidenceFile(event_id=event.id, file_path=str(outside or clip), video_path=str(outside or clip)))
    db.commit()
    return session, folder


def test_old_media_is_deleted_and_records_stay(db, roots):
    old, folder = _session(db, roots, ended_days_ago=100)
    recent, recent_folder = _session(db, roots, ended_days_ago=10)
    plan = retention.plan_retention(db, now=NOW)
    assert plan.days == 90 and [d.session_id for d in plan.due] == [old.id] and plan.bytes == 2500
    assert retention.apply_retention(db, plan) == {"sessions": 1, "files": 1, "bytes": 2500}
    db.expire_all()
    assert not folder.exists() and recent_folder.exists()
    old = db.get(ExamSession, old.id)
    assert old.media_purged_at is not None and old.video_status == "PURGED" and old.video_path is None
    evidence = db.query(EvidenceFile).join(DetectionEvent).filter(DetectionEvent.session_id == old.id).one()
    assert evidence.status == "PURGED" and "90 ngày" in evidence.error_message
    assert db.query(DetectionEvent).filter(DetectionEvent.session_id == old.id).count() == 1  # the record stays
    assert db.query(AuditLog).filter(AuditLog.action == "RETENTION_PURGE").count() == 1
    assert retention.plan_retention(db, now=NOW).due == []  # purged once


def test_a_confirmed_incident_holds_the_session(db, roots):
    held, folder = _session(db, roots, ended_days_ago=200, review="CONFIRMED")
    plan = retention.plan_retention(db, now=NOW)
    assert plan.due == [] and plan.held == [held.id]
    retention.apply_retention(db, plan)
    assert folder.exists()


def test_files_outside_the_data_roots_are_never_deleted(db, roots, tmp_path):
    outside = tmp_path / "final" / "classroom-01" / "evidence.mp4"  # a replay package
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"\0" * 10)
    _session(db, roots, ended_days_ago=100, outside=outside)
    retention.apply_retention(db, retention.plan_retention(db, now=NOW))
    assert outside.exists()


@pytest.mark.parametrize("value", ["0", "off"])
def test_retention_can_be_switched_off(db, roots, monkeypatch, value):
    _, folder = _session(db, roots, ended_days_ago=1000)
    monkeypatch.setenv("VIGIL_RETENTION_DAYS", value)
    plan = retention.plan_retention(db, now=NOW)
    assert plan.days is None and plan.due == []
    assert retention.apply_retention(db, plan)["sessions"] == 0 and folder.exists()


def test_custom_days(db, roots, monkeypatch):
    session, _ = _session(db, roots, ended_days_ago=40)
    monkeypatch.setenv("VIGIL_RETENTION_DAYS", "30")
    assert [d.session_id for d in retention.plan_retention(db, now=NOW).due] == [session.id]
