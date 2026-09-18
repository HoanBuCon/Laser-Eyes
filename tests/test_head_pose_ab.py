"""A/B Unit Tests for Head Orientation Providers (Pose Heuristic vs 6DRepNet).

Tests H1 to H12:
- H1: Existing pose provider works without frame.
- H2: SixD provider with frame=None returns UNKNOWN safely.
- H3: Invalid/empty head crop returns UNKNOWN.
- H4: Crop is clamped to frame bounds.
- H5: Canonical yaw sign: left < 0, right > 0.
- H6: Canonical pitch sign: down > 0.
- H7: Seat baseline subtraction works.
- H8: ObservationExtractor passes frame when available and works without frame.
- H9: Factory helper selects pose_heuristic.
- H10: Factory helper selects sixdrepnet.
- H11: Label-aware benchmark matching rejects opposite directional match (LEFT != RIGHT).
- H12: HeadOrientationEstimate quality semantics clamped [0.0, 1.0].
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from classroom_monitor.head_pose_provider import (
    HeadCropExtractor,
    HeadOrientationEstimate,
    HeadOrientationProvider,
    PoseHeuristicHeadOrientationProvider,
    SixDRepNetHeadOrientationProvider,
    create_head_pose_provider,
)
from classroom_monitor.models import Detection
from classroom_monitor.observation_extractor import ObservationExtractor, ObservationType
from classroom_monitor.scene_context import (
    CapabilityStatus,
    SeatCapabilities,
    SeatContext,
    SeatReferenceDirections,
)
from scripts.benchmark_temporal_ground_truth import compute_temporal_iou, normalize_label


class MockSixDModel:
    """Mock model that does not require downloading weights from the internet."""

    def __init__(self, mock_pitch: float = -15.0, mock_yaw: float = 30.0, mock_roll: float = 0.0):
        self.mock_pitch = mock_pitch
        self.mock_yaw = mock_yaw
        self.mock_roll = mock_roll

    def predict(self, crop: np.ndarray):
        return np.array([self.mock_pitch]), np.array([self.mock_yaw]), np.array([self.mock_roll])


def test_h1_existing_pose_provider_without_frame():
    """H1: Existing pose heuristic provider still works without frame."""
    provider = PoseHeuristicHeadOrientationProvider(min_kp_conf=0.30)

    # 17 COCO keypoints: nose=(100, 100), l_eye=(95, 95), r_eye=(105, 95), l_ear=(85, 95), r_ear=(115, 95), ls=(80, 150), rs=(120, 150)
    kps = np.zeros((17, 3), dtype=np.float32)
    kps[0] = [100.0, 100.0, 0.9]  # nose
    kps[1] = [95.0, 95.0, 0.9]    # l_eye
    kps[2] = [105.0, 95.0, 0.9]   # r_eye
    kps[3] = [85.0, 95.0, 0.9]    # l_ear
    kps[4] = [115.0, 95.0, 0.9]   # r_ear
    kps[5] = [80.0, 150.0, 0.9]   # ls
    kps[6] = [120.0, 150.0, 0.9]  # rs

    est = provider.estimate(keypoints=kps, frame=None)
    assert est.is_valid
    assert est.source == "pose_heuristic"
    assert est.yaw is not None
    assert est.pitch is not None
    assert est.quality > 0.5


def test_h2_sixd_provider_with_none_frame():
    """H2: SixD provider with frame=None returns UNKNOWN safely."""
    provider = SixDRepNetHeadOrientationProvider(model_instance=MockSixDModel())
    kps = np.ones((17, 3), dtype=np.float32)
    est = provider.estimate(keypoints=kps, frame=None)

    assert est.source == "sixdrepnet"
    assert est.quality == 0.0
    assert est.yaw is None
    assert not est.is_valid


def test_h3_invalid_empty_head_crop():
    """H3: Invalid/empty head crop returns UNKNOWN."""
    extractor = HeadCropExtractor(min_crop_size=16)
    dummy_frame = np.zeros((200, 200, 3), dtype=np.uint8)

    # Empty / Zero-confidence keypoints
    zero_kps = np.zeros((17, 3), dtype=np.float32)
    crop, quality = extractor.extract_crop(dummy_frame, zero_kps, bbox=None)
    assert crop is None
    assert quality == 0.0

    # Degenerate bbox
    crop2, quality2 = extractor.extract_crop(dummy_frame, None, bbox=(50, 50, 50, 50))
    assert crop2 is None
    assert quality2 == 0.0


def test_h4_crop_clamped_to_frame_bounds():
    """H4: Crop coordinates are clamped to image bounds."""
    extractor = HeadCropExtractor(min_crop_size=16, padding_ratio=0.5)
    dummy_frame = np.zeros((100, 100, 3), dtype=np.uint8)

    # Keypoints near top-left edge (x=2, y=2)
    kps = np.zeros((17, 3), dtype=np.float32)
    kps[0] = [2.0, 2.0, 0.9]
    kps[1] = [1.0, 1.0, 0.9]
    kps[2] = [10.0, 1.0, 0.9]
    kps[3] = [0.0, 1.0, 0.9]
    kps[4] = [20.0, 1.0, 0.9]

    crop, quality = extractor.extract_crop(dummy_frame, kps, bbox=None)
    assert crop is not None
    assert crop.shape[0] >= 16
    assert crop.shape[1] >= 16
    # Must be valid sliced sub-array within frame dimensions
    assert crop.shape[0] <= 100 and crop.shape[1] <= 100


def test_h5_canonical_yaw_signs():
    """H5: Canonical yaw sign convention: left < 0, right > 0."""
    # Mock model predicting left turn (yaw = -40.0)
    mock_left = MockSixDModel(mock_pitch=-10.0, mock_yaw=-40.0)
    provider_l = SixDRepNetHeadOrientationProvider(model_instance=mock_left)

    dummy_frame = np.zeros((200, 200, 3), dtype=np.uint8)
    kps = np.zeros((17, 3), dtype=np.float32)
    for i in range(5):
        kps[i] = [100.0 + i * 5, 100.0, 0.9]

    est_l = provider_l.estimate(keypoints=kps, frame=dummy_frame)
    assert est_l.is_valid
    assert est_l.yaw < 0.0  # LEFT is negative

    # Mock model predicting right turn (yaw = +40.0)
    mock_right = MockSixDModel(mock_pitch=-10.0, mock_yaw=40.0)
    provider_r = SixDRepNetHeadOrientationProvider(model_instance=mock_right)
    est_r = provider_r.estimate(keypoints=kps, frame=dummy_frame)
    assert est_r.is_valid
    assert est_r.yaw > 0.0  # RIGHT is positive


def test_h6_canonical_pitch_signs():
    """H6: Canonical pitch sign convention: down > 0."""
    # In 6DRepNet raw output, looking down gives pitch = -25.0
    # Our provider normalizes -(-25.0) = +25.0 (positive = downward tilt)
    mock_down = MockSixDModel(mock_pitch=-25.0, mock_yaw=0.0)
    provider = SixDRepNetHeadOrientationProvider(model_instance=mock_down)

    dummy_frame = np.zeros((200, 200, 3), dtype=np.uint8)
    kps = np.zeros((17, 3), dtype=np.float32)
    for i in range(5):
        kps[i] = [100.0 + i * 5, 100.0, 0.9]

    est = provider.estimate(keypoints=kps, frame=dummy_frame)
    assert est.is_valid
    assert est.pitch > 0.0  # DOWN is positive


def test_h7_seat_baseline_subtraction():
    """H7: Relative yaw/pitch baseline subtraction works correctly."""
    mock_model = MockSixDModel(mock_pitch=-10.0, mock_yaw=30.0)
    provider = SixDRepNetHeadOrientationProvider(model_instance=mock_model)

    dummy_frame = np.zeros((200, 200, 3), dtype=np.uint8)
    kps = np.zeros((17, 3), dtype=np.float32)
    for i in range(5):
        kps[i] = [100.0 + i * 5, 100.0, 0.9]

    # Neutral baseline (0.0): raw yaw 30 -> relative yaw 30
    est0 = provider.estimate(keypoints=kps, seat_baseline_yaw=0.0, seat_baseline_pitch=0.0, frame=dummy_frame)
    assert est0.yaw == 30.0
    assert est0.pitch == 10.0

    # Perspective baseline of +15.0 deg yaw and +5.0 deg pitch
    est_rel = provider.estimate(keypoints=kps, seat_baseline_yaw=15.0, seat_baseline_pitch=5.0, frame=dummy_frame)
    assert est_rel.yaw == 15.0  # 30.0 - 15.0 = 15.0
    assert est_rel.pitch == 5.0  # 10.0 - 5.0 = 5.0


def test_h8_observation_extractor_passes_frame():
    """H8: ObservationExtractor passes frame to provider when available and works without frame."""
    mock_provider = MagicMock(spec=HeadOrientationProvider)
    mock_provider.estimate.return_value = HeadOrientationEstimate(yaw=20.0, pitch=5.0, quality=0.8, source="mock")

    extractor = ObservationExtractor(head_pose_provider=mock_provider)
    s_ctx = SeatContext(
        seat_id="SEAT-TEST-01",
        room_id="ROOM-01",
        seat_code="SEAT-TEST-01",
        capabilities=SeatCapabilities(head_orientation=CapabilityStatus.ENABLED),
        reference_directions=SeatReferenceDirections(baseline_yaw=10.0, baseline_pitch=0.0),
    )

    kps = np.ones((17, 3), dtype=np.float32)
    det = Detection(bbox=(50.0, 50.0, 150.0, 200.0), confidence=0.9, keypoints=kps, class_id=0, class_name="person")

    # Pass frame
    dummy_frame = np.zeros((300, 300, 3), dtype=np.uint8)
    obs = extractor.extract(detection=det, seat_context=s_ctx, timestamp_ms=1000.0, frame=dummy_frame)

    assert mock_provider.estimate.called
    call_args = mock_provider.estimate.call_args
    assert call_args.kwargs.get("frame") is dummy_frame
    assert call_args.kwargs.get("seat_baseline_yaw") == 10.0

    # Ensure head yaw observation was extracted
    yaw_obs = [o for o in obs if o.observation_type == ObservationType.HEAD_YAW_RELATIVE.value]
    assert len(yaw_obs) == 1
    assert yaw_obs[0].value == 20.0


def test_h9_factory_pose_heuristic():
    """H9: Factory helper instantiates PoseHeuristicHeadOrientationProvider by default."""
    p = create_head_pose_provider("pose_heuristic")
    assert isinstance(p, PoseHeuristicHeadOrientationProvider)

    p2 = create_head_pose_provider("pose")
    assert isinstance(p2, PoseHeuristicHeadOrientationProvider)


def test_h10_factory_sixdrepnet():
    """H10: Factory helper instantiates SixDRepNetHeadOrientationProvider."""
    with patch("classroom_monitor.head_pose_provider.SixDRepNetHeadOrientationProvider._ensure_model_loaded"):
        p = create_head_pose_provider("sixdrepnet")
        assert isinstance(p, SixDRepNetHeadOrientationProvider)

        p2 = create_head_pose_provider("6drepnet")
        assert isinstance(p2, SixDRepNetHeadOrientationProvider)


def test_h11_label_aware_benchmark_matching():
    """H11: Label-aware benchmark rejects opposite directional match (LEFT != RIGHT)."""
    # Normalized labels
    h_label = normalize_label("HEAD_TURN_LEFT")
    ai_label = normalize_label("HEAD_TURN_RIGHT")
    assert h_label != ai_label

    # Overlap calculation
    iou = compute_temporal_iou(1000.0, 5000.0, 1200.0, 4800.0)
    assert iou > 0.80

    # Strict matching rule: (h_label == ai_label) must be False
    is_match = (h_label == ai_label) and (iou >= 0.30)
    assert not is_match


def test_h12_quality_clamping():
    """H12: Observation quality is derived and clamped to [0.0, 1.0]."""
    extractor = HeadCropExtractor()
    dummy_frame = np.zeros((400, 400, 3), dtype=np.uint8)

    kps = np.zeros((17, 3), dtype=np.float32)
    # High confidence landmarks
    for i in range(5):
        kps[i] = [150.0 + i * 10, 150.0, 1.5]  # Out of range conf

    crop, quality = extractor.extract_crop(dummy_frame, kps, bbox=None)
    assert 0.0 <= quality <= 1.0
