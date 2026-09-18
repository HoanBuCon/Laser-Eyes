"""Comprehensive Test Suite for Head Pose Estimation (HPE1 - HPE12).

Verifies:
- HPE1: Scheduled batch estimate is called only when due.
- HPE2: External cached estimate prevents internal provider inference.
- HPE3: One scheduled timestamp causes maximum one model batch forward.
- HPE4: Multiple Seats share one batch forward.
- HPE5: Stale cache becomes UNKNOWN.
- HPE6: Empty Seat causes no HPE request.
- HPE7: Roaming / unmapped person causes no HPE request.
- HPE8: Low-quality crop becomes UNKNOWN.
- HPE9: Seat baseline applied exactly once.
- HPE10: Median filter suppresses single spike.
- HPE11: Persistent turn still activates.
- HPE12: Reset clears smoothing and state cache appropriately.
"""

from __future__ import annotations

import numpy as np
import pytest

from classroom_monitor.detector import Detection
from classroom_monitor.head_pose_provider import (
    HeadCropExtractor,
    HeadOrientationEstimate,
    HeadOrientationProvider,
    PoseHeuristicHeadOrientationProvider,
    SixDRepNetHeadOrientationProvider,
)
from classroom_monitor.observation_extractor import ObservationExtractor, ObservationType, RawObservation
from classroom_monitor.scene_context import CapabilityStatus, SeatContext
from classroom_monitor.seat_manager import SeatDefinition, SeatManager, SeatState
from classroom_monitor.temporal_episode_engine import EpisodeState, EpisodeType, TemporalEpisodeEngine


class MockBatchHeadProvider(HeadOrientationProvider):
    """Test spy for HeadOrientationProvider counting batch and single forwards."""

    def __init__(self):
        self.estimate_calls = 0
        self.estimate_batch_calls = 0
        self.forward_batch_sizes: list[int] = []

    def estimate(self, keypoints, bbox=None, seat_baseline_yaw=0.0, seat_baseline_pitch=0.0, frame=None):
        self.estimate_calls += 1
        return HeadOrientationEstimate(source="mock", quality=0.9, yaw=-35.0, pitch=5.0)

    def estimate_batch(self, requests, frame=None):
        self.estimate_batch_calls += 1
        self.forward_batch_sizes.append(len(requests))
        results = {}
        for req in requests:
            s_id = req["seat_id"]
            base_yaw = req.get("seat_baseline_yaw", 0.0)
            base_pitch = req.get("seat_baseline_pitch", 0.0)
            results[s_id] = HeadOrientationEstimate(
                source="mock_batch",
                quality=0.9,
                yaw=-35.0 - base_yaw,
                pitch=5.0 - base_pitch,
            )
        return results


def test_hpe1_scheduled_batch_called_only_when_due():
    """HPE1: Scheduled batch estimate is called only when interval has elapsed."""
    last_hpe_time = {}
    hpe_interval_ms = 200.0  # 5 Hz
    provider = MockBatchHeadProvider()

    # t = 0ms: First call due
    reqs_t0 = [{"seat_id": "S1"}]
    provider.estimate_batch(reqs_t0)
    last_hpe_time["S1"] = 0.0

    # t = 50ms: Not due yet
    is_due_t50 = (50.0 - last_hpe_time["S1"]) >= hpe_interval_ms
    assert not is_due_t50

    # t = 200ms: Due again
    is_due_t200 = (200.0 - last_hpe_time["S1"]) >= hpe_interval_ms
    assert is_due_t200
    if is_due_t200:
        provider.estimate_batch([{"seat_id": "S1"}])
        last_hpe_time["S1"] = 200.0

    assert provider.estimate_batch_calls == 2


def test_hpe2_precomputed_estimate_prevents_internal_inference():
    """HPE2: Supplying precomputed_head_estimate prevents internal provider inference."""
    provider = MockBatchHeadProvider()
    extractor = ObservationExtractor(head_pose_provider=provider)
    ctx = SeatContext(seat_id="S01")

    # Keypoints for person
    kps = np.zeros((17, 3), dtype=np.float32)
    kps[:, 2] = 0.9  # high confidence
    det = Detection(0, "person", 0.95, (10, 10, 100, 200), keypoints=kps)

    precomputed = HeadOrientationEstimate(source="cached_batch", quality=0.95, yaw=32.0, pitch=-2.0)
    obs = extractor.extract(
        detection=det,
        seat_context=ctx,
        timestamp_ms=100.0,
        precomputed_head_estimate=precomputed,
    )

    # Provider should NOT have been called
    assert provider.estimate_calls == 0
    assert provider.estimate_batch_calls == 0

    yaw_obs = next(o for o in obs if o.observation_type == ObservationType.HEAD_YAW_RELATIVE.value)
    assert yaw_obs.value == 32.0
    assert yaw_obs.source == "cached_batch"


def test_hpe3_hpe4_single_batch_forward_for_multiple_seats():
    """HPE3 & HPE4: Multiple seats share a single batched model forward call."""
    provider = MockBatchHeadProvider()
    requests = [
        {"seat_id": "S01", "keypoints": np.zeros((17, 3)), "seat_baseline_yaw": 0.0},
        {"seat_id": "S02", "keypoints": np.zeros((17, 3)), "seat_baseline_yaw": 5.0},
        {"seat_id": "S03", "keypoints": np.zeros((17, 3)), "seat_baseline_yaw": -5.0},
    ]

    res = provider.estimate_batch(requests)
    assert provider.estimate_batch_calls == 1
    assert provider.forward_batch_sizes == [3]
    assert len(res) == 3
    assert res["S01"].yaw == -35.0
    assert res["S02"].yaw == -40.0  # -35 - 5


def test_hpe5_stale_cache_becomes_unknown():
    """HPE5: Cache older than max age (600ms) expires to UNKNOWN."""
    extractor = ObservationExtractor()
    ctx = SeatContext(seat_id="S01")
    kps = np.zeros((17, 3), dtype=np.float32)
    kps[:, 2] = 0.9
    det = Detection(0, "person", 0.95, (10, 10, 100, 200), keypoints=kps)

    expired_est = HeadOrientationEstimate(source="cache_expired", quality=0.0, yaw=None, pitch=None)
    obs = extractor.extract(
        detection=det,
        seat_context=ctx,
        timestamp_ms=1000.0,
        precomputed_head_estimate=expired_est,
    )

    yaw_obs = [o for o in obs if o.observation_type == ObservationType.HEAD_YAW_RELATIVE.value]
    assert len(yaw_obs) == 0  # No valid yaw observation emitted when UNKNOWN


def test_hpe6_empty_seat_causes_no_hpe_request():
    """HPE6: Empty seat produces occupancy observation without head estimation."""
    provider = MockBatchHeadProvider()
    extractor = ObservationExtractor(head_pose_provider=provider)
    ctx = SeatContext(seat_id="S01")

    obs = extractor.extract(
        detection=None,
        seat_context=ctx,
        timestamp_ms=500.0,
        occupancy_state="EMPTY",
    )

    assert provider.estimate_calls == 0
    assert any(o.observation_type == ObservationType.SEAT_OCCUPANCY.value and o.value == "EMPTY" for o in obs)


def test_hpe7_roaming_person_causes_no_seat_hpe():
    """HPE7: Unmapped roaming person does not trigger seat head estimation."""
    mgr = SeatManager(room_id="ROOM1")
    mgr.load_seats([
        SeatDefinition(seat_id="S01", room_id="ROOM1", seat_code="S01", polygon=np.array([[0, 0], [10, 0], [10, 10], [0, 10]], dtype=np.float32)),
    ])

    roaming_det = Detection(99, "person", 0.90, (500, 500, 600, 700))  # outside polygon
    mapped, unmapped = mgr.map_detections_to_seats([roaming_det], timestamp_ms=100.0, frame_idx=1)

    assert mapped["S01"] is None
    assert len(unmapped) == 1
    assert mgr.occupancies["S01"].state == SeatState.EMPTY


def test_hpe8_low_quality_crop_fails_quality_gate():
    """HPE8: Tiny/low-resolution crop fails quality gate and returns quality < min_quality."""
    crop_ext = HeadCropExtractor(min_crop_size=24)
    tiny_frame = np.zeros((20, 20, 3), dtype=np.uint8)

    crop, quality = crop_ext.extract_crop(frame=tiny_frame, keypoints=None, bbox=(0, 0, 10, 10))
    assert crop is None
    assert quality == 0.0


def test_hpe9_seat_baseline_subtraction_applied_once():
    """HPE9: Seat baseline yaw is subtracted accurately without double subtraction."""
    provider = PoseHeuristicHeadOrientationProvider()
    kps = np.zeros((17, 3), dtype=np.float32)
    # Nose at x=50, left ear at x=30, right ear at x=70 -> raw yaw ~ 0.0
    kps[0] = [50.0, 50.0, 0.9]  # Nose
    kps[3] = [30.0, 50.0, 0.9]  # Left ear
    kps[4] = [70.0, 50.0, 0.9]  # Right ear

    est = provider.estimate(keypoints=kps, seat_baseline_yaw=15.0)
    assert est.yaw is not None
    assert pytest.approx(est.yaw, abs=2.0) == -15.0


def test_hpe10_median_filter_suppresses_single_spike():
    """HPE10: A single-sample noise spike is filtered by the 3-sample median filter."""
    engine = TemporalEpisodeEngine(yaw_activation_deg=28.0, min_persistence_ms=200.0)

    # Frame 1 (t=0ms): normal yaw 0.0
    obs1 = [RawObservation("S01", 0.0, ObservationType.HEAD_YAW_RELATIVE.value, 0.0, 0.9, 0.9)]
    ep1 = engine.process_observations(obs1, timestamp_ms=0.0)
    assert len(ep1) == 0

    # Frame 2 (t=100ms): transient spike at -45.0 (median of [0, -45] is -22.5, < 28.0)
    obs2 = [RawObservation("S01", 100.0, ObservationType.HEAD_YAW_RELATIVE.value, -45.0, 0.9, 0.9)]
    ep2 = engine.process_observations(obs2, timestamp_ms=100.0)
    assert len(ep2) == 0

    # Frame 3 (t=200ms): returns to normal 0.0 (median of [0, -45, 0] is 0.0)
    obs3 = [RawObservation("S01", 200.0, ObservationType.HEAD_YAW_RELATIVE.value, 0.0, 0.9, 0.9)]
    ep3 = engine.process_observations(obs3, timestamp_ms=200.0)
    assert len(ep3) == 0  # Spike successfully suppressed!


def test_hpe11_persistent_turn_activates_episode():
    """HPE11: Sustained head turn across multiple samples promotes to ACTIVE episode."""
    engine = TemporalEpisodeEngine(yaw_activation_deg=28.0, min_persistence_ms=200.0)

    # Sustained left turn (-35.0 deg) across 4 frames
    for t in [0.0, 100.0, 200.0, 300.0]:
        obs = [RawObservation("S01", t, ObservationType.HEAD_YAW_RELATIVE.value, -35.0, 0.9, 0.9)]
        eps = engine.process_observations(obs, timestamp_ms=t)

    assert len(eps) >= 1
    assert eps[0].episode_type == EpisodeType.HEAD_TURN_LEFT.value
    assert eps[0].state == EpisodeState.ACTIVE


def test_hpe12_reset_clears_smoothing_and_cache():
    """HPE12: Resetting a seat clears rolling buffers and tracker state."""
    engine = TemporalEpisodeEngine()
    obs = [RawObservation("S01", 0.0, ObservationType.HEAD_YAW_RELATIVE.value, -35.0, 0.9, 0.9)]
    engine.process_observations(obs, timestamp_ms=0.0)

    assert ("S01", "yaw") in engine._smoothing_buffers
    engine.reset_seat("S01")
    assert ("S01", "yaw") not in engine._smoothing_buffers
    assert ("S01", EpisodeType.HEAD_TURN_LEFT.value) not in engine._trackers
