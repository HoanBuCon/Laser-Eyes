"""Phase 1 of docs/THIET_KE_UX_NGHIEP_VU_VIGIL.md: session lifecycle, test-data
cleanup, correct dashboard numbers and the cross-session review queue."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.main import app
from storage.database import Base, get_db
from storage.db_models import (
    AuditLog,
    Camera,
    DetectionEvent,
    EventReview,
    EvidenceFile,
    ExamRoom,
    ExamSession,
    SeatROI,
)
from storage.maintenance import apply_cleanup, plan_test_room_cleanup
from storage.session_lifecycle import close_interrupted_sessions, finish_session

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


@pytest.fixture
def client(db):
    def override():
        s = TestingSession()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _room(db, code: str, with_seat: bool) -> ExamRoom:
    room = ExamRoom(room_code=code, name=f"Room {code}")
    db.add(room)
    db.flush()
    if with_seat:
        db.add(SeatROI(room_id=room.id, seat_code=f"SEAT-{code}-01", polygon_json="[[0,0],[10,0],[10,10]]"))
    return room


def _event(db, session, room, n: int, *, pattern: str, status: str = "PENDING", risk: int = 80, meta=None) -> DetectionEvent:
    event = DetectionEvent(
        session_id=session.id,
        room_id=room.id,
        event_id=f"evt-{room.room_code}-{n}",
        behavior=pattern,
        primary_signal=pattern,
        primary_pattern=pattern,
        severity="HIGH" if risk >= 85 else "MEDIUM",
        risk_score=risk,
        review_status=status,
        incident_metadata_json=json.dumps(meta) if meta else None,
    )
    db.add(event)
    db.flush()
    return event


# --- Session lifecycle --------------------------------------------------------

def test_finished_analysis_closes_its_session_with_a_summary(db):
    room = _room(db, "R1", with_seat=True)
    session = ExamSession(room_id=room.id, exam_name="demo", status="RUNNING")
    db.add(session)
    db.flush()
    _event(db, session, room, 1, pattern="HEADS_TOGETHER", risk=82)
    _event(db, session, room, 2, pattern="HEADS_TOGETHER", risk=91)
    db.commit()

    assert finish_session(db, session.id, "COMPLETED") is True
    db.refresh(session)
    assert session.status == "COMPLETED"
    assert session.ended_at is not None
    assert session.total_events == 2
    assert session.risk_score == 91
    # A closed session is not closed again
    assert finish_session(db, session.id, "FAILED") is False


def test_sessions_left_running_by_a_previous_server_are_interrupted(db):
    room = _room(db, "R1", with_seat=True)
    running = ExamSession(room_id=room.id, exam_name="old", status="RUNNING")
    done = ExamSession(room_id=room.id, exam_name="done", status="COMPLETED")
    db.add_all([running, done])
    db.commit()

    assert close_interrupted_sessions(db) == 1
    db.refresh(running)
    db.refresh(done)
    assert running.status == "INTERRUPTED"
    assert done.status == "COMPLETED"


# --- Test-data cleanup --------------------------------------------------------

def test_cleanup_removes_rooms_without_seats_and_keeps_audit_and_demo_rooms(db):
    real = _room(db, "ROOM-CHINA-01", with_seat=True)
    junk = _room(db, "ROOM-ab12cd34", with_seat=False)
    demo_without_seats = _room(db, "ROOM-DEMO", with_seat=False)
    db.add(Camera(room_id=junk.id, name="cam"))
    junk_session = ExamSession(room_id=junk.id, exam_name="test", status="RUNNING")
    real_session = ExamSession(room_id=real.id, exam_name="real", status="RUNNING")
    db.add_all([junk_session, real_session])
    db.flush()
    junk_event = _event(db, junk_session, junk, 1, pattern="X")
    db.add(EvidenceFile(event_id=junk_event.id, file_path="x.mp4"))
    db.add(EventReview(event_id=junk_event.id, reviewer_id="t", decision="REJECTED"))
    _event(db, real_session, real, 1, pattern="X")
    db.add(AuditLog(actor_id="tester", action="REVIEW_EVENT", resource_type="EVENT", resource_id=junk_event.id))
    db.commit()

    plan = plan_test_room_cleanup(db, keep_room_codes=["ROOM-DEMO"])
    assert [room.room_code for room in plan.rooms] == ["ROOM-ab12cd34"]
    apply_cleanup(db, plan)

    codes = {code for (code,) in db.query(ExamRoom.room_code)}
    assert codes == {"ROOM-CHINA-01", "ROOM-DEMO"}
    assert db.query(DetectionEvent).count() == 1
    assert db.query(EvidenceFile).count() == 0
    assert db.query(EventReview).count() == 0
    assert db.query(AuditLog).count() == 1  # the audit trail is kept


# --- Dashboard numbers ----------------------------------------------------------

def test_summary_reports_review_progress_and_current_pattern_names(client, db):
    room = _room(db, "R1", with_seat=True)
    _room(db, "R2", with_seat=False)
    session = ExamSession(room_id=room.id, exam_name="s", status="RUNNING")
    db.add(session)
    db.flush()
    _event(db, session, room, 1, pattern="HEADS_TOGETHER")
    _event(db, session, room, 2, pattern="HEADS_TOGETHER", status="CONFIRMED")
    _event(db, session, room, 3, pattern="REPEATED_NEIGHBOR_GLANCE", status="REJECTED")
    db.commit()

    stats = client.get("/api/v1/statistics/summary").json()
    assert stats["events_by_review_status"] == {"PENDING": 1, "CONFIRMED": 1, "REJECTED": 1}
    assert stats["unreviewed_events_count"] == 1
    assert stats["events_by_pattern"] == {"HEADS_TOGETHER": 2, "REPEATED_NEIGHBOR_GLANCE": 1}
    assert stats["monitored_rooms"] == 1
    assert stats["active_rooms"] == 2


def test_operations_page_reads_review_progress_and_patterns_from_the_api():
    js = Path("dashboard/js/app.js").read_text(encoding="utf-8")
    html = Path("dashboard/index.html").read_text(encoding="utf-8")
    assert "PROLONGED_HEAD_TURN" not in js and "PROLONGED_HEAD_TURN" not in html
    assert "events_by_pattern" in js
    assert "unreviewed_events_count" in js


# --- Review queue ----------------------------------------------------------------

def test_review_queue_covers_every_session_with_filters_and_counts(client, db):
    china = _room(db, "ROOM-CHINA-03", with_seat=True)
    other = _room(db, "ROOM-OTHER", with_seat=True)
    old = ExamSession(room_id=china.id, exam_name="yesterday", status="COMPLETED")
    now = ExamSession(room_id=other.id, exam_name="today", status="RUNNING")
    db.add_all([old, now])
    db.flush()
    _event(db, old, china, 1, pattern="HEADS_TOGETHER", risk=90,
           meta={"behavior_start_ms": 14200.0, "first_seen_ms": 21400.0, "last_seen_ms": 21400.0,
                 "occurrence_count": 2, "focus": {"frame_size": [1920, 1080], "person_bbox": [1, 2, 3, 4]}})
    _event(db, old, china, 2, pattern="REPEATED_NEIGHBOR_GLANCE", status="CONFIRMED", risk=70)
    _event(db, now, other, 1, pattern="REPEATED_NEIGHBOR_GLANCE", risk=60)
    db.commit()

    queue = client.get("/api/v1/proctor/review-queue").json()
    # Not tied to the running analysis: the finished session's incident is there
    assert queue["counts"] == {"PENDING": 2, "CONFIRMED": 1, "REJECTED": 0, "INCONCLUSIVE": 0}
    assert queue["total"] == 2
    assert {item["room_code"] for item in queue["items"]} == {"ROOM-CHINA-03", "ROOM-OTHER"}

    china_only = client.get("/api/v1/proctor/review-queue", params={"room": "ROOM-CHINA-03", "status": "ALL", "sort": "priority"}).json()
    assert [item["review_priority_score"] for item in china_only["items"]] == [90, 70]
    first = china_only["items"][0]
    assert first["behavior_start_ms"] == 14200.0
    assert first["source_timestamp_ms"] == 21400.0   # no longer 0 in exports
    assert first["occurrence_count"] == 2
    assert first["focus"]["person_bbox"] == [1, 2, 3, 4]
    assert first["session_name"] == "yesterday"

    glances = client.get("/api/v1/proctor/review-queue", params={"pattern": "REPEATED_NEIGHBOR_GLANCE"}).json()
    assert [item["room_code"] for item in glances["items"]] == ["ROOM-OTHER"]
    facets = queue["facets"]
    assert {r["code"] for r in facets["rooms"]} == {"ROOM-CHINA-03", "ROOM-OTHER"}
    assert facets["patterns"][0]["name"] in ("HEADS_TOGETHER", "REPEATED_NEIGHBOR_GLANCE")


def test_review_page_is_served_and_every_sidebar_links_to_it(client):
    assert client.get("/review").status_code == 200
    for page in ("index.html", "demo.html", "calibration.html", "data_workbench.html", "review.html"):
        html = Path("dashboard", page).read_text(encoding="utf-8")
        assert 'href="/review"' in html, page
        assert "#reviewQueue" not in html.replace('id="reviewQueue"', ""), page


def test_decisions_are_recorded_under_the_reviewers_name_not_a_placeholder():
    for script in ("dashboard/js/demo.js", "dashboard/js/review.js"):
        source = Path(script).read_text(encoding="utf-8")
        assert "'Lead_Proctor'" not in source, script
        assert "vigil.reviewer" in source, script
