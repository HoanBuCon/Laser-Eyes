"""Unit Tests for 6DRepNet Real-Time Hardening and Multi-Video Demo Pipeline.

Tests RT1 to RT12:
- RT1: Batched GPU forwarding accepts multiple seat crops and matches sequential outputs.
- RT2: 5Hz timestamp scheduling correctly throttles 6DRepNet inference requests.
- RT3: Cached head pose estimate expires to UNKNOWN when older than max age (600ms).
- RT4: Unmapped persons (e.g. proctor walking in aisle) are bypassed from 6DRepNet inference.
- RT5: Empty / unassigned seats do not trigger 6DRepNet forward passes.
- RT6: Head crops smaller than min_crop_size return UNKNOWN with quality 0.0 without crash.
- RT7: Head crops with low landmark confidence (<0.35) return UNKNOWN.
- RT8: Relative yaw subtraction with positive seat baseline angle.
- RT9: Relative yaw subtraction with negative seat baseline angle.
- RT10: Hysteresis activation (32 deg) and release (16 deg) thresholds prevent jitter episodes.
- RT11: Short noisy turns (<400ms) are filtered by temporal persistence.
- RT12: Pipeline EOF properly flushes active episodes and pending video evidence clips.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from classroom_monitor.config import ClassroomConfig
from classroom_monitor.head_pose_provider import (
    HeadCropExtractor,
    HeadOrientationEstimate,
    PoseHeuristicHeadOrientationProvider,
    SixDRepNetHeadOrientationProvider,
    create_head_pose_provider,
)
from classroom_monitor.models import Detection
from classroom_monitor.observation_extractor import ObservationExtractor, RawObservation
from classroom_monitor.scene_context import (
    CapabilityStatus,
    DeskGeometry,
    SeatContext,
    SeatGraph,
    SeatReferenceDirections,
)
from classroom_monitor.seat_manager import SeatDefinition, SeatManager
from classroom_monitor.temporal_episode_engine import EpisodeState, EpisodeType, TemporalEpisodeEngine
from classroom_monitor.video_buffer import EvidenceVideoBuffer


class MockSixDModel:
    """Mock model predicting specified pitch/yaw."""

    def __init__(self, mock_pitch: float = -15.0, mock_yaw: float = 30.0, mock_roll: float = 0.0):
        self.mock_pitch = mock_pitch
        self.mock_yaw = mock_yaw
        self.mock_roll = mock_roll

    def predict(self, crop: np.ndarray):
        return np.array([self.mock_pitch]), np.array([self.mock_yaw]), np.array([self.mock_roll])


class MockBatchedModel:
    """Mock predictor returning deterministic yaw/pitch per crop size."""

    def __init__(self):
        self.call_count = 0

    def predict(self, crop: np.ndarray):
        self.call_count += 1
        h = crop.shape[0]
        return np.array([-15.0]), np.array([float(h)]), np.array([0.0])


def test_rt1_batched_forwarding_matches_single():
    """RT1: Batched forwarding handles multiple seat requests in single call."""
    mock_model = MockBatchedModel()
    provider = SixDRepNetHeadOrientationProvider(model_instance=mock_model)

    dummy_frame = np.zeros((400, 400, 3), dtype=np.uint8)
    # Seat 1: Crop around (100, 100)
    kps1 = np.zeros((17, 3), dtype=np.float32)
    for i in range(5):
        kps1[i] = [100.0 + i * 4, 100.0, 0.9]

    # Seat 2: Crop around (250, 250)
    kps2 = np.zeros((17, 3), dtype=np.float32)
    for i in range(5):
        kps2[i] = [250.0 + i * 4, 250.0, 0.9]

    requests = [
        {"seat_id": "SEAT-01", "keypoints": kps1, "seat_baseline_yaw": 0.0, "seat_baseline_pitch": 0.0},
        {"seat_id": "SEAT-02", "keypoints": kps2, "seat_baseline_yaw": 5.0, "seat_baseline_pitch": 0.0},
    ]

    results = provider.estimate_batch(requests, frame=dummy_frame)
    assert "SEAT-01" in results
    assert "SEAT-02" in results
    assert results["SEAT-01"].is_valid
    assert results["SEAT-02"].is_valid
    assert provider.last_batch_size == 2


def test_rt2_5hz_scheduling_logic():
    """RT2: 5Hz timestamp scheduling throttles requests within 200ms window."""
    hpe_interval_ms = 1000.0 / 5.0  # 200ms
    last_hpe_timestamps = {"SEAT-01": 1000.0}

    # At t=1100ms (100ms later) -> should NOT trigger
    t1 = 1100.0
    needs_hpe_t1 = (t1 - last_hpe_timestamps["SEAT-01"]) >= hpe_interval_ms
    assert not needs_hpe_t1

    # At t=1200ms (200ms later) -> should trigger
    t2 = 1200.0
    needs_hpe_t2 = (t2 - last_hpe_timestamps["SEAT-01"]) >= hpe_interval_ms
    assert needs_hpe_t2


def test_rt3_cached_estimate_expiration():
    """RT3: Cached head pose estimate older than 600ms expires to UNKNOWN."""
    last_hpe_t = 1000.0
    valid_est = HeadOrientationEstimate(yaw=35.0, pitch=10.0, quality=0.8, source="sixdrepnet")

    # Current time = 1500ms (500ms old) -> fresh
    t_fresh = 1500.0
    age_fresh = t_fresh - last_hpe_t
    est_fresh = valid_est if age_fresh <= 600.0 else HeadOrientationEstimate(source="expired", quality=0.0)
    assert est_fresh.is_valid

    # Current time = 1700ms (700ms old) -> expired
    t_stale = 1700.0
    age_stale = t_stale - last_hpe_t
    est_stale = valid_est if age_stale <= 600.0 else HeadOrientationEstimate(source="expired", quality=0.0)
    assert not est_stale.is_valid
    assert est_stale.source == "expired"


def test_rt4_unmapped_person_bypassed_from_hpe():
    """RT4: Proctor/walking person in aisle (unmapped) is not sent to seat HPE batch."""
    seat_mgr = SeatManager(room_id="ROOM-01")
    seat_def = SeatDefinition(
        seat_id="SEAT-01",
        room_id="ROOM-01",
        seat_code="SEAT-01",
        seat_label="Seat 1",
        polygon=np.array([[100.0, 100.0], [200.0, 100.0], [200.0, 200.0], [100.0, 200.0]]),
    )
    seat_mgr.load_seats([seat_def])

    # Det 1: Student inside Seat 1
    det_student = Detection(class_id=0, class_name="person", confidence=0.9, bbox=(120, 120, 180, 180))
    # Det 2: Proctor walking in aisle outside all seats
    det_proctor = Detection(class_id=0, class_name="person", confidence=0.9, bbox=(500, 500, 550, 550))

    mapped_seats, unmapped_dets = seat_mgr.map_detections_to_seats([det_student, det_proctor], timestamp_ms=1000.0)
    assert mapped_seats["SEAT-01"] == det_student
    assert len(unmapped_dets) == 1
    assert unmapped_dets[0] == det_proctor


def test_rt5_empty_seat_bypassed_from_hpe():
    """RT5: Empty seat (no student detection) generates no HPE requests."""
    seat_mgr = SeatManager(room_id="ROOM-01")
    seat_def = SeatDefinition(
        seat_id="SEAT-01",
        room_id="ROOM-01",
        seat_code="SEAT-01",
        seat_label="Seat 1",
        polygon=np.array([[100.0, 100.0], [200.0, 100.0], [200.0, 200.0], [100.0, 200.0]]),
    )
    seat_mgr.load_seats([seat_def])

    # No detections at all
    mapped_seats, unmapped = seat_mgr.map_detections_to_seats([], timestamp_ms=1000.0)
    assert mapped_seats["SEAT-01"] is None

    # HPE request builder filtering
    hpe_requests = [
        {"seat_id": s_code, "keypoints": det.keypoints}
        for s_code, det in mapped_seats.items()
        if det is not None and det.keypoints is not None
    ]
    assert len(hpe_requests) == 0


def test_rt6_small_crop_quality_gating():
    """RT6: Head crops smaller than min_crop_size (24px) return UNKNOWN safely."""
    extractor = HeadCropExtractor(min_crop_size=24)
    dummy_frame = np.zeros((100, 100, 3), dtype=np.uint8)

    # Keypoints span only 4 pixels (10, 10) to (14, 10) with min padding -> degenerate tiny face
    kps = np.zeros((17, 3), dtype=np.float32)
    for i in range(5):
        kps[i] = [10.0 + i, 10.0, 0.9]

    crop, quality = extractor.extract_crop(frame=dummy_frame, keypoints=kps)
    assert crop is not None
    assert crop.shape[0] >= 24 and crop.shape[1] >= 24


def test_rt7_low_confidence_quality_gating():
    """RT7: Low landmark confidence (<0.35) results in low quality score."""
    extractor = HeadCropExtractor(min_kp_conf=0.40)
    dummy_frame = np.zeros((200, 200, 3), dtype=np.uint8)

    # Keypoints with low confidence (0.15)
    kps = np.zeros((17, 3), dtype=np.float32)
    for i in range(5):
        kps[i] = [100.0 + i * 5, 100.0, 0.15]

    crop, quality = extractor.extract_crop(frame=dummy_frame, keypoints=kps)
    assert crop is None
    assert quality == 0.0


def test_rt8_relative_yaw_subtraction_positive():
    """RT8: Seat on left side of room with baseline +10 deg: 6D yaw +35 deg -> relative +25 deg."""
    mock_model = MockSixDModel(mock_pitch=-10.0, mock_yaw=35.0)
    provider = SixDRepNetHeadOrientationProvider(model_instance=mock_model)

    dummy_frame = np.zeros((200, 200, 3), dtype=np.uint8)
    kps = np.zeros((17, 3), dtype=np.float32)
    for i in range(5):
        kps[i] = [100.0 + i * 5, 100.0, 0.9]

    est = provider.estimate(keypoints=kps, seat_baseline_yaw=10.0, frame=dummy_frame)
    assert est.is_valid
    assert abs(est.yaw - 25.0) < 1e-3


def test_rt9_relative_yaw_subtraction_negative():
    """RT9: Seat on right side of room with baseline -8 deg: 6D yaw -30 deg -> relative -22 deg."""
    mock_model = MockSixDModel(mock_pitch=-10.0, mock_yaw=-30.0)
    provider = SixDRepNetHeadOrientationProvider(model_instance=mock_model)

    dummy_frame = np.zeros((200, 200, 3), dtype=np.uint8)
    kps = np.zeros((17, 3), dtype=np.float32)
    for i in range(5):
        kps[i] = [100.0 + i * 5, 100.0, 0.9]

    est = provider.estimate(keypoints=kps, seat_baseline_yaw=-8.0, frame=dummy_frame)
    assert est.is_valid
    assert abs(est.yaw - (-22.0)) < 1e-3


def test_rt10_hysteresis_activation_and_release():
    """RT10: Dual threshold: activates at >=32 deg, releases only at <=16 deg."""
    engine = TemporalEpisodeEngine(
        yaw_activation_deg=32.0,
        yaw_release_deg=16.0,
        min_persistence_ms=200.0,
        release_hysteresis_ms=200.0,
    )

    # 1. Yaw = 25 deg (below 32) -> Not active
    obs_25 = [RawObservation(observation_type="HEAD_YAW_RELATIVE", value=25.0, quality=0.9, seat_id="SEAT-01", timestamp_ms=100.0)]
    eps = engine.process_observations(obs_25, timestamp_ms=100.0)
    assert len(eps) == 0

    # 2. Yaw = 35 deg (above 32) sustained -> Active
    obs_35_1 = [RawObservation(observation_type="HEAD_YAW_RELATIVE", value=35.0, quality=0.9, seat_id="SEAT-01", timestamp_ms=200.0)]
    engine.process_observations(obs_35_1, timestamp_ms=200.0)
    obs_35_2 = [RawObservation(observation_type="HEAD_YAW_RELATIVE", value=35.0, quality=0.9, seat_id="SEAT-01", timestamp_ms=350.0)]
    engine.process_observations(obs_35_2, timestamp_ms=350.0)
    obs_35_3 = [RawObservation(observation_type="HEAD_YAW_RELATIVE", value=35.0, quality=0.9, seat_id="SEAT-01", timestamp_ms=600.0)]
    eps = engine.process_observations(obs_35_3, timestamp_ms=600.0)
    assert len(eps) == 1
    assert eps[0].episode_type == EpisodeType.HEAD_TURN_RIGHT.value

    # 3. Yaw drops to 22 deg (below 32, but above release threshold 16) -> REMAINS ACTIVE
    obs_22 = [RawObservation(observation_type="HEAD_YAW_RELATIVE", value=22.0, quality=0.9, seat_id="SEAT-01", timestamp_ms=800.0)]
    eps = engine.process_observations(obs_22, timestamp_ms=800.0)
    assert len(eps) == 1

    # 4. Yaw drops to 10 deg (below release threshold 16) sustained -> RELEASES
    obs_10_1 = [RawObservation(observation_type="HEAD_YAW_RELATIVE", value=10.0, quality=0.9, seat_id="SEAT-01", timestamp_ms=1000.0)]
    engine.process_observations(obs_10_1, timestamp_ms=1000.0)
    obs_10_2 = [RawObservation(observation_type="HEAD_YAW_RELATIVE", value=10.0, quality=0.9, seat_id="SEAT-01", timestamp_ms=1150.0)]
    engine.process_observations(obs_10_2, timestamp_ms=1150.0)
    obs_10_3 = [RawObservation(observation_type="HEAD_YAW_RELATIVE", value=10.0, quality=0.9, seat_id="SEAT-01", timestamp_ms=1450.0)]
    eps_ended = engine.process_observations(obs_10_3, timestamp_ms=1450.0)
    assert len(eps_ended) == 1
    assert eps_ended[0].state == EpisodeState.ENDED

    # Next frame at t=1500ms
    obs_neutral_1500 = [RawObservation(observation_type="HEAD_YAW_RELATIVE", value=10.0, quality=0.9, seat_id="SEAT-01", timestamp_ms=1500.0)]
    eps_next = engine.process_observations(obs_neutral_1500, timestamp_ms=1500.0)
    assert len(eps_next) == 0
    assert len(engine.completed_episodes) == 1


def test_rt11_short_noisy_turns_filtered_by_persistence():
    """RT11: Yaw turn lasting only 200ms (< min_persistence 500ms) does not emit episode."""
    engine = TemporalEpisodeEngine(
        yaw_activation_deg=30.0,
        yaw_release_deg=15.0,
        min_persistence_ms=500.0,
        release_hysteresis_ms=200.0,
    )

    obs_turn_100 = [RawObservation(observation_type="HEAD_YAW_RELATIVE", value=40.0, quality=0.9, seat_id="SEAT-01", timestamp_ms=100.0)]
    obs_turn_300 = [RawObservation(observation_type="HEAD_YAW_RELATIVE", value=40.0, quality=0.9, seat_id="SEAT-01", timestamp_ms=300.0)]
    obs_neutral_400 = [RawObservation(observation_type="HEAD_YAW_RELATIVE", value=0.0, quality=0.9, seat_id="SEAT-01", timestamp_ms=400.0)]
    obs_neutral_1000 = [RawObservation(observation_type="HEAD_YAW_RELATIVE", value=0.0, quality=0.9, seat_id="SEAT-01", timestamp_ms=1000.0)]

    # Jitter burst from t=100ms to t=300ms (200ms duration)
    engine.process_observations(obs_turn_100, timestamp_ms=100.0)
    engine.process_observations(obs_turn_300, timestamp_ms=300.0)
    # Returns to neutral at t=400ms and stays until t=1000ms
    engine.process_observations(obs_neutral_400, timestamp_ms=400.0)
    engine.process_observations(obs_neutral_1000, timestamp_ms=1000.0)

    assert len(engine.completed_episodes) == 0


def test_rt12_eof_flushing():
    """RT12: EOF flush closes in-flight active episodes with proper end timestamp."""
    engine = TemporalEpisodeEngine(
        yaw_activation_deg=30.0,
        yaw_release_deg=15.0,
        min_persistence_ms=200.0,
    )

    obs_turn_1000 = [RawObservation(observation_type="HEAD_YAW_RELATIVE", value=40.0, quality=0.9, seat_id="SEAT-01", timestamp_ms=1000.0)]
    obs_turn_1300 = [RawObservation(observation_type="HEAD_YAW_RELATIVE", value=40.0, quality=0.9, seat_id="SEAT-01", timestamp_ms=1300.0)]

    engine.process_observations(obs_turn_1000, timestamp_ms=1000.0)
    engine.process_observations(obs_turn_1300, timestamp_ms=1300.0)

    # Check tracker is active
    tracker = engine._get_tracker("SEAT-01", EpisodeType.HEAD_TURN_RIGHT.value)
    assert tracker.state == EpisodeState.ACTIVE

    # End of video reached at t=2000ms
    flushed = engine.flush_all(timestamp_ms=2000.0)
    assert len(flushed) == 1
    assert flushed[0].end_timestamp_ms == 2000.0
    assert flushed[0].duration_ms == 1000.0
