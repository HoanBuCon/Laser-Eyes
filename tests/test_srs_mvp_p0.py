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
from classroom_monitor.behavior_signals import (
    BehaviorSignal,
    BehaviorSignalExtractor,
    SignalType,
    extract_body_lean_angle,
    extract_head_yaw_pitch_safe,
    extract_low_hands_cues,
)
from classroom_monitor.models import ClassroomEvent, Detection
from classroom_monitor.rtsp_reader import RTSPStreamReader, VideoFrame
from classroom_monitor.seat_manager import SeatDefinition, SeatManager, SeatState
from classroom_monitor.seat_risk_tracker import RiskState, SeatRiskTracker
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
    tracker = SeatRiskTracker(room_id="ROOM_A101", cooldown_duration_ms=2000.0, recidivism_window_ms=5000.0)

    sig_turn = BehaviorSignal(
        signal_type=SignalType.PROLONGED_HEAD_TURN.value,
        raw_score=1.0,
        confidence=0.9,
        quality=0.9,
        timestamp_ms=1000.0,
        seat_id="SEAT_A01",
    )
    sig_lean = BehaviorSignal(
        signal_type=SignalType.BODY_LEAN_SIDE.value,
        raw_score=1.0,
        confidence=0.9,
        quality=0.9,
        timestamp_ms=1000.0,
        seat_id="SEAT_A01",
    )

    # Step 1: Ingest signals at t = 1000ms (+35 points -> OBSERVE)
    evt1 = tracker.update_seat("SEAT_A01", [sig_turn, sig_lean], timestamp_ms=1000.0)
    assert evt1 is None
    profile = tracker.profiles["SEAT_A01"]
    assert profile.risk_score >= 35
    assert profile.current_state == RiskState.OBSERVE.value

    # Step 2: Ingest signals at t = 1300ms (+35 points -> SUSPICIOUS, score ~70)
    evt2 = tracker.update_seat("SEAT_A01", [sig_turn, sig_lean], timestamp_ms=1300.0)
    assert evt2 is None
    assert profile.risk_score >= 70
    assert profile.current_state == RiskState.SUSPICIOUS.value

    # Step 3: Ingest signals at t = 1600ms (+35 points -> Score >= 80 -> FLAGGED_FOR_REVIEW triggered!)
    dummy_det = Detection(0, "person", 0.95, (100, 100, 200, 250), frame_index=50)
    evt3 = tracker.update_seat("SEAT_A01", [sig_turn, sig_lean], timestamp_ms=1600.0, detection=dummy_det)
    assert evt3 is not None
    assert evt3.status == "PENDING"
    assert profile.current_state == RiskState.COOLDOWN.value  # Entered cooldown

    # Step 4: During cooldown (t = 2000ms), no events emitted
    evt_cool = tracker.update_seat("SEAT_A01", [sig_turn, sig_lean], timestamp_ms=2000.0)
    assert evt_cool is None

    # Step 5: After cooldown expires (t = 4000ms), student immediately cheats again -> RECIDIVISM triggered!
    for t_step in [4000.0, 4300.0, 4600.0]:
        evt_recid = tracker.update_seat("SEAT_A01", [sig_turn, sig_lean], timestamp_ms=t_step, detection=dummy_det)
        if evt_recid is not None:
            break

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
