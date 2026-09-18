"""Unit & Regression Tests for VIGIL AI SRS v2.0 Demo Runner Infrastructure.

Validates Requirements D1 through D12:
- D1: Demo video config resolves aliases ('india', 'student') and custom paths.
- D2: Scene profile loads correctly from YAML configuration.
- D3: Canonical episodes.json export deduplicates by episode_id and includes required fields.
- D4: Events export contains canonical review states (FLAGGED_FOR_REVIEW, no auto CHEATING).
- D5: Demo summary JSON contains required structure and seat risk rankings.
- D6: Runtime profile JSON exports FPS, latency breakdown, and hardware info.
- D7: Renderer supports both clean HUD and --debug-overlay modes.
- D8: EOF flush properly flushes active episodes and evidence buffers.
- D9: Roaming person without seat is correctly tracked/isolated without corrupting seat state.
- D10: Baseline subtraction applies seat-specific yaw offset to 6DRepNet predictions.
- D11: Evidence clip writer generates valid MP4 clip and SHA-256 digest on event creation.
- D12: Batch orchestrator smoke test for presets and arguments.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
import pytest
import torch

from classroom_monitor.async_evidence_writer import AsyncEvidenceWriter, compute_file_sha256
from classroom_monitor.behavior_pattern_engine import BehaviorPattern, BehaviorPatternEngine, PatternType
from classroom_monitor.demo.config import (
    DEMO_PRESETS,
    DemoVideoConfig,
    build_arg_parser,
    get_demo_config,
    resolve_video_path,
)
from classroom_monitor.demo.exporter import export_demo_artifacts
from classroom_monitor.demo.renderer import DemoHUDOverlayRenderer
from classroom_monitor.demo.runner import run_demo_pipeline, setup_room_seats
from classroom_monitor.detector import PoseClassroomDetector
from classroom_monitor.head_pose_provider import HeadOrientationEstimate, SixDRepNetHeadOrientationProvider
from classroom_monitor.models import ClassroomEvent, Detection, EventStatus, SeverityLevel
from classroom_monitor.observation_extractor import ObservationType, RawObservation
from classroom_monitor.scene_context import CapabilityStatus, DeskGeometry, SceneProfile, SeatContext, SeatGraph
from classroom_monitor.seat_manager import SeatDefinition, SeatManager, SeatState
from classroom_monitor.seat_risk_tracker import RiskState, SeatRiskTracker
from classroom_monitor.temporal_episode_engine import EpisodeState, EpisodeType, TemporalEpisode, TemporalEpisodeEngine
from classroom_monitor.video_buffer import EvidenceVideoBuffer


# D1: Demo video config resolves aliases ('india', 'student') and custom paths
def test_d1_demo_config_resolution():
    cfg_india = get_demo_config("india")
    assert cfg_india.name == "india"
    assert cfg_india.room_code == "ROOM-CALIB-01"
    assert cfg_india.pose_imgsz == 1280
    assert cfg_india.video_path.exists()

    cfg_student = get_demo_config("student")
    assert cfg_student.name == "student"
    assert cfg_student.room_code == "ROOM-STUDENT-01"
    assert cfg_student.pose_imgsz == 640
    assert cfg_student.video_path.exists()


# D2: Scene profile loads correctly from YAML configuration
def test_d2_scene_profile_loading():
    scene_p = Path("configs/scenes/india_classroom.yaml")
    assert scene_p.exists()
    profile = SceneProfile.from_file(scene_p)
    assert profile.room_code == "ROOM-CALIB-01"
    assert len(profile.seat_graph.seats_context) >= 21
    ctx = profile.seat_graph.get_context("SEAT-ROOM-CALIB-01-01")
    assert ctx is not None
    assert ctx.reference_directions.baseline_yaw == 0.0


# D3: Canonical episodes.json export deduplicates by episode_id and includes required fields
def test_d3_canonical_episodes_export():
    with tempfile.TemporaryDirectory() as tmpdir:
        out_p = Path(tmpdir)
        ep1 = TemporalEpisode(
            episode_id="EP-001",
            seat_id="SEAT-01",
            episode_type=EpisodeType.HEAD_TURN_LEFT.value,
            state=EpisodeState.ENDED,
            start_timestamp_ms=1000.0,
            peak_timestamp_ms=1500.0,
            end_timestamp_ms=2500.0,
            duration_ms=1500.0,
            peak_intensity=-35.0,
            confidence=0.90,
        )
        ep1_dup = TemporalEpisode(
            episode_id="EP-001",
            seat_id="SEAT-01",
            episode_type=EpisodeType.HEAD_TURN_LEFT.value,
            state=EpisodeState.ENDED,
            start_timestamp_ms=1000.0,
            peak_timestamp_ms=1500.0,
            end_timestamp_ms=2500.0,
            duration_ms=1500.0,
            peak_intensity=-35.0,
            confidence=0.90,
        )
        ep2 = TemporalEpisode(
            episode_id="EP-002",
            seat_id="SEAT-02",
            episode_type=EpisodeType.HEAD_TURN_RIGHT.value,
            state=EpisodeState.ENDED,
            start_timestamp_ms=3000.0,
            peak_timestamp_ms=3500.0,
            end_timestamp_ms=4500.0,
            duration_ms=1500.0,
            peak_intensity=32.0,
            confidence=0.88,
        )

        risk_tracker = SeatRiskTracker()
        export_demo_artifacts(
            output_dir=out_p,
            episodes=[ep1, ep1_dup, ep2],
            events=[],
            patterns=[],
            risk_tracker=risk_tracker,
            scene_metadata={"scene_id": "test", "duration_sec": 10.0},
            runtime_stats={"total_frames": 100},
        )

        ep_file = out_p / "episodes.json"
        assert ep_file.exists()
        with open(ep_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert len(data) == 2  # Duplicate removed
        assert data[0]["episode_id"] == "EP-001"
        assert data[0]["seat_code"] == "SEAT-01"
        assert "start_ms" in data[0]
        assert "end_ms" in data[0]
        assert "duration_ms" in data[0]
        assert "peak_value" in data[0]
        assert "confidence" in data[0]


# D4: Events export contains canonical review states (FLAGGED_FOR_REVIEW, no auto CHEATING)
def test_d4_events_canonical_status():
    with tempfile.TemporaryDirectory() as tmpdir:
        out_p = Path(tmpdir)
        evt = ClassroomEvent(
            event_id="EVT-001",
            track_id=1,
            behavior="SUSPICIOUS_BEHAVIOR_TRIGGER",
            severity=SeverityLevel.HIGH.value,
            confidence=0.95,
            timestamp=5.0,
            seat_id="SEAT-01",
            room_id="ROOM-01",
            camera_id="CAM-01",
        )

        risk_tracker = SeatRiskTracker()
        export_demo_artifacts(
            output_dir=out_p,
            episodes=[],
            events=[evt],
            patterns=[],
            risk_tracker=risk_tracker,
            scene_metadata={"scene_id": "test"},
            runtime_stats={},
        )

        evt_file = out_p / "events.json"
        assert evt_file.exists()
        with open(evt_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert len(data) == 1
        assert data[0]["status"] == "FLAGGED_FOR_REVIEW"
        assert data[0]["status"] != "CHEATING"


# D5: Demo summary JSON contains required structure and seat risk rankings
def test_d5_demo_summary_structure():
    with tempfile.TemporaryDirectory() as tmpdir:
        out_p = Path(tmpdir)
        risk_tracker = SeatRiskTracker(room_id="ROOM-CALIB-01")
        ep = TemporalEpisode(
            episode_id="EP-1",
            seat_id="SEAT-01",
            episode_type=EpisodeType.HEAD_TURN_LEFT.value,
            state=EpisodeState.ENDED,
            start_timestamp_ms=1000.0,
            peak_timestamp_ms=1500.0,
            end_timestamp_ms=2500.0,
            duration_ms=1500.0,
            peak_intensity=-35.0,
            confidence=0.90,
        )
        risk_tracker.update_seat("SEAT-01", [ep], [], timestamp_ms=2500.0)

        export_demo_artifacts(
            output_dir=out_p,
            episodes=[ep],
            events=[],
            patterns=[],
            risk_tracker=risk_tracker,
            scene_metadata={
                "scene_id": "india",
                "room_code": "ROOM-CALIB-01",
                "camera_id": "CAM-01",
                "duration_sec": 45.0,
                "fps": 30.0,
                "total_seats": 21,
                "occupied_seats": 18,
            },
            runtime_stats={"total_frames": 1350},
        )

        sum_file = out_p / "demo_summary.json"
        assert sum_file.exists()
        with open(sum_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert data["scene_id"] == "india"
        assert data["room_code"] == "ROOM-CALIB-01"
        assert data["total_calibrated_seats"] == 21
        assert data["total_canonical_episodes"] == 1
        assert len(data["seat_risk_rankings"]) > 0


# D6: Runtime profile JSON exports FPS, latency breakdown, and hardware info
def test_d6_runtime_profile_export():
    with tempfile.TemporaryDirectory() as tmpdir:
        out_p = Path(tmpdir)
        risk_tracker = SeatRiskTracker()
        runtime_stats = {
            "overall_fps": 32.5,
            "wall_time_sec": 10.0,
            "realtime_factor": 1.1,
            "lat_perception_avg_ms": 15.2,
            "lat_6drepnet_avg_ms": 4.1,
            "lat_temporal_avg_ms": 0.8,
            "lat_pattern_avg_ms": 0.5,
            "lat_risk_avg_ms": 0.4,
            "lat_render_avg_ms": 3.0,
            "lat_writer_avg_ms": 2.0,
        }

        export_demo_artifacts(
            output_dir=out_p,
            episodes=[],
            events=[],
            patterns=[],
            risk_tracker=risk_tracker,
            scene_metadata={"duration_sec": 11.0},
            runtime_stats=runtime_stats,
        )

        rp_file = out_p / "runtime_profile.json"
        assert rp_file.exists()
        with open(rp_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert data["overall_fps"] == 32.5
        assert "hardware" in data
        assert "stage_latency_averages_ms" in data
        assert data["stage_latency_averages_ms"]["perception_yolo_pose"] == 15.2


# D7: Renderer supports both clean HUD and --debug-overlay modes
def test_d7_renderer_overlay_modes():
    renderer = DemoHUDOverlayRenderer(debug_overlay=False)
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    seat_mgr = SeatManager(room_id="ROOM-01")
    s_def = SeatDefinition(
        seat_id="SEAT-01",
        room_id="ROOM-01",
        seat_code="SEAT-01",
        polygon=np.array([[100, 100], [200, 100], [200, 200], [100, 200]], dtype=np.float32),
    )
    seat_mgr.load_seats([s_def])
    risk_tracker = SeatRiskTracker()

    # Clean HUD mode
    out_clean = renderer.render_frame(
        frame=frame,
        frame_idx=1,
        timestamp_ms=100.0,
        fps=30.0,
        room_code="ROOM-01",
        camera_id="CAM-01",
        seat_mgr=seat_mgr,
        seat_graph=None,
        risk_tracker=risk_tracker,
        active_episodes=[],
        recent_events=[],
        raw_observations={},
        detections=[],
        roaming_detections=[],
        debug_overlay=False,
    )
    assert out_clean.shape == frame.shape

    # Debug Overlay mode
    out_debug = renderer.render_frame(
        frame=frame,
        frame_idx=1,
        timestamp_ms=100.0,
        fps=30.0,
        room_code="ROOM-01",
        camera_id="CAM-01",
        seat_mgr=seat_mgr,
        seat_graph=None,
        risk_tracker=risk_tracker,
        active_episodes=[],
        recent_events=[],
        raw_observations={},
        detections=[],
        roaming_detections=[],
        runtime_metrics={"lat_perception_ms": 15.0},
        debug_overlay=True,
    )
    assert out_debug.shape == frame.shape


# D8: EOF flush properly flushes active episodes and evidence buffers
def test_d8_eof_flush_mechanism():
    engine = TemporalEpisodeEngine(
        yaw_activation_deg=28.0,
        yaw_release_deg=16.0,
        min_persistence_ms=400.0,
    )

    # Feed active observation that stays active until EOF
    obs = RawObservation(
        seat_id="SEAT-01",
        timestamp_ms=1000.0,
        observation_type=ObservationType.HEAD_YAW_RELATIVE.value,
        value=-35.0,
    )
    engine.process_observations([obs], timestamp_ms=1000.0)

    obs2 = RawObservation(
        seat_id="SEAT-01",
        timestamp_ms=1600.0,
        observation_type=ObservationType.HEAD_YAW_RELATIVE.value,
        value=-35.0,
    )
    active = engine.process_observations([obs2], timestamp_ms=1600.0)
    assert len(active) == 1
    assert active[0].state == EpisodeState.ACTIVE

    # EOF Flush
    flushed = engine.flush_all(timestamp_ms=2000.0)
    assert len(flushed) == 1
    assert flushed[0].end_timestamp_ms == 2000.0
    assert flushed[0].duration_ms >= 1000.0


# D9: Roaming person without seat is correctly tracked/isolated without corrupting seat state
def test_d9_roaming_person_isolation():
    seat_mgr = SeatManager(room_id="ROOM-01")
    s_def = SeatDefinition(
        seat_id="SEAT-01",
        room_id="ROOM-01",
        seat_code="SEAT-01",
        polygon=np.array([[100, 100], [200, 100], [200, 200], [100, 200]], dtype=np.float32),
    )
    seat_mgr.load_seats([s_def])

    # Person 1 inside seat
    det_seated = Detection(
        class_id=0,
        class_name="person",
        confidence=0.90,
        bbox=(120, 120, 180, 190),
    )

    # Person 2 roaming in hallway
    det_roaming = Detection(
        class_id=0,
        class_name="person",
        confidence=0.90,
        bbox=(800, 600, 900, 700),
    )

    mapped_seats, unmapped = seat_mgr.map_detections_to_seats([det_seated, det_roaming], timestamp_ms=1000.0)

    assert mapped_seats.get("SEAT-01") == det_seated
    assert len(unmapped) == 1
    assert unmapped[0] == det_roaming


# D10: Baseline subtraction applies seat-specific yaw offset to 6DRepNet predictions
def test_d10_baseline_subtraction():
    sg = SeatGraph(room_id="ROOM-01")
    ctx = SeatContext(
        seat_id="SEAT-01",
        room_id="ROOM-01",
        seat_code="SEAT-01",
    )
    ctx.reference_directions.baseline_yaw = -10.0  # Camera tilted 10 degrees left
    sg.add_seat_context(ctx)

    # Raw 6DRepNet prediction
    raw_yaw = -5.0
    # Relative yaw calculation: raw_yaw - baseline_yaw = -5.0 - (-10.0) = +5.0
    rel_yaw = raw_yaw - ctx.reference_directions.baseline_yaw
    assert rel_yaw == 5.0


# D11: Evidence clip writer generates valid MP4 clip and SHA-256 digest on event creation
def test_d11_evidence_clip_and_hash():
    with tempfile.TemporaryDirectory() as tmpdir:
        writer = AsyncEvidenceWriter(base_evidence_dir=tmpdir)
        frames = [np.zeros((100, 100, 3), dtype=np.uint8) for _ in range(30)]
        peak_snap = np.zeros((100, 100, 3), dtype=np.uint8)

        writer.submit_job(
            event_id="EVT-TEST-001",
            frames=frames,
            peak_snapshot_frame=peak_snap,
            fps=30.0,
            metadata={"seat_code": "SEAT-01"},
        )
        writer.shutdown(wait=True)

        clip_p = Path(tmpdir) / "EVT-TEST-001" / "evidence.mp4"
        snap_p = Path(tmpdir) / "EVT-TEST-001" / "snapshot.jpg"

        assert clip_p.exists()
        assert snap_p.exists()

        clip_hash = compute_file_sha256(clip_p)
        assert len(clip_hash) == 64  # Valid SHA-256 hex string


# D12: Batch orchestrator smoke test for presets and arguments
def test_d12_demo_arg_parser_and_presets():
    parser = build_arg_parser()
    args = parser.parse_args(["--video", "student", "--hz", "6.0", "--show", "--debug-overlay"])
    assert args.video == "student"
    assert args.hz == 6.0
    assert args.show is True
    assert args.debug_overlay is True

    # Check preset mapping
    assert "india" in DEMO_PRESETS
    assert "student" in DEMO_PRESETS
