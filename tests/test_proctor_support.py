"""Integration tests for AI-adjacent human proctor support features."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.main import app
from classroom_monitor.demo.runtime import DemoRuntime, DemoStatus
from exam_monitor.app import persist_local_bookmark
from exam_monitor.events import SessionStore
from exam_monitor.models import SessionInfo
from storage.database import Base, get_db
from storage.db_models import Camera, ExamRoom, ExamSession, ExamSite, ProctorBookmark, SeatROI


test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    Base.metadata.create_all(bind=test_engine)
    monkeypatch.setenv("VIGIL_PROCTOR_EVIDENCE_ROOT", str(tmp_path / "bookmarks"))
    db = TestingSessionLocal()
    site = ExamSite(name="Test site")
    db.add(site)
    db.flush()
    room = ExamRoom(site_id=site.id, name="Test room", room_code="TEST")
    db.add(room)
    db.flush()
    camera = Camera(room_id=room.id, name="Test camera")
    db.add(camera)
    db.flush()
    db.add(
        SeatROI(
            room_id=room.id,
            camera_id=camera.id,
            seat_code="S01",
            polygon_json="[[0.1,0.1],[0.6,0.1],[0.6,0.8],[0.1,0.8]]",
        )
    )
    session = ExamSession(room_id=room.id, camera_id=camera.id, exam_name="Test exam")
    db.add(session)
    db.commit()
    session_id = session.id
    db.close()

    def override_get_db():
        scoped = TestingSessionLocal()
        try:
            yield scoped
        finally:
            scoped.close()

    runtime = DemoRuntime.get_instance()
    runtime.stop()
    runtime.reset()
    with runtime._lock:
        runtime.status = DemoStatus(
            state="RUNNING",
            run_id="RUN-PROCTOR-1",
            session_id=session_id,
            frame_index=42,
            source_timestamp_ms=1400.0,
            inference_mode="REAL",
        )
        runtime.latest_raw_frame = np.full((48, 64, 3), 127, dtype=np.uint8)

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client, session_id
    app.dependency_overrides.clear()
    runtime.stop()
    runtime.reset()
    Base.metadata.drop_all(bind=test_engine)


def test_capture_bookmark_review_and_export_are_durable(client):
    http, session_id = client
    capture_res = http.post("/api/v1/proctor/captures")
    assert capture_res.status_code == 200
    capture = capture_res.json()
    assert capture["frame_id"] == 42
    assert capture["session_id"] == session_id
    snapshot = http.get(capture["snapshot_url"])
    assert snapshot.status_code == 200
    assert snapshot.headers["content-type"] == "image/jpeg"

    request = {
        "capture_id": capture["capture_id"],
        "request_id": "request-proctor-0001",
        "subject_ref": "S01",
        "note": "Please review this movement",
        "created_by": "judge",
    }
    saved = http.post("/api/v1/proctor/bookmarks", json=request)
    assert saved.status_code == 200, saved.text
    bookmark = saved.json()
    assert bookmark["created"] is True
    assert bookmark["source_type"] == "MANUAL_BOOKMARK"
    assert bookmark["evidence_status"] == "READY"
    assert bookmark["crop_url"]
    durable_image = http.get(bookmark["snapshot_url"])
    assert durable_image.status_code == 200
    assert hashlib.sha256(durable_image.content).hexdigest() == bookmark["sha256"]
    crop = http.get(bookmark["crop_url"])
    assert crop.status_code == 200
    assert 0 < len(crop.content) < len(durable_image.content)
    integrity = http.get(f"/api/v1/proctor/bookmarks/{bookmark['bookmark_id']}/integrity")
    assert integrity.status_code == 200
    assert integrity.json()["hash_status"] == "HASH_VERIFIED"

    DemoRuntime.get_instance().frame_capture_store.clear()
    duplicate = http.post("/api/v1/proctor/bookmarks", json=request)
    assert duplicate.status_code == 200
    assert duplicate.json()["bookmark_id"] == bookmark["bookmark_id"]
    assert duplicate.json()["created"] is False

    reviewed = http.post(
        f"/api/v1/proctor/bookmarks/{bookmark['bookmark_id']}/review",
        json={
            "reviewer_id": "judge",
            "decision": "INCONCLUSIVE",
            "reason_code": "NEEDS_CONTEXT",
            "note": "Keep for later comparison",
        },
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["review_decision"] == "INCONCLUSIVE"
    assert reviewed.json()["reviews"][0]["previous_decision"] == "PENDING"
    assert reviewed.json()["reviews"][0]["previous_note"] == "Please review this movement"

    queue = http.get(f"/api/v1/proctor/queue?session_id={session_id}").json()
    assert [item["queue_id"] for item in queue] == [f"MANUAL:{bookmark['bookmark_id']}"]
    exported = http.get(f"/api/v1/proctor/sessions/{session_id}/export?format=json")
    assert exported.status_code == 200
    assert exported.json()["schema_version"] == "vigil.proctor-support.v1"
    csv_export = http.get(f"/api/v1/proctor/sessions/{session_id}/export?format=csv")
    assert csv_export.status_code == 200
    assert bookmark["bookmark_id"] in csv_export.text


def test_capture_from_previous_run_fails_closed(client):
    http, _session_id = client
    capture = http.post("/api/v1/proctor/captures").json()
    runtime = DemoRuntime.get_instance()
    with runtime._lock:
        runtime.status.run_id = "RUN-PROCTOR-2"
    response = http.post(
        "/api/v1/proctor/bookmarks",
        json={"capture_id": capture["capture_id"], "request_id": "stale-request-0001"},
    )
    assert response.status_code == 409


def test_evidence_write_failure_is_persisted(client, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    http, _session_id = client
    blocked_root = tmp_path / "not-a-directory"
    blocked_root.write_text("blocked", encoding="utf-8")
    monkeypatch.setenv("VIGIL_PROCTOR_EVIDENCE_ROOT", str(blocked_root))
    capture = http.post("/api/v1/proctor/captures").json()
    saved = http.post(
        "/api/v1/proctor/bookmarks",
        json={"capture_id": capture["capture_id"], "request_id": "failed-write-0001"},
    )
    assert saved.status_code == 200
    assert saved.json()["evidence_status"] == "FAILED"
    db = TestingSessionLocal()
    try:
        stored = db.query(ProctorBookmark).filter_by(request_id="failed-write-0001").one()
        assert stored.error_message
    finally:
        db.close()


def test_classroom_dashboard_exposes_proctor_workflow(client):
    http, _session_id = client
    page = http.get("/demo")
    assert page.status_code == 200
    assert "btnMarkFrame" in page.text
    assert "captureModal" in page.text
    script = http.get("/static/js/demo.js")
    assert script.status_code == 200
    assert "PROCTOR_BOOKMARK" in script.text
    assert "/api/v1/proctor/bookmarks" in script.text


def test_local_bookmark_round_trip_and_legacy_compatibility(tmp_path: Path):
    store = SessionStore(tmp_path)
    frame = np.full((32, 40, 3), 80, dtype=np.uint8)
    bookmark = persist_local_bookmark(
        store.evidence_dir,
        session_id="LOCAL-001",
        frame_id=17,
        session_elapsed_ms=1250.0,
        frame=frame,
        note="Observed movement",
        source_kind="SIMULATION",
        source_ref="built-in-demo",
        simulation=True,
    )
    assert bookmark.evidence_status == "READY"
    assert bookmark.snapshot_path and Path(bookmark.snapshot_path).is_file()
    session = SessionInfo(
        session_id="LOCAL-001",
        candidate_id="SV01",
        exam_name="Demo",
        started_at="2026-09-29T10:00:00",
        bookmarks=[bookmark],
    )
    store.save_session(session)
    loaded = store.load_session("LOCAL-001")
    assert loaded is not None
    assert loaded.bookmarks[0].bookmark_id == bookmark.bookmark_id
    assert loaded.bookmarks[0].simulation is True
    assert loaded.events == []

    legacy_path = store.sessions_dir / "LEGACY-001.json"
    legacy_path.write_text(
        '{"session_id":"LEGACY-001","candidate_id":"OLD","exam_name":"Old",'
        '"started_at":"2026-01-01T00:00:00","events":[]}',
        encoding="utf-8",
    )
    legacy = store.load_session("LEGACY-001")
    assert legacy is not None
    assert legacy.bookmarks == []
    assert legacy.schema_version == "vigil.local-session.v1"
