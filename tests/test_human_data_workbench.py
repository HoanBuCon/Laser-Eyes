"""Automated Unit & Integration Test Suite for VIGIL AI Human Data Operations Workbench.

Tests:
1. DB Models & Repositories (CRUD, revisions, episodes, datasets, staged protocols)
2. Domain Services (Actor Crop extraction, Temporal IoU engine, Spatial quality check, Group-Safe splits)
3. REST API Endpoints (/api/v1/data/*)
4. UI Routing (/data-workbench)
"""

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.main import app
from storage.database import Base, get_db
from storage.data_workbench_service import (
    DataWorkbenchService,
    compute_group_split,
)
from storage.db_models import (
    DetectionEvent,
    ExamRoom,
    ExamSession,
    MediaAsset,
    SeatROI,
)
from storage.repositories import (
    DatasetRepository,
    EventRepository,
    ImageRevisionRepository,
    MediaAssetRepository,
    SeatRepository,
    StagedSessionRepository,
    TemporalEpisodeRepository,
)


from sqlalchemy.pool import StaticPool

# Test database setup (in-memory SQLite with StaticPool)
TEST_DB_URL = "sqlite:///:memory:"
test_engine = create_engine(
    TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(scope="function")
def db_session():
    """Create fresh isolated database session per test."""
    Base.metadata.create_all(bind=test_engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=test_engine)


@pytest.fixture(scope="function")
def client(db_session):
    """FastAPI TestClient with overridden database dependency."""
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# ==============================================================================
# 1. TEST REPOSITORIES & DATA MODELS
# ==============================================================================

def test_media_asset_and_revision_repo(db_session):
    """Test MediaAssetRepository and ImageRevisionRepository CRUD and provenance."""
    asset_repo = MediaAssetRepository(db_session)
    rev_repo = ImageRevisionRepository(db_session)

    # 1. Create MediaAsset
    asset = asset_repo.create(
        file_path="dataset/train/images/test_01.png",
        relative_path="dataset/train/images/test_01.png",
        file_name="test_01.png",
        asset_type="IMAGE",
        original_split="train",
        width=640,
        height=480,
    )
    assert asset.id is not None
    assert asset.audit_status == "UNAUDITED"

    # 2. Save Non-destructive revision
    rev = rev_repo.save_revision(
        asset_id=asset.id,
        bbox_index=0,
        original_class="side peeking",
        reviewed_class="HEAD_TURN_LEFT",
        bbox_json=[0.5, 0.5, 0.2, 0.4],
        is_ambiguous=False,
        posture_tags_json=["LOOK_LEFT"],
        audit_notes="Clear head turn toward left desk",
        reviewer_id="lead_annotator",
    )
    assert rev.id is not None
    assert rev.reviewed_class == "HEAD_TURN_LEFT"

    # 3. Check asset updated to AUDITED
    db_session.refresh(asset)
    assert asset.audit_status == "AUDITED"

    # 4. Summary metrics
    summary = asset_repo.get_summary()
    assert summary["total_images"] == 1
    assert summary["audited_images"] == 1
    assert summary["total_revisions"] == 1


def test_temporal_episode_and_iou_comparison(db_session):
    """Test TemporalEpisodeRepository and Temporal IoU calculation."""
    asset_repo = MediaAssetRepository(db_session)
    ep_repo = TemporalEpisodeRepository(db_session)

    # Create video asset
    v_asset = asset_repo.create(
        file_path="demo_video/test.mp4",
        relative_path="demo_video/test.mp4",
        file_name="test.mp4",
        asset_type="VIDEO",
        duration_seconds=20.0,
    )

    # Create Human GT Episode: [3000ms, 6000ms] (duration = 3000ms)
    ep_h = ep_repo.create(
        asset_id=v_asset.id,
        episode_type="HEAD_TURN_LEFT",
        start_ms=3000.0,
        peak_ms=4500.0,
        end_ms=6000.0,
        seat_code="SEAT-01",
        is_ai_proposal=False,
    )
    assert ep_h.duration_ms == 3000.0

    # Create AI Proposal Episode: [3200ms, 5800ms]
    # Intersection = min(6000, 5800) - max(3000, 3200) = 5800 - 3200 = 2600ms
    # Union = max(6000, 5800) - min(3000, 3200) = 6000 - 3000 = 3000ms
    # IoU = 2600 / 3000 = 0.8667
    ep_ai = ep_repo.create(
        asset_id=v_asset.id,
        episode_type="HEAD_TURN_LEFT",
        start_ms=3200.0,
        peak_ms=4600.0,
        end_ms=5800.0,
        seat_code="SEAT-01",
        is_ai_proposal=True,
    )

    # Compute Comparison
    comp = ep_repo.compare_human_vs_ai(v_asset.id, iou_threshold=0.30)
    assert comp["total_human_episodes"] == 1
    assert comp["total_ai_proposals"] == 1
    assert comp["true_positives"] == 1
    assert comp["precision"] == 1.0
    assert comp["recall"] == 1.0
    assert comp["f1_score"] == 1.0
    assert 0.85 <= comp["average_temporal_iou"] <= 0.88
    assert len(comp["matched_pairs"]) == 1
    assert comp["matched_pairs"][0]["is_label_match"] is True


def test_group_safe_split_determinism():
    """Verify deterministic group-safe hash partition prevents split leakage."""
    # Test identical group keys always produce identical split
    split1 = compute_group_split("video_001_classroom", train_ratio=0.70, val_ratio=0.15)
    split2 = compute_group_split("video_001_classroom", train_ratio=0.70, val_ratio=0.15)
    assert split1 == split2
    assert split1 in ["train", "val", "test"]

    # Verify ratio bounds over large sample
    splits = [compute_group_split(f"group_{i}", 0.70, 0.15) for i in range(1000)]
    train_pct = splits.count("train") / 1000.0
    val_pct = splits.count("val") / 1000.0
    test_pct = splits.count("test") / 1000.0

    assert 0.65 <= train_pct <= 0.75
    assert 0.10 <= val_pct <= 0.20
    assert 0.10 <= test_pct <= 0.20


# ==============================================================================
# 2. TEST DOMAIN SERVICES
# ==============================================================================

def test_spatial_calibration_validator(db_session):
    """Test Spatial Context Quality Validator on valid and invalid seat geometries."""
    service = DataWorkbenchService(db_session)

    # Valid non-overlapping 4-point seats
    valid_seats = [
        {"seat_code": "SEAT-01", "polygon_json": [[0.1, 0.1], [0.4, 0.1], [0.4, 0.4], [0.1, 0.4]]},
        {"seat_code": "SEAT-02", "polygon_json": [[0.6, 0.1], [0.9, 0.1], [0.9, 0.4], [0.6, 0.4]]},
    ]
    rep = service.validate_spatial_calibration(valid_seats)
    assert rep["valid"] is True
    assert len(rep["errors"]) == 0

    # Invalid seats (< 4 points and overlapping)
    invalid_seats = [
        {"seat_code": "SEAT-01", "polygon_json": [[0.1, 0.1], [0.5, 0.1], [0.5, 0.5]]},  # only 3 points
        {"seat_code": "SEAT-02", "polygon_json": [[0.1, 0.1], [0.5, 0.1], [0.5, 0.5], [0.1, 0.5]]},
        {"seat_code": "SEAT-03", "polygon_json": [[0.12, 0.12], [0.48, 0.12], [0.48, 0.48], [0.12, 0.48]]},  # heavy overlap with SEAT-02
    ]
    rep_inv = service.validate_spatial_calibration(invalid_seats)
    assert rep_inv["valid"] is False
    assert len(rep_inv["errors"]) >= 1


# ==============================================================================
# 3. TEST REST API ENDPOINTS
# ==============================================================================

def test_api_workbench_summary(client, db_session):
    """Test GET /api/v1/data/summary endpoint."""
    res = client.get("/api/v1/data/summary")
    assert res.status_code == 200
    data = res.json()
    assert "total_images" in data
    assert "audit_percentage" in data
    assert "standardized_observable_labels" in data


def test_api_image_audit_flow(client, db_session):
    """Test GET /api/v1/data/images and POST /api/v1/data/images/{id}/review."""
    asset_repo = MediaAssetRepository(db_session)
    asset = asset_repo.create(
        file_path="dataset/train/images/dummy.png",
        relative_path="dataset/train/images/dummy.png",
        file_name="dummy.png",
        asset_type="IMAGE",
        original_split="train",
    )

    # 1. List images
    res = client.get("/api/v1/data/images")
    assert res.status_code == 200
    items = res.json()["items"]
    assert len(items) >= 1

    # 2. Get detail
    res_detail = client.get(f"/api/v1/data/images/{asset.id}")
    assert res_detail.status_code == 200
    assert res_detail.json()["file_name"] == "dummy.png"

    # 3. Submit review
    review_payload = {
        "revisions": [
            {
                "bbox_index": 0,
                "original_class": "side peeking",
                "reviewed_class": "HEAD_TURN_LEFT",
                "bbox": [0.5, 0.5, 0.3, 0.4],
                "is_ambiguous": False,
                "is_rejected": False,
                "audit_notes": "Reviewed and confirmed",
            }
        ],
        "overall_status": "AUDITED",
        "reviewer_id": "test_engineer",
    }
    res_rev = client.post(f"/api/v1/data/images/{asset.id}/review", json=review_payload)
    assert res_rev.status_code == 200
    assert res_rev.json()["status"] == "success"


def test_api_video_episodes_crud(client, db_session):
    """Test CRUD operations on /api/v1/data/videos/{id}/episodes."""
    asset_repo = MediaAssetRepository(db_session)
    v_asset = asset_repo.create(
        file_path="demo_video/india_classroom.mp4",
        relative_path="demo_video/india_classroom.mp4",
        file_name="india_classroom.mp4",
        asset_type="VIDEO",
        duration_seconds=30.0,
    )

    # 1. Create Ground-Truth Episode
    ep_payload = {
        "episode_type": "HEAD_TURN_LEFT",
        "start_ms": 3000.0,
        "peak_ms": 4500.0,
        "end_ms": 6000.0,
        "seat_code": "SEAT-01",
        "target_neighbor_id": "SEAT-02",
        "confidence": 1.0,
        "reviewer_id": "proctor_lead",
        "notes": "Clear gaze deflection",
    }
    res_post = client.post(f"/api/v1/data/videos/{v_asset.id}/episodes", json=ep_payload)
    assert res_post.status_code == 200
    ep_id = res_post.json()["episode_id"]

    # 2. List episodes
    res_list = client.get(f"/api/v1/data/videos/{v_asset.id}/episodes")
    assert res_list.status_code == 200
    assert len(res_list.json()["episodes"]) >= 1

    # 3. Update episode
    res_put = client.put(
        f"/api/v1/data/videos/{v_asset.id}/episodes/{ep_id}",
        json={"notes": "Updated note"},
    )
    assert res_put.status_code == 200

    # 4. Delete episode
    res_del = client.delete(f"/api/v1/data/videos/{v_asset.id}/episodes/{ep_id}")
    assert res_del.status_code == 200


def test_api_event_review_queue(client, db_session):
    """Test AI Event review queue and triage endpoint."""
    room = ExamRoom(name="Room 101", room_code="R101")
    db_session.add(room)
    db_session.commit()

    session = ExamSession(room_id=room.id, exam_name="Final Exam")
    db_session.add(session)
    db_session.commit()

    event = DetectionEvent(
        session_id=session.id,
        room_id=room.id,
        event_id="EVT-TEST-01",
        behavior="PROLONGED_HEAD_TURN",
        severity="HIGH",
        risk_score=85,
        review_status="PENDING",
    )
    db_session.add(event)
    db_session.commit()

    # 1. Query review queue
    res_q = client.get("/api/v1/data/events/review-queue")
    assert res_q.status_code == 200
    assert len(res_q.json()["items"]) >= 1

    # 2. Submit review decision
    rev_payload = {
        "decision": "CONFIRMED",
        "reason_code": "TRUE_SUSPICIOUS",
        "note": "Validated by Lead Proctor",
        "reviewer_id": "chief_proctor",
    }
    res_rev = client.post(f"/api/v1/data/events/{event.id}/review", json=rev_payload)
    assert res_rev.status_code == 200
    assert res_rev.json()["decision"] == "CONFIRMED"


def test_api_ui_routes(client):
    """Test serving UI pages."""
    # Data Workbench UI
    res_wb = client.get("/data-workbench")
    assert res_wb.status_code == 200
    assert "VIGIL AI" in res_wb.text
    assert "Data Operations Workbench" in res_wb.text

    # Main Dashboard & Calibration
    res_dash = client.get("/")
    assert res_dash.status_code == 200

    res_cal = client.get("/calibration")
    assert res_cal.status_code == 200


def test_api_seats_listing(client, db_session):
    """Test GET /api/v1/data/seats endpoint for video overlay with room filtering and bulk sync."""
    room1 = ExamRoom(name="Room 101", room_code="ROOM-101")
    room2 = ExamRoom(name="Room 102", room_code="ROOM-102")
    db_session.add_all([room1, room2])
    db_session.commit()

    seat1 = SeatROI(
        room_id=room1.id,
        seat_code="SEAT-101-01",
        seat_label="Desk 1",
        polygon_json=json.dumps([[100, 200], [300, 200], [300, 400], [100, 400]]),
        enabled=True,
    )
    seat2 = SeatROI(
        room_id=room2.id,
        seat_code="SEAT-102-01",
        seat_label="Desk 2",
        polygon_json=json.dumps([[150, 250], [350, 250], [350, 450], [150, 450]]),
        enabled=True,
    )
    db_session.add_all([seat1, seat2])
    db_session.commit()

    # 1. Test fetch all seats
    res = client.get("/api/v1/data/seats")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 2

    # 2. Test filter by room_id
    res_r1 = client.get(f"/api/v1/data/seats?room_id={room1.id}")
    assert res_r1.status_code == 200
    data_r1 = res_r1.json()
    assert data_r1["total"] == 1
    assert data_r1["seats"][0]["seat_code"] == "SEAT-101-01"

    # 3. Test Bulk Upsert sync with replacement of missing seats
    bulk_payload = {
        "room_id": room1.id,
        "camera_id": "cam-01",
        "seats": [
            {
                "room_id": room1.id,
                "seat_code": "SEAT-101-NEW",
                "seat_label": "New Calibrated Desk",
                "polygon_json": [[50, 50], [150, 50], [150, 150], [50, 150]],
                "enabled": True,
            }
        ],
    }
    res_bulk = client.post(f"/api/v1/rooms/{room1.id}/seats/bulk", json=bulk_payload)
    assert res_bulk.status_code == 200
    assert len(res_bulk.json()) == 1

    # Verify old seat in room 1 was replaced
    res_r1_after = client.get(f"/api/v1/data/seats?room_id={room1.id}")
    assert res_r1_after.json()["total"] == 1
    assert res_r1_after.json()["seats"][0]["seat_code"] == "SEAT-101-NEW"

    # 4. Test delete single seat
    res_del = client.delete(f"/api/v1/data/seats/by-code/SEAT-101-NEW")
    assert res_del.status_code == 200
    assert res_del.json()["status"] == "deleted"

    res_after = client.get(f"/api/v1/data/seats?room_id={room1.id}")
    assert res_after.json()["total"] == 0


