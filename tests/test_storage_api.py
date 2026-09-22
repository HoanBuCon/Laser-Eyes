"""Integration Tests for Database Storage Layer, Repositories, and FastAPI REST API."""

from __future__ import annotations

import os
from pathlib import Path
import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.main import app
from classroom_monitor.models import ClassroomEvent, Detection
from classroom_monitor.seat_manager import SeatDefinition, SeatManager, SeatState
from storage.database import Base, get_db
from storage.evidence_store import EvidenceStore
from storage.repositories import (
    AuditLogRepository,
    CameraRepository,
    EventRepository,
    ReviewRepository,
    RoomRepository,
    SeatRepository,
    SessionRepository,
    SiteRepository,
    StatisticsRepository,
    WorkerRepository,
)

# Test in-memory SQLite database using StaticPool to share connection across threads/sessions
TEST_DB_URL = "sqlite:///:memory:"
test_engine = create_engine(
    TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(scope="function")
def db_session():
    """Create fresh database tables for each test."""
    Base.metadata.create_all(bind=test_engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=test_engine)


@pytest.fixture(scope="function")
def client(db_session):
    """FastAPI test client with injected test database session."""
    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ==============================================================================
# 1. Storage & Repositories Tests
# ==============================================================================
def test_repositories_crud(db_session):
    """Test full repository hierarchy creation and relational linking."""
    site_repo = SiteRepository(db_session)
    room_repo = RoomRepository(db_session)
    cam_repo = CameraRepository(db_session)
    sess_repo = SessionRepository(db_session)
    event_repo = EventRepository(db_session)
    stats_repo = StatisticsRepository(db_session)

    # 1. Create Site
    site = site_repo.create(name="ĐH Bách Khoa", address="TP.HCM", contact_info="admin@hcmut.edu.vn")
    assert site.id is not None
    assert site.name == "ĐH Bách Khoa"

    # 2. Create Room
    room = room_repo.create(site_id=site.id, name="Phòng 302", capacity=40)
    assert room.id is not None
    assert room.site_id == site.id

    # 3. Create Camera
    cam = cam_repo.create(room_id=room.id, name="Cam 1", source_uri="0")
    assert cam.id is not None

    # 4. Create Session
    session = sess_repo.create(room_id=room.id, exam_name="Toán Giải Tích 1", camera_id=cam.id)
    assert session.id is not None
    assert session.status == "running"

    # 5. Record Event
    dummy_event = ClassroomEvent(
        event_id="EVT-TEST01",
        track_id=1,
        behavior="phone using",
        severity="HIGH",
        confidence_avg=0.92,
        confidence_peak=0.95,
        start_frame=100,
        end_frame=150,
        duration_seconds=1.67,
    )
    db_event = event_repo.record_event(session_id=session.id, event=dummy_event)
    assert db_event.id is not None
    assert db_event.event_id == "EVT-TEST01"

    # 6. Recalculate Risk Score
    risk = sess_repo.recalculate_risk_score(session.id)
    assert risk == 25  # 1 HIGH event = 25 points

    # 7. Statistics
    summary = stats_repo.get_overall_summary(site_id=site.id)
    assert summary["total_events"] == 1
    assert summary["events_by_behavior"]["phone using"] == 1


def test_evidence_store(tmp_path):
    """Test saving and retrieving evidence frame images."""
    store = EvidenceStore(base_dir=tmp_path)
    dummy_img = np.zeros((100, 100, 3), dtype=np.uint8)

    rel_path, size_bytes = store.save_evidence(
        event_id="EVT-001",
        frame=dummy_img,
        site_id="siteA",
        room_id="roomB",
        session_id="sessC",
    )

    assert rel_path is not None
    assert size_bytes > 0
    abs_path = store.get_absolute_path(rel_path)
    assert abs_path is not None
    assert abs_path.exists()


# ==============================================================================
# 2. FastAPI REST Endpoints Tests
# ==============================================================================
def test_api_health(client):
    """Test health check endpoint."""
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_api_sites_and_rooms(client, db_session):
    """Test creating and listing sites and rooms via HTTP endpoints."""
    # Create Site
    site_payload = {"name": "ĐH Khoa Học Tự Nhiên", "address": "227 Nguyễn Văn Cừ", "contact_info": "admin@khtn.edu.vn"}
    res_site = client.post("/api/sites/", json=site_payload)
    assert res_site.status_code == 201
    site_data = res_site.json()
    assert site_data["name"] == site_payload["name"]

    site_id = site_data["id"]

    # Create Room
    room_payload = {"site_id": site_id, "name": "Phòng F102", "capacity": 50, "description": "Tầng 1"}
    res_room = client.post("/api/rooms/", json=room_payload)
    assert res_room.status_code == 201
    room_data = res_room.json()
    assert room_data["name"] == room_payload["name"]

    # List Rooms
    res_list = client.get(f"/api/rooms/?site_id={site_id}")
    assert res_list.status_code == 200
    assert len(res_list.json()) == 1


def test_api_statistics_and_dashboard(client, db_session):
    """Test dashboard serving and statistics JSON endpoints."""
    res_stats = client.get("/api/statistics/summary")
    assert res_stats.status_code == 200
    assert "total_events" in res_stats.json()

    res_rank = client.get("/api/statistics/rankings")
    assert res_rank.status_code == 200
    assert isinstance(res_rank.json(), list)

    res_dash = client.get("/")
    assert res_dash.status_code == 200
    assert "VIGIL AI" in res_dash.text


def test_seat_repository_and_seat_manager(db_session):
    """Test Seat ROI polygon CRUD and Seat-based identity matching under occlusion."""
    room_repo = RoomRepository(db_session)
    seat_repo = SeatRepository(db_session)

    room = room_repo.create(name="Phòng 101", room_code="A101")
    assert room.id is not None

    # 1. Create Seats via Repository
    polygon_a01 = [[100, 100], [300, 100], [300, 400], [100, 400]]
    polygon_a02 = [[400, 100], [600, 100], [600, 400], [400, 400]]

    seat1 = seat_repo.create(room_id=room.id, seat_code="A101_S01", polygon_json=polygon_a01, seat_label="Row 1 Desk 1")
    seat2 = seat_repo.create(room_id=room.id, seat_code="A101_S02", polygon_json=polygon_a02, seat_label="Row 1 Desk 2")

    seats = seat_repo.list_by_room(room.id)
    assert len(seats) == 2

    # 2. Test SeatManager mapping
    mgr = SeatManager(room_id=room.id, occlusion_grace_period_ms=3000.0)
    mgr.load_seats([
        {"id": seat1.id, "room_id": room.id, "seat_code": "A101_S01", "polygon_json": polygon_a01},
        {"id": seat2.id, "room_id": room.id, "seat_code": "A101_S02", "polygon_json": polygon_a02},
    ])

    # Candidate 1 in Seat 1 (bbox 150, 150, 250, 350 -> bottom center (200, 320) inside A01)
    det1 = Detection(class_id=0, class_name="person", confidence=0.9, bbox=(150, 150, 250, 350), frame_index=0)
    # Proctor outside all seats (bbox 700, 500, 800, 700)
    det_proctor = Detection(class_id=0, class_name="person", confidence=0.85, bbox=(700, 500, 800, 700), frame_index=0)

    mapped, unmapped = mgr.map_detections_to_seats([det1, det_proctor], timestamp_ms=1000.0)
    assert mapped["A101_S01"] is not None
    assert mapped["A101_S02"] is None  # Seat 2 is empty
    assert len(unmapped) == 1  # Proctor unmapped

    # 3. Test Proctor Occlusion (Candidate 1 temporarily occluded at t = 2000ms)
    mapped_occ, _ = mgr.map_detections_to_seats([], timestamp_ms=2000.0)
    assert mapped_occ["A101_S01"] is None
    assert mgr.occupancies["A101_S01"].state == SeatState.OCCLUDED

    # 4. Candidate 1 reappears with new detector track ID -> mapped back to stable Seat ID!
    det1_new_id = Detection(class_id=0, class_name="person", confidence=0.88, bbox=(152, 150, 252, 350), frame_index=30)
    mapped_reappear, _ = mgr.map_detections_to_seats([det1_new_id], timestamp_ms=4000.0)
    assert mapped_reappear["A101_S01"] is not None
    assert mgr.occupancies["A101_S01"].state == SeatState.OCCUPIED

    # 5. Test Seat API GET & PUT endpoints
    updated_seat = seat_repo.update(seat1.id, seat_label="Updated Label Desk 1", enabled=False)
    assert updated_seat.seat_label == "Updated Label Desk 1"
    assert updated_seat.enabled is False


def test_workers_and_reviews_api(client, db_session):
    """Test Worker Registration, Heartbeat, and Human Review Flow."""
    # 1. Register Worker
    worker_payload = {
        "worker_id": "worker-node-test-01",
        "hostname": "gpu-test.local",
        "gpu_name": "NVIDIA RTX 4060",
        "gpu_memory_mb": 8192,
        "max_active_streams": 10,
        "version": "1.0.0",
    }
    res_reg = client.post("/api/v1/workers/register", json=worker_payload)
    assert res_reg.status_code == 200
    assert res_reg.json()["id"] == worker_payload["worker_id"]

    # 2. Worker Heartbeat
    hb_payload = {"worker_id": "worker-node-test-01", "active_camera_count": 4}
    res_hb = client.post("/api/v1/workers/heartbeat", json=hb_payload)
    assert res_hb.status_code == 200
    assert res_hb.json()["active_camera_count"] == 4

    # 3. Create Session and Event for Review
    room_repo = RoomRepository(db_session)
    sess_repo = SessionRepository(db_session)
    event_repo = EventRepository(db_session)

    room = room_repo.create(name="Phòng Review Test")
    session = sess_repo.create(room_id=room.id, exam_name="Review Exam")
    dummy_event = ClassroomEvent(
        event_id="EVT-REV-001",
        track_id=3,
        behavior="PROLONGED_HEAD_TURN",
        severity="HIGH",
        confidence_avg=0.89,
        confidence_peak=0.94,
        start_frame=10,
        end_frame=40,
        duration_seconds=1.0,
    )
    db_evt = event_repo.create_from_domain_event(session_id=session.id, event=dummy_event, room_id=room.id)

    # 4. Human Review Submission via API
    review_payload = {
        "reviewer_id": "proctor_nguyen_van_a",
        "decision": "CONFIRMED",
        "reason_code": "TRUE_SUSPICIOUS",
        "note": "Xác nhận quay sang nhìn bài bạn dãy bên",
    }
    res_rev = client.post(f"/api/v1/events/{db_evt.id}/review", json=review_payload)
    assert res_rev.status_code == 200
    rev_data = res_rev.json()
    assert rev_data["decision"] == "CONFIRMED"
    assert rev_data["reviewer_id"] == "proctor_nguyen_van_a"

    # Human decision is durable but must not overwrite the independent AI status.
    res_get = client.get(f"/api/v1/events/{db_evt.id}")
    assert res_get.status_code == 200
    assert res_get.json()["review_status"] == "CONFIRMED"
    assert res_get.json()["status"] == "PENDING"

    db_session.expire_all()
    refreshed_evt = event_repo.get_by_id(db_evt.id)
    assert refreshed_evt.review_status == "CONFIRMED"


