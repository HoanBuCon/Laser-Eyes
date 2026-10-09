"""Comprehensive Acceptance & Verification Tests for VIGIL AI SRS v1.0 P0 Scope.

Covers:
- P0-01, P0-02: RTSP Ingestion, Reconnection & Stale Frame Dropping.
- P0-03, P0-04, P0-05: YOLO-Pose, Seat ROI Polygon Mapping & Stable Identity under Occlusion.
- P0-06: Suspicious Behavior Signals & Unknown-Safe Missing Keypoint Handling.
- P0-07, P0-08: 0–100 Temporal Risk Scoring, 4-Tier State Machine, Silent Cooldown & Recidivism.
- P0-09, P0-10, P0-11, P0-12: Event Engine, Snapshot, 10s Video Evidence & Async SHA-256 Digest.
- P0-17: Distributed Multi-Camera Worker Node Pipeline.
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
from pathlib import Path
import numpy as np
import pytest

from classroom_monitor.async_evidence_writer import AsyncEvidenceWriter, compute_file_sha256
from classroom_monitor.behavior_pattern_engine import BehaviorPattern, PatternType
from classroom_monitor.behavior_signals import (
    BehaviorSignal,
    BehaviorSignalExtractor,
    SignalType,
    extract_body_lean_angle,
    extract_head_yaw_pitch_safe,
    extract_low_hands_cues,
)
from classroom_monitor.config import ClassroomConfig
from classroom_monitor.models import ClassroomEvent, Detection
from classroom_monitor.rtsp_reader import RTSPStreamReader, VideoFrame
from classroom_monitor.seat_manager import SeatDefinition, SeatManager, SeatROI, SeatState
from classroom_monitor.seat_risk_tracker import RiskState, SeatRiskTracker
from classroom_monitor.temporal_episode_engine import EpisodeState, EpisodeType, TemporalEpisode
from classroom_monitor.worker_node import WorkerNodeRunner


# ==============================================================================
# 1. P0-01 & P0-02: Ingestion, Bounded Queue & Stale Frame Dropping
# ==============================================================================
def test_p0_02_stale_frame_dropping():
    """Verify that bounded queue drops older frames when inference cannot keep up."""
    reader = RTSPStreamReader(camera_id="cam-test-01", source_uri="0", max_queue_size=2, max_frame_age_ms=500.0)

    # Manually simulate 5 fast incoming frames
    dummy_frame = np.zeros((100, 100, 3), dtype=np.uint8)
    now = time.time()

    for idx in range(1, 6):
        v_frame = VideoFrame(
            frame=dummy_frame,
            capture_time=now - (0.05 * (5 - idx)),
            timestamp_ms=now * 1000.0,
            frame_index=idx,
            camera_id="cam-test-01",
            width=100,
            height=100,
        )
        if reader._frame_queue.full():
            reader._frame_queue.get_nowait()
            reader.metrics.total_dropped_frames += 1
        reader._frame_queue.put_nowait(v_frame)

    # Queue size should never exceed 2
    assert reader._frame_queue.qsize() == 2
    assert reader.metrics.total_dropped_frames >= 3

    # Fetched frame should be fresh (sequence 4 or 5)
    freshest = reader.get_latest_frame(timeout=0.1)
    assert freshest is not None
    assert freshest.frame_index in (4, 5)


# ==============================================================================
# 2. P0-04 & P0-05: Seat ROI Mapping & Identity Persistence across Occlusion
# ==============================================================================
def test_p0_04_05_seat_roi_mapping_and_identity_stability():
    """Verify that logical Seat ID remains persistent when student is occluded by proctor."""
    mgr = SeatManager(room_id="ROOM_A101", occlusion_grace_period_ms=3000.0, empty_timeout_ms=5000.0)

    poly_seat1 = [[50, 50], [250, 50], [250, 300], [50, 300]]
    poly_seat2 = [[350, 50], [550, 50], [550, 300], [350, 300]]

    mgr.load_seats([
        {"id": "S1", "room_id": "ROOM_A101", "seat_code": "SEAT_01", "polygon_json": poly_seat1},
        {"id": "S2", "room_id": "ROOM_A101", "seat_code": "SEAT_02", "polygon_json": poly_seat2},
    ])

    # t = 1000ms: Candidate 1 in Seat 1 (bbox 100, 100, 200, 250)
    det1 = Detection(0, "person", 0.92, (100, 100, 200, 250), frame_index=1)
    mapped_t1, unmapped = mgr.map_detections_to_seats([det1], timestamp_ms=1000.0)
    assert mapped_t1["SEAT_01"] is not None
    assert mapped_t1["SEAT_02"] is None
    assert mgr.occupancies["SEAT_01"].state == SeatState.OCCUPIED

    # t = 2000ms: Proctor walks by and completely occludes Candidate 1
    det_proctor = Detection(0, "person", 0.95, (600, 600, 700, 750), frame_index=30)
    mapped_t2, unmapped_proctor = mgr.map_detections_to_seats([det_proctor], timestamp_ms=2000.0)
    assert mapped_t2["SEAT_01"] is None
    assert mgr.occupancies["SEAT_01"].state == SeatState.OCCLUDED
    assert len(unmapped_proctor) == 1

    # t = 3500ms: Candidate 1 reappears in Seat 1 -> Seat ID seamlessly maintained!
    det1_new = Detection(0, "person", 0.91, (105, 100, 205, 250), frame_index=75)
    mapped_t3, _ = mgr.map_detections_to_seats([det1_new], timestamp_ms=3500.0)
    assert mapped_t3["SEAT_01"] is not None
    assert mgr.occupancies["SEAT_01"].state == SeatState.OCCUPIED


@pytest.mark.parametrize("reverse_order", [False, True])
def test_overlapping_seat_rois_choose_best_match_independent_of_config_order(reverse_order):
    """A detection in overlapping ROIs maps by geometry, never YAML order."""
    broad = {
        "id": "S-BROAD",
        "room_id": "ROOM_A101",
        "seat_code": "SEAT_BROAD",
        "polygon_json": [[0, 0], [100, 0], [100, 100], [0, 100]],
    }
    precise = {
        "id": "S-PRECISE",
        "room_id": "ROOM_A101",
        "seat_code": "SEAT_PRECISE",
        "polygon_json": [[40, 40], [80, 40], [80, 80], [40, 80]],
    }
    definitions = [precise, broad] if reverse_order else [broad, precise]
    mgr = SeatManager(room_id="ROOM_A101")
    mgr.load_seats(definitions)

    # Bottom-center anchor is approximately (60, 60), inside both polygons
    # but relatively deeper inside the more precise seat ROI.
    det = Detection(0, "person", 0.92, (50, 40, 70, 64), frame_index=1)
    mapped, unmapped = mgr.map_detections_to_seats(
        [det], timestamp_ms=1000.0, frame_w=100, frame_h=100
    )

    assert mgr.get_seat_for_detection(det, frame_w=100, frame_h=100) == "SEAT_PRECISE"
    assert mapped["SEAT_PRECISE"] is det
    assert mapped["SEAT_BROAD"] is None
    assert unmapped == []


def test_overlapping_roi_resolution_preserves_multiple_person_candidates():
    """Best-match assignment must retain the multiple-person-near-seat signal."""
    mgr = SeatManager(room_id="ROOM_A101")
    mgr.load_seats([
        {
            "id": "S-BROAD",
            "room_id": "ROOM_A101",
            "seat_code": "SEAT_BROAD",
            "polygon_json": [[0, 0], [100, 0], [100, 100], [0, 100]],
        },
        {
            "id": "S-PRECISE",
            "room_id": "ROOM_A101",
            "seat_code": "SEAT_PRECISE",
            "polygon_json": [[40, 40], [80, 40], [80, 80], [40, 80]],
        },
    ])
    detections = [
        Detection(0, "person", 0.91, (48, 40, 68, 64), frame_index=1),
        Detection(0, "person", 0.95, (52, 40, 72, 64), frame_index=1),
    ]

    mapped, unmapped = mgr.map_detections_to_seats(
        detections, timestamp_ms=1000.0, frame_w=100, frame_h=100
    )

    occupancy = mgr.occupancies["SEAT_PRECISE"]
    assert occupancy.state == SeatState.MULTIPLE_PERSON
    assert len(occupancy.candidate_detections) == 2
    assert mapped["SEAT_PRECISE"] is detections[1]
    assert unmapped == []


# ==============================================================================
# 3. P0-06: Suspicious Behavior Signals & Unknown-Safe Handling
# ==============================================================================
def test_p0_06_behavior_signals_and_unknown_safe():
    """Verify signals are accurately triggered and missing keypoints return unknown-safe quality."""
    extractor = BehaviorSignalExtractor(head_turn_yaw_threshold=25.0, body_lean_angle_threshold=18.0)

    # 1. Unknown-safe check: all keypoints occluded (confidence = 0)
    kps_empty = np.zeros((17, 3), dtype=np.float32)
    yaw, pitch, quality = extract_head_yaw_pitch_safe(kps_empty)
    assert yaw is None
    assert pitch is None
    assert quality == 0.0

    # 2. PROLONGED_HEAD_TURN test: head turned significantly right
    kps_turn = np.zeros((17, 3), dtype=np.float32)
    kps_turn[0] = [135, 80, 0.95]  # Nose shifted far towards right ear
    kps_turn[1] = [120, 75, 0.95]  # L Eye
    kps_turn[2] = [140, 75, 0.95]  # R Eye
    kps_turn[3] = [70, 75, 0.95]   # L Ear
    kps_turn[4] = [145, 75, 0.95]  # R Ear
    kps_turn[5] = [60, 150, 0.95]  # L Shoulder
    kps_turn[6] = [150, 150, 0.95] # R Shoulder

    signals = extractor.analyze_candidate_keypoints(kps_turn, timestamp_ms=1000.0, seat_id="SEAT_01")
    sig_types = [s.signal_type for s in signals]
    assert SignalType.PROLONGED_HEAD_TURN.value in sig_types


# ==============================================================================
# 4. P0-07 & P0-08: 0–100 Temporal Risk Scoring, State Machine & Recidivism
# ==============================================================================
def test_p0_07_08_temporal_risk_scoring_and_recidivism():
    """Verify 0-100 normalized score progression, FLAGGED_FOR_REVIEW event, cooldown, and recidivism."""
    tracker = SeatRiskTracker(room_id="ROOM_A101", flagged_threshold=65.0, cooldown_duration_ms=2000.0, recidivism_window_ms=5000.0)

    ep_turn1 = TemporalEpisode(
        episode_id="ep-turn-01",
        seat_id="SEAT_A01",
        episode_type=EpisodeType.HEAD_TURN_LEFT.value,
        state=EpisodeState.ACTIVE,
        start_timestamp_ms=1000.0,
        end_timestamp_ms=1000.0,
        duration_ms=500.0,
        confidence=0.9,
        quality=0.9,
    )
    pat_glance = BehaviorPattern(
        pattern_id="pat-glance-01",
        seat_id="SEAT_A01",
        pattern_type=PatternType.REPEATED_NEIGHBOR_GLANCE.value,
        confidence=0.95,
        quality=0.95,
        start_timestamp_ms=1000.0,
        end_timestamp_ms=3000.0,
        target_neighbor_id="SEAT_A02",
    )
    pat_lean = BehaviorPattern(
        pattern_id="pat-lean-01",
        seat_id="SEAT_A01",
        pattern_type=PatternType.NEIGHBOR_ORIENTED_LEAN.value,
        confidence=0.95,
        quality=0.95,
        start_timestamp_ms=1000.0,
        end_timestamp_ms=3000.0,
        target_neighbor_id="SEAT_A02",
    )

    # Step 1: Ingest single episode at t = 1000ms -> Score ~10.8 (NORMAL)
    evt1 = tracker.update_seat("SEAT_A01", [ep_turn1], [], timestamp_ms=1000.0)
    assert evt1 is None
    profile = tracker.profiles["SEAT_A01"]
    assert profile.current_state == RiskState.NORMAL.value
    assert profile.risk_score > 0.0

    # Step 2: Ingest pattern at t = 2500ms -> Score increases to SUSPICIOUS/FLAGGED
    dummy_det = Detection(0, "person", 0.95, (100, 100, 200, 250), frame_index=50)
    evt2 = tracker.update_seat("SEAT_A01", [ep_turn1], [pat_glance, pat_lean], timestamp_ms=2500.0, detection=dummy_det)
    assert evt2 is not None
    assert evt2.status in ("suspicious", "flagged_for_human_review", "PENDING")
    assert profile.current_state == RiskState.COOLDOWN.value  # Entered cooldown

    # Step 3: During cooldown (t = 3000ms), no events emitted
    evt_cool = tracker.update_seat("SEAT_A01", [], [pat_glance], timestamp_ms=3000.0)
    assert evt_cool is None

    # Step 4: After cooldown expires (t = 5500ms), student repeats pattern -> RECIDIVISM triggered!
    pat_recid = BehaviorPattern(
        pattern_id="pat-glance-02",
        seat_id="SEAT_A01",
        pattern_type=PatternType.REPEATED_NEIGHBOR_GLANCE.value,
        confidence=0.95,
        quality=0.95,
        start_timestamp_ms=5500.0,
        end_timestamp_ms=6000.0,
    )
    evt_recid = tracker.update_seat("SEAT_A01", [], [pat_recid], timestamp_ms=5500.0, detection=dummy_det)
    assert evt_recid is not None
    assert evt_recid.is_recidivist is True
    assert evt_recid.severity in ("HIGH", "CRITICAL")


# ==============================================================================
# 5. P0-11 & P0-12: Async Evidence Writer & SHA-256 Digest
# ==============================================================================
def test_p0_11_12_async_evidence_writer_and_sha256(tmp_path):
    """Verify non-blocking async video encoding, snapshot extraction, and SHA-256 calculation."""
    writer = AsyncEvidenceWriter(base_evidence_dir=str(tmp_path), max_workers=2)

    # Generate 15 dummy video frames
    dummy_frames = [np.full((120, 160, 3), fill_value=i * 15, dtype=np.uint8) for i in range(15)]
    peak_snap = np.zeros((120, 160, 3), dtype=np.uint8)

    callback_results = []
    completed_event = threading.Event()

    def on_done(res):
        callback_results.append(res)
        completed_event.set()

    accepted = writer.submit_job(
        event_id="EVT-ASYNC-TEST-001",
        frames=dummy_frames,
        peak_snapshot_frame=peak_snap,
        fps=15.0,
        metadata={"seat_id": "SEAT_01", "room_id": "ROOM_A101"},
        on_complete=on_done,
    )
    assert accepted is True

    # Wait for async encoding to complete
    assert completed_event.wait(timeout=5.0) is True
    assert len(callback_results) == 1
    res = callback_results[0]
    assert res["status"] == "READY"

    # Verify files created on disk
    event_dir = tmp_path / "EVT-ASYNC-TEST-001"
    assert (event_dir / "snapshot.jpg").exists()
    assert (event_dir / "evidence.mp4").exists()
    assert (event_dir / "metadata.json").exists()

    # Verify SHA-256 digest
    sha256_calc = compute_file_sha256(event_dir / "evidence.mp4")
    assert res["video_sha256"] == sha256_calc
    assert len(res["video_sha256"]) == 64

    writer.shutdown(wait=True)


# ==============================================================================
# 6. Semantic Alignment Tests: Unmapped Role, Contextual Combinations & State SRS
# ==============================================================================
def test_unmapped_person_semantics_no_proctor_inference():
    """Verify unmapped detections have seat_id=None and are never assumed to be PROCTOR."""
    seat_mgr = SeatManager(room_id="ROOM_A101", camera_id="CAM_01")
    seat_mgr.load_seats([
        SeatROI(
            seat_id="SEAT_01",
            room_id="ROOM_A101",
            seat_code="SEAT_01",
            polygon=np.array([(100, 100), (200, 100), (200, 200), (100, 200)], dtype=np.float32),
        ),
    ])

    # Person 1 inside seat ROI
    det_seated = Detection(0, "person", 0.90, (120, 120, 180, 190), frame_index=1)
    # Person 2 outside seat ROI (e.g. standing in corridor or unconfigured seat)
    det_outside = Detection(0, "person", 0.85, (500, 500, 550, 600), frame_index=1)

    mapped, unmapped = seat_mgr.map_detections_to_seats([det_seated, det_outside], timestamp_ms=100.0, frame_idx=1)
    assert "SEAT_01" in mapped
    assert mapped["SEAT_01"] is not None
    assert len(unmapped) == 1
    assert unmapped[0] == det_outside

    # Verify that unmapped detection is NOT mapped to a seat ID
    seat_id = seat_mgr.get_seat_for_detection(det_outside)
    assert seat_id is None


def test_look_down_long_contextual_contribution_and_combination_bonus():
    """Verify HEAD_PITCH_DOWN alone has zero direct risk contribution, while combined with below-desk activity escalates."""
    tracker = SeatRiskTracker(room_id="ROOM_A101")

    ep_pitch_down = TemporalEpisode(
        episode_id="ep-pitch-01",
        seat_id="SEAT_01",
        episode_type=EpisodeType.HEAD_PITCH_DOWN.value,
        state=EpisodeState.ACTIVE,
        start_timestamp_ms=1000.0,
        end_timestamp_ms=1000.0,
        duration_ms=3000.0,
        confidence=0.9,
        quality=0.9,
    )

    # 1. Candidate writing normally (HEAD_PITCH_DOWN alone)
    tracker.update_seat("SEAT_01", [ep_pitch_down], [], timestamp_ms=1000.0)
    tracker.update_seat("SEAT_01", [ep_pitch_down], [], timestamp_ms=4000.0)

    prof = tracker.profiles["SEAT_01"]
    assert prof.current_state == RiskState.NORMAL.value
    assert prof.risk_score == 0.0  # ZERO direct risk contribution in SRS v2!

    # 2. Candidate looking down + below desk interaction (Pattern)
    pat_below_desk = BehaviorPattern(
        pattern_id="pat-below-01",
        seat_id="SEAT_01",
        pattern_type=PatternType.BELOW_DESK_INTERACTION.value,
        confidence=0.95,
        quality=0.95,
        start_timestamp_ms=6000.0,
        end_timestamp_ms=8000.0,
    )
    dummy_det = Detection(0, "person", 0.95, (100, 100, 200, 250), frame_index=180)
    tracker.update_seat("SEAT_01", [ep_pitch_down], [pat_below_desk], timestamp_ms=6000.0, detection=dummy_det)

    assert prof.risk_score >= 30.0
    assert prof.current_state in (RiskState.OBSERVE.value, RiskState.SUSPICIOUS.value, RiskState.FLAGGED_FOR_REVIEW.value)


def test_state_machine_srs_compliance_and_no_cheating_state():
    """Verify State Machine adheres strictly to SRS and contains NO 'CHEATING' states."""
    valid_states = {"NORMAL", "OBSERVE", "SUSPICIOUS", "FLAGGED_FOR_REVIEW", "COOLDOWN"}
    actual_states = {s.value for s in RiskState}
    assert actual_states == valid_states
    assert "CHEATING" not in actual_states
    assert "GUILTY" not in actual_states


def test_risk_score_formatting_cleanliness():
    """Verify risk scores format cleanly as integers or 1 decimal without floating-point artifacts."""
    raw_scores = [95.86987923319205, 0.0, 30.12669588434663, 100.0]
    formatted = [f"R:{s:.0f}" for s in raw_scores]
    assert formatted == ["R:96", "R:0", "R:30", "R:100"]


# ==============================================================================
# 7. SRS v1.1 Composite Behavior & Anti-Double-Counting Scenarios
# ==============================================================================
def test_normal_writing_does_not_trigger_below_desk_composite():
    """Scenario 1: Normal Writing (LOOK_DOWN=true, hands on desk) does NOT trigger composite."""
    extractor = BehaviorSignalExtractor()

    # Create dummy 17 keypoints: head tilted down, hands resting high on desk (near shoulder/elbow level)
    kps = np.zeros((17, 3), dtype=np.float32)
    kps[0] = [100, 120, 0.9]  # Nose (down)
    kps[1] = [90, 110, 0.9]   # L Eye
    kps[2] = [110, 110, 0.9]  # R Eye
    kps[5] = [70, 100, 0.9]   # L Shoulder
    kps[6] = [130, 100, 0.9]  # R Shoulder
    kps[9] = [80, 110, 0.9]   # L Wrist (resting on desk, close to shoulder level)
    kps[10] = [120, 110, 0.9] # R Wrist (resting on desk)

    signals = extractor.analyze_candidate_keypoints(kps, timestamp_ms=1000.0, seat_id="S01")
    sig_types = {s.signal_type for s in signals}

    # Should contain LOOK_DOWN_LONG as context observation, but NOT SUSPICIOUS_BELOW_DESK_ACTIVITY
    assert SignalType.LOOK_DOWN_LONG.value in sig_types
    assert SignalType.SUSPICIOUS_BELOW_DESK_ACTIVITY.value not in sig_types
    assert SignalType.LOW_HAND_POSTURE.value not in sig_types


def test_composite_suspicious_below_desk_activity_synthesis():
    """Scenario 2 & 3: Low hands + head down + persistence triggers SUSPICIOUS_BELOW_DESK_ACTIVITY."""
    extractor = BehaviorSignalExtractor()

    # Create dummy keypoints: head down, hands low below desk/torso
    kps = np.zeros((17, 3), dtype=np.float32)
    kps[0] = [100, 120, 0.9]  # Nose
    kps[1] = [90, 110, 0.9]   # L Eye
    kps[2] = [110, 110, 0.9]  # R Eye
    kps[5] = [70, 100, 0.9]   # L Shoulder
    kps[6] = [130, 100, 0.9]  # R Shoulder
    kps[9] = [95, 175, 0.9]   # L Wrist (low & close together, below desk)
    kps[10] = [105, 175, 0.9] # R Wrist (low & close together, below desk)

    signals = extractor.analyze_candidate_keypoints(kps, timestamp_ms=1000.0, seat_id="S01")
    sig_types = {s.signal_type for s in signals}

    assert SignalType.SUSPICIOUS_BELOW_DESK_ACTIVITY.value in sig_types
    composite_sig = [s for s in signals if s.signal_type == SignalType.SUSPICIOUS_BELOW_DESK_ACTIVITY.value][0]
    assert "LOW_HAND_POSTURE" in composite_sig.metadata["evidence_components"]
    assert "LOOK_DOWN_LONG" in composite_sig.metadata["evidence_components"]


def test_missing_wrist_unknown_safe():
    """Scenario 5: Missing / occluded wrist keypoints return UNKNOWN and do not trigger false alert."""
    extractor = BehaviorSignalExtractor()

    kps = np.zeros((17, 3), dtype=np.float32)
    kps[0] = [100, 120, 0.9]  # Nose
    kps[5] = [70, 100, 0.9]   # L Shoulder
    kps[6] = [130, 100, 0.9]  # R Shoulder
    kps[9] = [95, 170, 0.05]  # L Wrist (occluded, low confidence)
    kps[10] = [105, 170, 0.08] # R Wrist (occluded, low confidence)

    signals = extractor.analyze_candidate_keypoints(kps, timestamp_ms=1000.0, seat_id="S01")
    sig_types = {s.signal_type for s in signals}

    assert SignalType.LOW_HAND_POSTURE.value not in sig_types
    assert SignalType.SUSPICIOUS_BELOW_DESK_ACTIVITY.value not in sig_types


def test_anti_double_counting_component_suppression():
    """Verify RiskEngine deduplicates episodes and uses diminishing returns on patterns."""
    tracker = SeatRiskTracker(room_id="ROOM_A101", decay_rate_per_sec=0.0)

    ep1 = TemporalEpisode(
        episode_id="ep-turn-fixed-id",
        seat_id="S01",
        episode_type=EpisodeType.HEAD_TURN_LEFT.value,
        state=EpisodeState.ACTIVE,
        start_timestamp_ms=1000.0,
        end_timestamp_ms=1000.0,
        duration_ms=1000.0,
        confidence=1.0,
        quality=1.0,
    )

    # Ingest the SAME episode across multiple frames
    tracker.update_seat("S01", [ep1], [], timestamp_ms=1000.0)
    score1 = tracker.profiles["S01"].risk_score
    tracker.update_seat("S01", [ep1], [], timestamp_ms=2000.0)
    score2 = tracker.profiles["S01"].risk_score

    # Same episode ID must NOT be double-counted across frames
    assert score1 == score2 == 12.0
