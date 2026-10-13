"""System page maintenance actions, the video wall endpoints and the shared
VIGIL Local look (flat surfaces, full width, no Seat ROIs outside debug)."""

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
from api.routes import admin as admin_routes
from storage.database import Base, get_db
from storage.db_models import AuditLog, DetectionEvent, EvidenceFile, ExamRoom, ExamSession, SeatROI

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


def _seed(db, run_dir: str):
    room = ExamRoom(room_code="CLASSROOM-01", name="Classroom 01")
    db.add(room)
    db.flush()
    db.add(SeatROI(room_id=room.id, seat_code="SEAT-1", polygon_json="[[0,0],[1,0],[1,1]]"))
    session = ExamSession(room_id=room.id, exam_name="run", status="COMPLETED")
    db.add(session)
    db.flush()
    event = DetectionEvent(session_id=session.id, room_id=room.id, event_id="e1", behavior="X",
                           primary_signal="X", severity="HIGH", risk_score=90)
    db.add(event)
    db.flush()
    db.add(EvidenceFile(event_id=event.id, file_path=f"data\\demo_runs\\{run_dir}\\evidence\\clip.mp4"))
    db.commit()
    return room


# --- System maintenance ----------------------------------------------------------

def test_destructive_actions_need_the_typed_phrase(client, db):
    _seed(db, "DEMO-1-classroom-01")
    for url in ("/api/v1/admin/reset/incidents", "/api/v1/admin/cleanup/test-rooms",
                "/api/v1/admin/cleanup/orphan-runs", "/api/v1/admin/cleanup/playback-cache"):
        assert client.post(url, json={"confirm": "yes"}).status_code == 400, url
    assert db.query(DetectionEvent).count() == 1


def test_maintenance_is_refused_while_an_analysis_runs(client, db, monkeypatch):
    _seed(db, "DEMO-1-classroom-01")
    monkeypatch.setattr(admin_routes, "_active_run", lambda: {"busy": True, "run_id": "DEMO-9", "state": "RUNNING"})
    res = client.post("/api/v1/admin/reset/incidents", json={"confirm": "RESET ALL"})
    assert res.status_code == 409
    assert db.query(DetectionEvent).count() == 1


def test_reset_keeps_rooms_seats_and_audit_and_records_who_did_it(client, db):
    _seed(db, "DEMO-1-classroom-01")
    res = client.post("/api/v1/admin/reset/incidents", json={"confirm": "reset all", "actor": "Demo Operator"})
    assert res.status_code == 200, res.text
    assert res.json()["incidents"] == 1
    db.expire_all()
    assert db.query(DetectionEvent).count() == 0
    assert db.query(EvidenceFile).count() == 0
    assert db.query(ExamSession).count() == 0
    assert db.query(ExamRoom).count() == 1
    assert db.query(SeatROI).count() == 1
    audit = db.query(AuditLog).one()
    assert (audit.actor_id, audit.action) == ("Demo Operator", "ADMIN_RESET_INCIDENTS")


def test_orphan_run_purge_keeps_runs_with_incidents_or_replay_clips(client, db):
    runs = Path(os.environ["VIGIL_DEMO_RUNS_ROOT"])
    final = Path(os.environ["VIGIL_DEMO_FINAL_ROOT"])
    for name in ("DEMO-1-classroom-01", "DEMO-2-classroom-03", "DEMO-3-old"):
        (runs / name / "evidence").mkdir(parents=True)
        (runs / name / "evidence" / "clip.mp4").write_bytes(b"x" * 10)
    (final / "classroom-03").mkdir(parents=True)
    (final / "classroom-03" / "events.json").write_text(
        json.dumps([{"evidence_clip_path": "data\\demo_runs\\DEMO-2-classroom-03\\evidence\\clip.mp4"}]), encoding="utf-8")
    _seed(db, "DEMO-1-classroom-01")

    overview = client.get("/api/v1/admin/overview").json()
    assert overview["cleanup"]["orphan_runs"]["examples"] == ["DEMO-3-old"]
    res = client.post("/api/v1/admin/cleanup/orphan-runs", json={"confirm": "DELETE"})
    assert res.json()["runs"] == 1
    assert sorted(p.name for p in runs.iterdir()) == ["DEMO-1-classroom-01", "DEMO-2-classroom-03"]


# --- Video wall --------------------------------------------------------------------

def test_wall_incidents_carry_the_student_box_and_timing(client):
    final = Path(os.environ["VIGIL_DEMO_FINAL_ROOT"]) / "classroom-03"
    final.mkdir(parents=True)
    (final / "events.json").write_text(json.dumps([
        {"event_id": "a", "seat_code": "SEAT-CLASSROOM-03-15", "title": "HEADS_TOGETHER", "severity": "HIGH",
         "peak_risk_score": 88, "behavior_start_ms": 1800.0, "first_seen_ms": 5000.0, "last_seen_ms": 9000.0,
         "metadata": {"primary_pattern": "HEADS_TOGETHER",
                      "focus": {"frame_size": [1920, 1080], "person_bbox": [808, 189, 918, 304]}}},
        {"event_id": "b", "metadata": {"focus": {"frame_size": [1920, 1080],
                                                  "seat_polygon": [[10, 20], [50, 20], [50, 80], [10, 80]]}},
         "timestamp_ms": 7000.0},
        {"event_id": "c", "metadata": {}},
    ]), encoding="utf-8")
    data = client.get("/api/v1/demo/presets/classroom-03/incidents").json()
    assert data["available"] is True and data["frame_size"] == [1920, 1080]
    first, second = data["incidents"]
    assert first["bbox"] == [808, 189, 918, 304]
    assert (first["behavior_start_ms"], first["flagged_ms"], first["last_seen_ms"]) == (1800.0, 5000.0, 9000.0)
    assert first["pattern"] == "HEADS_TOGETHER"
    assert second["bbox"] == [10, 20, 50, 80]  # seat bounds stand in when no person was recorded
    assert client.get("/api/v1/demo/presets/nope/incidents").status_code == 404
    assert client.get("/api/v1/demo/presets/nope/source").status_code == 404


def test_wall_and_system_pages_are_served_and_linked_from_every_sidebar(client):
    assert client.get("/wall").status_code == 200
    assert client.get("/admin").status_code == 200
    for page in ("index.html", "demo.html", "calibration.html", "data_workbench.html",
                 "review.html", "wall.html", "admin.html"):
        html = Path("dashboard", page).read_text(encoding="utf-8")
        assert 'href="/wall"' in html and 'href="/admin"' in html, page


# --- Shared look ---------------------------------------------------------------------

def test_global_tokens_are_flat_and_full_width():
    tokens = Path("dashboard/css/vigil-tokens.css").read_text(encoding="utf-8")
    for name in ("radius-xs", "radius-sm", "radius-md", "radius-lg"):
        assert f"--vigil-{name}: 0;" in tokens
    for name in ("shadow-sm", "shadow-md", "shadow-lg"):
        assert tokens.count(f"--vigil-{name}: none;") == 2  # dark and light themes
    assert "--vigil-content-max: none;" in tokens


def test_review_highlight_does_not_draw_the_seat_roi():
    overlay = Path("dashboard/js/focus-overlay.js").read_text(encoding="utf-8")
    assert "'polygon'" not in overlay and "polyline" not in overlay
