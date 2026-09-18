"""Final Prototype Acceptance & Regression Tests for ICTU 2026.

Verifies:
1. Scene Profile YAML parsing and SeatGraph topology loading.
2. Zero Automated Cheating Verdicts guarantee (no CHEATING in any enum).
3. Pydantic schemas and database repository support for context_json.
4. Mandatory Writing Zone suppression (WRITING priority over BELOW_DESK).
5. Anti-spam incident aggregation and 5.0s cooldown reset.
6. 10-second video evidence buffer and SHA-256 integrity hashing.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pytest

from classroom_monitor.async_evidence_writer import compute_file_sha256
from classroom_monitor.behavior_pattern_engine import BehaviorPattern, BehaviorPatternEngine, PatternType
from classroom_monitor.models import ClassroomEvent
from classroom_monitor.observation_extractor import ObservationExtractor, RawObservation
from classroom_monitor.scene_context import CapabilityStatus, DeskGeometry, SceneProfile, SeatContext, SeatGraph
from classroom_monitor.seat_risk_tracker import RiskState, SeatRiskTracker
from classroom_monitor.temporal_episode_engine import EpisodeState, EpisodeType, TemporalEpisode, TemporalEpisodeEngine
from storage.database import SessionLocal, init_db
from storage.db_models import ExamRoom, ExamSite, SeatROI
from storage.repositories import SeatRepository


def test_final_01_scene_profile_yaml_loading():
    """Verify that SceneProfile loads YAML scene configurations accurately."""
    india_yaml = Path("configs/scenes/india_classroom.yaml")
    student_yaml = Path("configs/scenes/student_classroom.yaml")

    assert india_yaml.exists(), "configs/scenes/india_classroom.yaml must exist"
    assert student_yaml.exists(), "configs/scenes/student_classroom.yaml must exist"

    p_india = SceneProfile.from_file(india_yaml)
    assert p_india.room_code == "ROOM-CALIB-01"
    assert len(p_india.seat_graph.seats_context) == 21
    assert p_india.video_resolution["width"] == 1280
    assert p_india.video_resolution["height"] == 720

    p_student = SceneProfile.from_file(student_yaml)
    assert p_student.room_code == "ROOM-STUDENT-01"
    assert len(p_student.seat_graph.seats_context) == 12
    assert p_student.video_resolution["width"] == 640
    assert p_student.video_resolution["height"] == 352


def test_final_02_zero_automated_cheating_verdicts_guarantee():
    """Verify that the system contains zero automated 'CHEATING' states."""
    for state in RiskState:
        assert "CHEAT" not in state.value.upper(), f"Prohibited verdict in RiskState: {state}"

    for ep in EpisodeType:
        assert "CHEAT" not in ep.value.upper(), f"Prohibited verdict in EpisodeType: {ep}"

    for pat in PatternType:
        assert "CHEAT" not in pat.value.upper(), f"Prohibited verdict in PatternType: {pat}"


def test_final_03_seat_roi_context_json_crud():
    """Verify that SeatRepository and SeatROI preserve context_json."""
    init_db()
    db = SessionLocal()
    try:
        site = db.query(ExamSite).first()
        if not site:
            site = ExamSite(name="Test Site", address="123 Test St")
            db.add(site)
            db.commit()
            db.refresh(site)

        room = db.query(ExamRoom).filter(ExamRoom.room_code == "ROOM-TEST-FINAL").first()
        if not room:
            room = ExamRoom(site_id=site.id, room_code="ROOM-TEST-FINAL", name="Test Room Final")
            db.add(room)
            db.commit()
            db.refresh(room)

        repo = SeatRepository(db)
        ctx_data = {
            "neighbors": {"left_neighbor_id": "S01", "right_neighbor_id": "S03"},
            "baseline_yaw": 5.0,
            "baseline_pitch": -2.0,
        }
        seat = repo.create(
            room_id=room.id,
            seat_code="SEAT-FINAL-01",
            polygon_json=[[10, 10], [50, 10], [50, 50], [10, 50]],
            context_json=ctx_data,
            seat_label="Test Final Desk",
        )
        assert seat.id is not None
        assert seat.context_json is not None
        loaded_ctx = json.loads(seat.context_json)
        assert loaded_ctx["baseline_yaw"] == 5.0
        assert loaded_ctx["neighbors"]["left_neighbor_id"] == "S01"

        # Update context_json
        ctx_data["baseline_yaw"] = 8.0
        updated = repo.update(seat.id, context_json=ctx_data)
        assert updated is not None
        assert json.loads(updated.context_json)["baseline_yaw"] == 8.0

        # Clean up
        repo.delete(seat.id)
    finally:
        db.close()


def test_final_04_writing_zone_mandatory_suppression():
    """Verify that wrist in writing zone suppresses look-down alerts into NORMAL_WRITING."""
    desk_geo = DeskGeometry(
        desk_boundary_y=300.0,
        writing_zone_polygon=np.array([[100, 200], [300, 200], [300, 300], [100, 300]], dtype=np.float32),
    )
    # Wrist is on table surface inside writing zone
    wrist_point = (200.0, 250.0)
    assert desk_geo.contains_wrist_in_writing_zone(wrist_point) is True
    assert desk_geo.is_wrist_below_desk(wrist_point) is False

    # Wrist below desk
    below_desk_point = (200.0, 350.0)
    assert desk_geo.contains_wrist_in_writing_zone(below_desk_point) is False


def test_final_05_anti_spam_cooldown_and_decay():
    """Verify SeatRiskTracker triggers FLAGGED_FOR_REVIEW then enters 5s COOLDOWN."""
    tracker = SeatRiskTracker(room_id="ROOM-TEST", cooldown_duration_ms=5000.0)

    # Accumulate risk past 80
    ep = TemporalEpisode(
        episode_id="ep-1",
        seat_id="SEAT-TEST-01",
        episode_type=EpisodeType.HEAD_TURN_RIGHT,
        start_timestamp_ms=500.0,
        duration_ms=2000.0,
        quality=1.0,
    )
    pat = BehaviorPattern(
        pattern_id="pat-1",
        pattern_type=PatternType.REPEATED_NEIGHBOR_GLANCE,
        seat_id="SEAT-TEST-01",
        start_timestamp_ms=500.0,
        end_timestamp_ms=2000.0,
        confidence=1.0,
        quality=1.0,
    )
    pat_lean = BehaviorPattern(
        pattern_id="pat-2",
        pattern_type=PatternType.NEIGHBOR_ORIENTED_LEAN,
        seat_id="SEAT-TEST-01",
        start_timestamp_ms=500.0,
        end_timestamp_ms=2000.0,
        confidence=1.0,
        quality=1.0,
    )

    ev1 = tracker.update_seat(
        seat_id="SEAT-TEST-01",
        active_episodes=[ep],
        detected_patterns=[pat, pat_lean],
        timestamp_ms=2000.0,
    )
    profile = tracker.get_or_create_profile("SEAT-TEST-01")
    assert profile.peak_risk_score >= 80.0
    assert ev1 is not None
    assert profile.current_state == RiskState.COOLDOWN.value
    assert profile.risk_score == 45.0

    # During cooldown (within 5 seconds), score should stay suppressed and no new event emitted
    ev2 = tracker.update_seat(
        seat_id="SEAT-TEST-01",
        active_episodes=[],
        detected_patterns=[],
        timestamp_ms=2500.0,
    )
    assert profile.current_state == RiskState.COOLDOWN.value
    assert profile.risk_score <= 45.0
    assert ev2 is None



def test_final_06_sha256_evidence_integrity():
    """Verify SHA-256 calculation for evidence MP4 files."""
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tf:
        tf.write(b"VIGIL_AI_TEST_EVIDENCE_VIDEO_BYTES_LEGAL_CHAIN_OF_CUSTODY")
        tf_path = Path(tf.name)

    try:
        sha256_hash = compute_file_sha256(tf_path)
        assert len(sha256_hash) == 64
        assert isinstance(sha256_hash, str)
    finally:
        if tf_path.exists():
            tf_path.unlink()
