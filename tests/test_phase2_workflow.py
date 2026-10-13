"""Phase 2 of docs/THIET_KE_UX_NGHIEP_VU_VIGIL.md: accounts and roles, exams and
sessions, playback timeline, session reports and the calibration room check."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.main import app
from classroom_monitor.demo.runtime import DemoRuntime
from classroom_monitor.demo.seat_timeline import SeatTimelineRecorder, load_timeline
from storage.auth_service import create_user, hash_password, verify_password
from storage.database import Base, get_db
from storage.db_models import (
    AuditLog,
    DetectionEvent,
    EventReview,
    EvidenceFile,
    ExamRoom,
    ExamSession,
    SeatROI,
    User,
)

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
PASSWORD = "correct-horse-1"


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


def _sign_in(client, db, username="chief", role="CHIEF"):
    if db.query(User).filter(User.username == username).first() is None:
        create_user(db, username, username.title(), role, PASSWORD)
    res = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert res.status_code == 200, res.text
    return res.json()["user"]


def _room(db, code="CLASSROOM-01", seats=3, capacity=30):
    room = ExamRoom(room_code=code, name=f"Room {code}", capacity=capacity)
    db.add(room)
    db.flush()
    for i in range(seats):
        x = 100 + i * 110  # 10 px gap: desks side by side
        db.add(SeatROI(room_id=room.id, seat_code=f"SEAT-{code}-{i + 1:02d}",
                       polygon_json=json.dumps([[x, 300], [x + 100, 300], [x + 100, 400], [x, 400]])))
    db.commit()
    return room


def _incident(db, session, n, status="PENDING", pattern="HEADS_TOGETHER", start=1800.0, flagged=5000.0, last=9000.0):
    seat = db.query(SeatROI).filter(SeatROI.room_id == session.room_id).first()
    event = DetectionEvent(
        session_id=session.id, room_id=session.room_id, seat_id=seat.id if seat else None, event_id=f"ev-{session.id[:6]}-{n}",
        behavior=pattern, primary_signal=pattern, primary_pattern=pattern, severity="HIGH", risk_score=80 + n,
        review_status=status,
        incident_metadata_json=json.dumps({"behavior_start_ms": start, "first_seen_ms": flagged, "last_seen_ms": last,
                                           "focus": {"frame_size": [1920, 1080], "person_bbox": [10, 20, 110, 220]}}),
    )
    db.add(event)
    db.flush()
    if status != "PENDING":
        db.add(EventReview(event_id=event.id, reviewer_id="Ms Lan", decision=status, reason_code="CLEAR_CHEATING"))
    db.commit()
    return event


# --- Accounts -------------------------------------------------------------------------

def test_passwords_are_salted_hashes():
    stored = hash_password("s3cret-pass", iterations=1000)
    assert "s3cret-pass" not in stored
    assert verify_password("s3cret-pass", stored) and not verify_password("wrong", stored)
    assert hash_password("same-pass1", iterations=1000) != hash_password("same-pass1", iterations=1000)


def test_first_admin_setup_only_while_no_account_exists(client, db):
    assert client.get("/api/v1/auth/me").json()["needs_setup"] is True
    res = client.post("/api/v1/auth/setup", json={"username": "Admin", "display_name": "Head Admin", "password": PASSWORD})
    assert res.status_code == 200
    me = client.get("/api/v1/auth/me").json()
    assert me["user"]["username"] == "admin" and me["user"]["role"] == "ADMIN"
    assert client.post("/api/v1/auth/setup", json={"username": "x", "password": PASSWORD}).status_code == 409


def test_sign_in_and_account_management_need_an_administrator(client, db):
    create_user(db, "boss", "Boss", "ADMIN", PASSWORD)
    assert client.post("/api/v1/auth/login", json={"username": "boss", "password": "nope-nope"}).status_code == 401
    assert client.get("/api/v1/users").status_code == 401  # anonymous, even in open mode
    _sign_in(client, db, "proctor", "PROCTOR")
    assert client.get("/api/v1/users").status_code == 403
    _sign_in(client, db, "boss", "ADMIN")
    created = client.post("/api/v1/users", json={"username": "lan", "display_name": "Ms Lan", "role": "PROCTOR", "password": PASSWORD})
    assert created.status_code == 201
    boss = db.query(User).filter(User.username == "boss").one()
    # The last active administrator cannot be demoted or disabled
    assert client.patch(f"/api/v1/users/{boss.id}", json={"role": "PROCTOR"}).status_code == 409
    client.post("/api/v1/auth/logout")
    assert client.get("/api/v1/auth/me").json()["user"] is None


def test_a_signed_in_reviewer_is_recorded_by_name(client, db):
    room = _room(db)
    session = ExamSession(room_id=room.id, exam_name="s", status="COMPLETED")
    db.add(session)
    db.commit()
    event = _incident(db, session, 1)
    _sign_in(client, db, "lan", "PROCTOR")
    res = client.post(f"/api/v1/events/{event.id}/review", json={"decision": "REJECTED", "reviewer_id": "Someone Else", "reason_code": "OTHER"})
    assert res.status_code == 200, res.text
    db.expire_all()
    assert db.query(EventReview).filter(EventReview.event_id == event.id).one().reviewer_id == "Lan"


def test_required_mode_sends_visitors_to_sign_in(client, db, monkeypatch):
    monkeypatch.setenv("VIGIL_AUTH_MODE", "required")
    page = client.get("/review", follow_redirects=False)
    assert page.status_code == 303 and page.headers["location"].startswith("/login")
    assert client.get("/api/v1/exams").status_code == 401
    assert client.get("/api/v1/auth/me").status_code == 200
    assert client.get("/login").status_code == 200
    _sign_in(client, db, "lan", "PROCTOR")
    assert client.get("/api/v1/exams").status_code == 200
    # Proctors cannot manage exams when sign-in is required
    assert client.post("/api/v1/exams", json={"name": "Maths"}).status_code == 403


# --- Exams and sessions -----------------------------------------------------------------

def test_scheduled_sessions_follow_the_readiness_checklist(client, db):
    ready_room = _room(db, "CLASSROOM-01")
    empty_room = _room(db, "ROOM-EMPTY", seats=0)
    exam = client.post("/api/v1/exams", json={"name": "Term 1 – Maths", "exam_date": "2026-10-20"}).json()
    created = client.post(f"/api/v1/exams/{exam['id']}/sessions", json={
        "room_ids": [ready_room.id, empty_room.id],
        "scheduled_start": "2026-10-20T07:30:00", "scheduled_end": "2026-10-20T09:00:00",
    })
    assert created.status_code == 201, created.text
    by_room = {s["room_code"]: s for s in created.json()}
    assert by_room["ROOM-EMPTY"]["status"] == "DRAFT"
    if Path("demo_video/classroom-01.mp4").is_file():
        assert by_room["CLASSROOM-01"]["status"] == "READY"
    checklist = client.get(f"/api/v1/exam-sessions/{by_room['ROOM-EMPTY']['id']}/checklist").json()
    assert checklist["ready"] is False
    assert {i["key"]: i["ok"] for i in checklist["items"]}["seat_rois"] is False
    refused = client.post(f"/api/v1/exam-sessions/{by_room['ROOM-EMPTY']['id']}/start", json={"mode": "LIVE"})
    assert refused.status_code == 409 and "Seat ROIs" in refused.json()["detail"]
    listing = client.get("/api/v1/exam-sessions", params={"exam_id": exam["id"]}).json()
    assert listing["total"] == 2
    exams = client.get("/api/v1/exams").json()
    assert exams[0]["session_count"] == 2
    assert client.post(f"/api/v1/exams/{exam['id']}/sessions", json={
        "room_ids": [ready_room.id], "scheduled_start": "2026-10-20T09:00:00", "scheduled_end": "2026-10-20T08:00:00",
    }).status_code == 400


def test_starting_a_session_runs_the_analysis_under_that_session(client, db, monkeypatch):
    room = _room(db)
    session = ExamSession(room_id=room.id, exam_name="P101 morning", status="READY", started_at=None)
    db.add(session)
    db.commit()
    calls = {}
    runtime = DemoRuntime.get_instance()
    monkeypatch.setattr(runtime, "start", lambda **kw: calls.update(kw) or {"state": "RUNNING", "session_id": kw["session_id"]})
    monkeypatch.setattr("api.routes.exams.readiness", lambda db_, s: {"ready": True, "items": [], "preset": "classroom-01"})
    assert client.post(f"/api/v1/exam-sessions/{session.id}/start", json={"mode": "REPLAY"}).status_code == 400
    res = client.post(f"/api/v1/exam-sessions/{session.id}/start", json={"mode": "LIVE"})
    assert res.status_code == 200, res.text
    assert calls == {"preset": "classroom-01", "mode": "LIVE", "session_id": session.id}
    assert db.query(AuditLog).filter(AuditLog.action == "START_SESSION").count() == 1


def test_runtime_files_its_run_under_a_scheduled_session():
    from storage.database import SessionLocal, init_db

    init_db()
    s = SessionLocal()
    try:
        room = ExamRoom(room_code="CLASSROOM-01", name="Classroom 01")
        s.add(room)
        s.flush()
        scheduled = ExamSession(room_id=room.id, exam_name="Scheduled", status="READY", started_at=None)
        s.add(scheduled)
        s.commit()
        sid = scheduled.id
    finally:
        s.close()
    runtime = DemoRuntime.get_instance()
    assert runtime._init_db_session("classroom-01", "DEMO-1-classroom-01", "LIVE", session_id=sid) == sid
    s = SessionLocal()
    try:
        row = s.get(ExamSession, sid)
        assert (row.status, row.run_id, row.source_preset, row.exam_name) == ("RUNNING", "DEMO-1-classroom-01", "classroom-01", "Scheduled")
        adhoc_id = runtime._init_db_session("classroom-01", "DEMO-2-classroom-01", "LIVE")
        assert s.get(ExamSession, adhoc_id).run_id == "DEMO-2-classroom-01"
    finally:
        s.close()


# --- Playback ------------------------------------------------------------------------------

def test_seat_timeline_samples_once_per_second_and_keeps_active_seats():
    rec = SeatTimelineRecorder(interval_ms=1000)
    assert rec.observe(0, {"S1": 0, "S2": 10}) is True
    assert rec.observe(400, {"S1": 50}) is False
    rec.observe(1000, {"S1": 40.4, "S2": 12, "S3": 0})
    rec.observe(2100, {"S1": 0, "S2": 0, "S3": 0, "S4": 70})
    data = rec.to_dict()
    assert data["samples"] == 3
    assert data["seats"] == {"S1": [0, 40, 0], "S2": [10, 12, 0], "S4": [0, 0, 70]}


def test_playback_returns_incident_timing_and_the_runs_timeline(client, db):
    room = _room(db)
    run_dir = Path(os.environ["VIGIL_DEMO_RUNS_ROOT"]) / "DEMO-7-classroom-01"
    run_dir.mkdir(parents=True)
    rec = SeatTimelineRecorder()
    rec.observe(0, {"SEAT-CLASSROOM-01-01": 30})
    rec.write(run_dir)
    assert load_timeline(run_dir)["seats"]["SEAT-CLASSROOM-01-01"] == [30]
    session = ExamSession(room_id=room.id, exam_name="run", status="COMPLETED", run_id="DEMO-7-classroom-01")
    db.add(session)
    db.commit()
    _incident(db, session, 1)
    data = client.get(f"/api/v1/exam-sessions/{session.id}/playback").json()
    inc = data["incidents"][0]
    assert (inc["behavior_start_ms"], inc["flagged_ms"], inc["last_seen_ms"]) == (1800.0, 5000.0, 9000.0)
    assert inc["seat_code"] == "SEAT-CLASSROOM-01-01"
    assert data["frame_size"] == [1920, 1080]
    assert data["timeline"]["seats"] == {"SEAT-CLASSROOM-01-01": [30]}
    assert data["video"]["source_url"] in (None, "/api/v1/demo/presets/classroom-01/source")


# --- Reports ---------------------------------------------------------------------------------

def test_session_report_lists_only_confirmed_violations_and_freezes_on_approval(client, db):
    room = _room(db)
    session = ExamSession(room_id=room.id, exam_name="P101", status="COMPLETED")
    db.add(session)
    db.commit()
    confirmed = _incident(db, session, 1, status="CONFIRMED")
    _incident(db, session, 2, status="REJECTED", pattern="REPEATED_NEIGHBOR_GLANCE")
    _incident(db, session, 3, status="PENDING")

    report = client.get(f"/api/v1/reports/sessions/{session.id}").json()["report"]
    assert report["incidents"]["by_review_status"] == {"PENDING": 1, "CONFIRMED": 1, "REJECTED": 1, "INCONCLUSIVE": 0}
    assert [c["id"] for c in report["confirmed"]] == [confirmed.id]
    assert report["coverage"] == {"seats_with_roi": 3, "capacity": 30, "ratio": 0.1}

    # Approving needs a signed-in chief proctor, and a reason while incidents are pending
    assert client.post(f"/api/v1/reports/sessions/{session.id}/approve", json={}).status_code == 401
    _sign_in(client, db)
    assert client.post(f"/api/v1/reports/sessions/{session.id}/approve", json={}).status_code == 409
    first = client.post(f"/api/v1/reports/sessions/{session.id}/approve", json={"note": "Pending one is a duplicate"})
    assert first.status_code == 200, first.text
    assert first.json()["version"] == 1 and first.json()["status"] == "CLOSED"
    frozen = client.get(f"/api/v1/reports/sessions/{session.id}", params={"version": 1}).json()
    assert frozen["frozen"]["sha256"] == first.json()["sha256"]
    assert frozen["report"]["approval"]["approved_by"] == "Chief"
    # A closed session must be reopened (with a reason) before a new version
    assert client.post(f"/api/v1/reports/sessions/{session.id}/approve", json={"note": "again"}).status_code == 409
    assert client.post(f"/api/v1/reports/sessions/{session.id}/reopen", json={"reason": "Appeal re seat 1"}).json()["status"] == "COMPLETED"
    second = client.post(f"/api/v1/reports/sessions/{session.id}/approve", json={"note": "re-issued"})
    assert second.json()["version"] == 2

    record = client.get(f"/api/v1/reports/incidents/{confirmed.id}/record")
    assert record.status_code == 200 and record.json()["incident"]["reviewer"] == "Ms Lan"
    pending = db.query(DetectionEvent).filter(DetectionEvent.review_status == "PENDING").one()
    assert client.get(f"/api/v1/reports/incidents/{pending.id}/record").status_code == 409
    csv_text = client.get(f"/api/v1/reports/sessions/{session.id}/incidents.csv").text
    assert csv_text.count("\n") == 4 and "behavior_start_s" in csv_text and "1.8" in csv_text


def test_unfinished_sessions_cannot_be_approved(client, db):
    room = _room(db)
    session = ExamSession(room_id=room.id, exam_name="later", status="READY")
    db.add(session)
    db.commit()
    _sign_in(client, db)
    assert client.post(f"/api/v1/reports/sessions/{session.id}/approve", json={"note": "x"}).status_code == 409


def test_ai_quality_reports_precision_per_pattern(client, db):
    room = _room(db)
    session = ExamSession(room_id=room.id, exam_name="s", status="COMPLETED")
    db.add(session)
    db.commit()
    for n, status in enumerate(["CONFIRMED", "CONFIRMED", "CONFIRMED", "REJECTED", "PENDING"]):
        _incident(db, session, n, status=status)
    _incident(db, session, 9, status="PENDING", pattern="MULTI_PERSON_DWELL_NEAR_SEAT")
    data = client.get("/api/v1/reports/ai-quality").json()
    rows = {r["pattern"]: r for r in data["patterns"]}
    assert rows["HEADS_TOGETHER"]["precision"] == 0.75
    assert rows["MULTI_PERSON_DWELL_NEAR_SEAT"]["precision"] is None
    assert len(data["recent_rejected"]) == 1


# --- Calibration room check -------------------------------------------------------------------------

def test_seat_layout_reports_coverage_neighbours_and_overlaps(client, db):
    room = _room(db, "ROOM-X", seats=3, capacity=4)
    db.add(SeatROI(room_id=room.id, seat_code="SEAT-ROOM-X-09",
                   polygon_json=json.dumps([[110, 310], [190, 310], [190, 390], [110, 390]])))  # inside seat 01
    db.commit()
    layout = client.get(f"/api/v1/rooms/{room.id}/seat-layout").json()
    assert layout["seat_count"] == 4 and layout["coverage"] == 1.0
    assert any(o["seats"] == ["SEAT-ROOM-X-01", "SEAT-ROOM-X-09"] for o in layout["overlaps"])
    side = {(e["a"], e["b"]) for e in layout["edges"] if e["kind"] == "side"}
    assert ("SEAT-ROOM-X-02", "SEAT-ROOM-X-03") in side
    assert layout["error"] is None


def test_new_pages_are_served_and_linked_from_every_sidebar(client):
    for route in ("/sessions", "/playback", "/reports", "/reports/print", "/login", "/users"):
        assert client.get(route).status_code == 200, route
    for page in ("index.html", "demo.html", "wall.html", "review.html", "playback.html", "reports.html",
                 "sessions.html", "calibration.html", "admin.html", "data_workbench.html", "users.html"):
        html = Path("dashboard", page).read_text(encoding="utf-8")
        for href in ('href="/sessions"', 'href="/playback"', 'href="/reports"'):
            assert href in html, (page, href)
        assert "/static/js/vigil-auth.js" in html, page
