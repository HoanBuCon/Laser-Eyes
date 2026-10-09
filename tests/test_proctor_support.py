"""Integration tests for AI-adjacent human proctor support features."""

from __future__ import annotations

import csv
import hashlib
import io
import shutil
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.main import app
from classroom_monitor.demo.runtime import DemoRuntime, DemoStatus
from classroom_monitor.evidence_playback import ensure_browser_playback, probe_video_codec
from exam_monitor.app import persist_local_bookmark
from exam_monitor.events import SessionStore
from exam_monitor.models import SessionInfo
from storage.database import Base, get_db
from storage.db_models import (
    Camera,
    DetectionEvent,
    EventReview,
    EvidenceFile,
    ExamRoom,
    ExamSession,
    ExamSite,
    ProctorBookmark,
    SeatROI,
)


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

    db = TestingSessionLocal()
    try:
        seat = db.query(SeatROI).filter_by(seat_code="S01").one()
        ai_event = DetectionEvent(
            session_id=session_id,
            seat_id=seat.id,
            event_id="AI-EXPORT-001",
            behavior="HEAD_TURN_LEFT",
            primary_signal="HEAD_TURN_LEFT",
            supporting_patterns_json='["REPEATED_NEIGHBOR_GLANCE"]',
            severity="MEDIUM",
            risk_score=42,
            status="FLAGGED_FOR_HUMAN_REVIEW",
            review_status="CONFIRMED",
            start_frame=21,
            start_timestamp=datetime(2026, 9, 29, 10, 0, 1),
            end_timestamp=datetime(2026, 9, 29, 10, 0, 3),
            duration_seconds=2.0,
        )
        db.add(ai_event)
        db.flush()
        db.add(
            EvidenceFile(
                event_id=ai_event.id,
                file_path="data/demo_runs/test/evidence/clip.mp4",
                snapshot_path="data/demo_runs/test/evidence/frame.jpg",
                video_path="data/demo_runs/test/evidence/clip.mp4",
                video_sha256="a" * 64,
                file_size_bytes=1234,
                status="READY",
            )
        )
        db.add(
            EventReview(
                event_id=ai_event.id,
                reviewer_id="judge",
                decision="CONFIRMED",
                reason_code="OBSERVED_POLICY_VIOLATION",
                note="Reviewed with context",
            )
        )
        db.commit()
    finally:
        db.close()

    exported = http.get(f"/api/v1/proctor/sessions/{session_id}/export?format=json")
    assert exported.status_code == 200
    payload = exported.json()
    assert payload["schema_version"] == "vigil.proctor-support.v1.1"
    assert payload["record_count"] == 2
    assert payload["ai_review_incidents"][0]["event_id"] == "AI-EXPORT-001"
    assert payload["ai_review_incidents"][0]["sha256"] == "a" * 64
    assert payload["ai_review_incidents"][0]["review_note"] == "Reviewed with context"
    csv_export = http.get(f"/api/v1/proctor/sessions/{session_id}/export?format=csv")
    assert csv_export.status_code == 200
    assert bookmark["bookmark_id"] in csv_export.text
    rows = list(csv.DictReader(io.StringIO(csv_export.text.lstrip("\ufeff"))))
    assert {row["record_type"] for row in rows} == {"AI_INCIDENT", "MANUAL_BOOKMARK"}
    ai_row = next(row for row in rows if row["record_type"] == "AI_INCIDENT")
    assert ai_row["record_id"] == "AI-EXPORT-001"
    assert ai_row["review_priority_score"] == "42"
    assert ai_row["sha256"] == "a" * 64


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
    assert "Preparing browser-compatible evidence video" in script.text


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg tools unavailable")
def test_mp4v_evidence_gets_h264_browser_derivative(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VIGIL_PLAYBACK_CACHE_ROOT", str(tmp_path / "playback"))
    source = tmp_path / "archive.mp4"
    writer = cv2.VideoWriter(
        str(source),
        cv2.VideoWriter_fourcc(*"mp4v"),
        10.0,
        (64, 48),
    )
    assert writer.isOpened()
    for index in range(20):
        frame = np.full((48, 64, 3), index * 8, dtype=np.uint8)
        writer.write(frame)
    writer.release()
    assert probe_video_codec(source) == "mpeg4"

    playback, derivative = ensure_browser_playback(source)
    assert derivative is True
    assert playback != source
    assert playback.is_file()
    assert probe_video_codec(playback) == "h264"
    assert probe_video_codec(source) == "mpeg4"

    cached, cached_derivative = ensure_browser_playback(source)
    assert cached == playback
    assert cached_derivative is True


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
