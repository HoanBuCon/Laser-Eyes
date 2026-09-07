"""Unit Tests for Classroom Cheating Detection Core Components.

Tests:
1. IoU Spatial Matching & Multi-Object Tracking.
2. Anti-Flickering Score Accumulator dynamics.
3. 4-State Behavior Machine Transitions.
4. Macro Room Crowd Context (Collective Suppression & Proximity Clusters).
5. Event Lifecycle & Peak-Confidence Evidence Frame Tracking.
"""

from __future__ import annotations

import numpy as np
import pytest

from classroom_monitor.behavior_tracker import PersonBehaviorTracker, StudentState
from classroom_monitor.config import ClassroomConfig
from classroom_monitor.event_engine import EventEngine
from classroom_monitor.live_event import LiveEvent
from classroom_monitor.models import Detection, TrackedDetection
from classroom_monitor.room_context import RoomContextAnalyzer
from classroom_monitor.score_accumulator import ScoreAccumulator
from classroom_monitor.spatial_matcher import SpatialMatcher, compute_bbox_iou


# ==============================================================================
# 1. Spatial Matching & IoU Tests
# ==============================================================================
def test_compute_bbox_iou():
    """Test mathematical accuracy of IoU box overlap."""
    box_a = (0, 0, 100, 100)
    box_b = (50, 0, 150, 100)

    # Overlap is 50x100 = 5000, Total Area is 10000 + 10000 - 5000 = 15000 -> IoU = 0.3333
    iou = compute_bbox_iou(box_a, box_b)
    assert abs(iou - 0.3333) < 0.01

    # Disjoint boxes
    box_c = (200, 200, 300, 300)
    assert compute_bbox_iou(box_a, box_c) == 0.0

    # Identical boxes
    assert compute_bbox_iou(box_a, box_a) == 1.0


def test_spatial_matcher_tracking():
    """Test tracking continuity and track ID assignment."""
    matcher = SpatialMatcher(iou_threshold=0.3, max_missing_frames=5)

    # Frame 0: 2 students
    dets_f0 = [
        Detection(class_id=2, class_name="no cheating", confidence=0.9, bbox=(10, 10, 50, 50), frame_index=0),
        Detection(class_id=2, class_name="no cheating", confidence=0.9, bbox=(100, 100, 150, 150), frame_index=0),
    ]
    tracked_f0 = matcher.update(dets_f0, frame_idx=0)
    assert len(tracked_f0) == 2
    track_ids_f0 = {td.track_id for td in tracked_f0}
    assert track_ids_f0 == {1, 2}

    # Frame 1: slight movement -> tracks should be maintained
    dets_f1 = [
        Detection(class_id=4, class_name="side peeking", confidence=0.85, bbox=(12, 12, 52, 52), frame_index=1),
        Detection(class_id=2, class_name="no cheating", confidence=0.92, bbox=(102, 98, 152, 148), frame_index=1),
    ]
    tracked_f1 = matcher.update(dets_f1, frame_idx=1)
    assert len(tracked_f1) == 2
    track_ids_f1 = {td.track_id for td in tracked_f1}
    assert track_ids_f1 == {1, 2}

    # Frame 2: Student 1 missing, new student 3 appears
    dets_f2 = [
        Detection(class_id=2, class_name="no cheating", confidence=0.9, bbox=(102, 98, 152, 148), frame_index=2),
        Detection(class_id=3, class_name="phone using", confidence=0.95, bbox=(300, 300, 350, 350), frame_index=2),
    ]
    tracked_f2 = matcher.update(dets_f2, frame_idx=2)
    assert len(tracked_f2) == 2
    track_ids_f2 = {td.track_id for td in tracked_f2}
    assert 2 in track_ids_f2
    assert 3 in track_ids_f2


# ==============================================================================
# 2. Score Accumulator & Anti-Flickering Tests
# ==============================================================================
def test_score_accumulator_flicker_tolerance():
    """Verify accumulator tolerates alternating frames without losing evidence."""
    acc = ScoreAccumulator(
        window_size=30,
        score_threshold=3.0,
        cheating_ratio_threshold=0.5,
        min_frames=5,
        normal_penalty=-0.3,
    )

    # Feed sequence with intermittent flicker: S S N S S N S S
    sequence = [
        ("side peeking", 0.9),
        ("side peeking", 0.85),
        ("no cheating", 0.95), # Flicker
        ("side peeking", 0.88),
        ("side peeking", 0.92),
        ("no cheating", 0.90), # Flicker
        ("side peeking", 0.89),
        ("side peeking", 0.91),
    ]

    for b, c in sequence:
        acc.add(b, c)

    assert acc.is_suspicious() is True
    assert acc.dominant_behavior == "side peeking"
    assert acc.cheating_ratio >= 0.70
    assert acc.cumulative_score > 4.0


# ==============================================================================
# 3. State Machine Transitions Tests
# ==============================================================================
def test_behavior_tracker_lifecycle():
    """Test full cycle: NORMAL -> WATCHING -> SUSPICIOUS -> COOLDOWN."""
    config = ClassroomConfig(score_threshold=2.0, min_frames_in_window=3, cooldown_seconds=1.0)
    tracker = PersonBehaviorTracker(track_id=1, fps=30.0, config=config)

    assert tracker.state == StudentState.NORMAL

    # Frame 0: normal
    det_normal = Detection(2, "no cheating", 0.9, (10, 10, 50, 50), 0)
    tracker.update(det_normal, frame_idx=0)
    assert tracker.state == StudentState.NORMAL

    # Frame 1: suspicious detection -> enters WATCHING
    det_cheating = Detection(3, "phone using", 0.95, (10, 10, 50, 50), 1)
    tracker.update(det_cheating, frame_idx=1)
    assert tracker.state == StudentState.WATCHING

    # Frame 2-4: sustained cheating -> enters SUSPICIOUS and emits event
    ev1 = tracker.update(det_cheating, frame_idx=2)
    ev2 = tracker.update(det_cheating, frame_idx=3)
    assert tracker.state == StudentState.SUSPICIOUS
    assert ev2 is not None
    assert ev2.behavior == "phone using"
    assert ev2.severity == "HIGH"

    # Frame 5-25: return to normal -> clears to COOLDOWN
    closed_ev = None
    for f in range(4, 30):
        res = tracker.update(det_normal, frame_idx=f)
        if res:
            closed_ev = res

    assert tracker.state == StudentState.COOLDOWN
    assert closed_ev is not None


# ==============================================================================
# 4. Crowd Room Context Tests
# ==============================================================================
def test_room_context_collective_suppression():
    """Test suppressing false alarms when majority of room performs same act."""
    analyzer = RoomContextAnalyzer(ClassroomConfig(collective_suppress_ratio=0.4))

    # 10 students: 6 front peeking (60% -> collective suppression)
    dets = []
    for i in range(6):
        d = Detection(1, "front peeking", 0.85, (i * 50, 50, (i + 1) * 50, 100), 0)
        dets.append(TrackedDetection(track_id=i + 1, detection=d))
    for i in range(6, 10):
        d = Detection(2, "no cheating", 0.90, (i * 50, 50, (i + 1) * 50, 100), 0)
        dets.append(TrackedDetection(track_id=i + 1, detection=d))

    signal = analyzer.analyze(dets)
    assert signal.suppress is True
    assert "Collective room behavior" in signal.reason


def test_room_context_spatial_cluster_boost():
    """Test boosting severity when 3 adjacent students peek simultaneously (in a 10-person room)."""
    analyzer = RoomContextAnalyzer(ClassroomConfig(collective_suppress_ratio=0.4, cluster_distance_threshold=100.0, cluster_min_size=3))

    # 10 students: 3 adjacent students peeking (30% < 40% threshold, but form cluster)
    dets = [
        TrackedDetection(1, Detection(4, "side peeking", 0.85, (40, 50, 80, 100), 0)),
        TrackedDetection(2, Detection(4, "side peeking", 0.88, (90, 50, 130, 100), 0)),
        TrackedDetection(3, Detection(4, "side peeking", 0.91, (140, 50, 180, 100), 0)),
    ]
    for i in range(4, 11):
        dets.append(
            TrackedDetection(i, Detection(2, "no cheating", 0.95, (i * 100, 300, i * 100 + 40, 350), 0))
        )

    signal = analyzer.analyze(dets)
    assert signal.suppress is False
    assert signal.boost_severity is True
    assert "Suspicious spatial cluster" in signal.reason


# ==============================================================================
# 5. Live Event Peak Confidence Tracking
# ==============================================================================
def test_live_event_peak_evidence():
    """Test that LiveEvent always stores the highest confidence frame for evidence."""
    img_low = np.zeros((100, 100, 3), dtype=np.uint8)
    img_peak = np.ones((100, 100, 3), dtype=np.uint8) * 255

    event = LiveEvent(
        track_id=1,
        behavior="phone using",
        start_frame=10,
        confidence=0.75,
        bbox=(10, 10, 50, 50),
        fps=30.0,
    )
    event.evidence_frame = img_low

    # Update with higher confidence at frame 15
    event.update(frame_idx=15, confidence=0.98, bbox=(12, 12, 52, 52), frame_image=img_peak)

    assert event.peak_confidence == 0.98
    assert event.peak_frame_idx == 15
    assert np.array_equal(event.evidence_frame, img_peak)

    # Update with lower confidence at frame 20 -> peak should remain unchanged
    event.update(frame_idx=20, confidence=0.80, bbox=(12, 12, 52, 52), frame_image=img_low)
    assert event.peak_confidence == 0.98
    assert event.peak_frame_idx == 15
