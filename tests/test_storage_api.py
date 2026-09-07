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
from classroom_monitor.models import ClassroomEvent
from storage.database import Base, get_db
from storage.evidence_store import EvidenceStore
from storage.repositories import (
    CameraRepository,
    EventRepository,
    RoomRepository,
    SessionRepository,
    SiteRepository,
    StatisticsRepository,
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
