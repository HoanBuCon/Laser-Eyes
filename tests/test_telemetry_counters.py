"""Unit & Integration Tests for Head Pose Provider Telemetry Counters (TEL1 - TEL5).

Verifies:
- TEL1: Telemetry counters are cleanly initialized (0 calls, 0 crops).
- TEL2: estimate_single_calls increments on estimate(), estimate_batch_calls on estimate_batch().
- TEL3: Crop validity partitioning accurately tracks valid_head_crops vs rejected_head_crops.
- TEL4: model_forward_calls increments per batch forward pass and tracks batch sizes.
- TEL5: get_telemetry() returns accurate summary statistics (average_batch_size, max_batch_size, etc.).
"""

from __future__ import annotations

import numpy as np
import pytest

from classroom_monitor.head_pose_provider import (
    HeadCropExtractor,
    HeadOrientationEstimate,
    PoseHeuristicHeadOrientationProvider,
    SixDRepNetHeadOrientationProvider,
)


class MockSixDRepModel:
    """Mock deep learning model simulating 6DRepNet forward pass."""

    def __init__(self):
        self.gpu = -1
        self.call_count = 0

    def predict(self, crop: np.ndarray):
        self.call_count += 1
        # Returns pitch, yaw, roll
        return np.array([5.0]), np.array([10.0]), np.array([0.0])


def _make_dummy_keypoints(conf: float = 0.9) -> np.ndarray:
    """Create dummy 17-keypoint array with valid head landmarks."""
    kps = np.zeros((17, 3), dtype=np.float32)
    # 0: nose, 1: left_eye, 2: right_eye, 3: left_ear, 4: right_ear
    kps[0] = [100, 100, conf]
    kps[1] = [90, 95, conf]
    kps[2] = [110, 95, conf]
    kps[3] = [80, 100, conf]
    kps[4] = [120, 100, conf]
    return kps


def test_tel1_initial_telemetry_state():
    """TEL1: Verified that fresh provider instance starts with zeroed counters."""
    provider = PoseHeuristicHeadOrientationProvider()
    tel = provider.get_telemetry()

    assert tel["estimate_single_calls"] == 0
    assert tel["estimate_batch_calls"] == 0
    assert tel["model_forward_calls"] == 0
    assert tel["total_head_crops"] == 0
    assert tel["valid_head_crops"] == 0
    assert tel["rejected_head_crops"] == 0
    assert tel["average_batch_size"] == 0.0
    assert tel["max_batch_size"] == 0


def test_tel2_single_vs_batch_call_tracking():
    """TEL2: Calling estimate() increments single_calls, estimate_batch() increments batch_calls."""
    provider = PoseHeuristicHeadOrientationProvider()
    kps = _make_dummy_keypoints(0.9)

    # 1. Single call
    res = provider.estimate(keypoints=kps)
    assert res is not None
    assert provider.total_single_calls == 1
    assert provider.total_batch_calls == 0

    # 2. Batch call
    requests = [
        {"seat_id": "S1", "keypoints": kps, "seat_baseline_yaw": 0.0, "seat_baseline_pitch": 0.0},
        {"seat_id": "S2", "keypoints": kps, "seat_baseline_yaw": 0.0, "seat_baseline_pitch": 0.0},
    ]
    batch_res = provider.estimate_batch(requests)
    assert len(batch_res) == 2
    assert provider.total_batch_calls == 1
    # estimate_batch calls estimate for each request internally in heuristic provider
    assert provider.total_single_calls == 3


def test_tel3_crop_validity_and_rejection_partitioning():
    """TEL3: Valid vs rejected head crops are partitioned based on crop quality gates."""
    mock_model = MockSixDRepModel()
    provider = SixDRepNetHeadOrientationProvider(
        min_quality=0.35,
        model_instance=mock_model,
        crop_extractor=HeadCropExtractor(min_crop_size=20),
    )

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    good_kps = _make_dummy_keypoints(0.9)
    bad_kps = _make_dummy_keypoints(0.1)  # Low confidence -> quality < 0.35

    requests = [
        {"seat_id": "S1", "keypoints": good_kps, "bbox": (50, 50, 150, 200)},
        {"seat_id": "S2", "keypoints": bad_kps, "bbox": (200, 200, 210, 210)},  # Small bbox < min_crop_size -> rejected
        {"seat_id": "S3", "keypoints": None, "bbox": None},
    ]

    results = provider.estimate_batch(requests, frame=frame)
    assert len(results) == 3

    tel = provider.get_telemetry()
    assert tel["total_head_crops"] == 3
    assert tel["valid_head_crops"] == 1
    assert tel["rejected_head_crops"] == 2


def test_tel4_model_forward_calls_and_batch_sizes():
    """TEL4: model_forward_calls increments per batch forward pass and tracks batch sizes."""
    mock_model = MockSixDRepModel()
    provider = SixDRepNetHeadOrientationProvider(
        min_quality=0.30,
        model_instance=mock_model,
        crop_extractor=HeadCropExtractor(min_crop_size=20),
    )

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    kps = _make_dummy_keypoints(0.9)

    # Batch 1: 3 valid seats
    req1 = [
        {"seat_id": f"S{i}", "keypoints": kps, "bbox": (50 + i * 20, 50, 150 + i * 20, 200)}
        for i in range(3)
    ]
    provider.estimate_batch(req1, frame=frame)

    # Batch 2: 2 valid seats
    req2 = [
        {"seat_id": f"S{i}", "keypoints": kps, "bbox": (50 + i * 20, 50, 150 + i * 20, 200)}
        for i in range(2)
    ]
    provider.estimate_batch(req2, frame=frame)

    tel = provider.get_telemetry()
    assert tel["model_forward_calls"] == 2
    assert tel["average_batch_size"] == 2.5
    assert tel["max_batch_size"] == 3


def test_tel5_get_telemetry_aggregated_structure():
    """TEL5: Verified that get_telemetry returns full dict with all expected keys and rounding."""
    provider = PoseHeuristicHeadOrientationProvider()
    kps = _make_dummy_keypoints(0.8)

    provider.estimate_batch([{"seat_id": "S1", "keypoints": kps}])
    provider.estimate_batch([{"seat_id": "S1", "keypoints": kps}, {"seat_id": "S2", "keypoints": kps}])

    tel = provider.get_telemetry()
    expected_keys = {
        "estimate_single_calls",
        "estimate_batch_calls",
        "model_forward_calls",
        "total_head_crops",
        "valid_head_crops",
        "rejected_head_crops",
        "average_batch_size",
        "max_batch_size",
        "average_forward_ms",
    }
    assert expected_keys.issubset(set(tel.keys()))
    assert isinstance(tel["average_batch_size"], float)
    assert isinstance(tel["max_batch_size"], int)
    assert isinstance(tel["average_forward_ms"], float)
