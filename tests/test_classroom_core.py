"""Unit Tests for Classroom Cheating Detection Core Components.

Tests:
1. IoU Spatial Matching & Multi-Object Tracking.
2. Anti-Flickering Score Accumulator dynamics (Time-Aware Sliding Window).
3. 4-State Behavior Machine Transitions with Silent Tracking & Recidivism Escalation.
4. Macro Room Crowd Context (Collective Suppression & Proximity Clusters).
5. Live Event Peak-Confidence Evidence Frame Tracking.
6. 2D Kinematic Kalman Filter Box Tracking.
7. Spatial Coasting during temporary occlusions (Anti-ID Switch).
8. Time-based Millisecond Accumulator under variable frame rates.
9. 10-Second Evidence Video Ring Buffer and MP4 Generation.
10. High-Resolution SAHI Slicing and NMS Suppression.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
import numpy as np
import pytest

from classroom_monitor.behavior_tracker import PersonBehaviorTracker, StudentState
from classroom_monitor.config import ClassroomConfig
from classroom_monitor.detector import (
    ClassroomDetector,
    PoseClassroomDetector,
    calculate_head_pose_yaw_pitch,
    check_phone_posture_multicue,
    create_detector,
)
from classroom_monitor.event_engine import EventEngine
from classroom_monitor.live_event import LiveEvent
from classroom_monitor.models import Detection, EventStatus, TrackState, TrackedDetection
from classroom_monitor.room_context import RoomContextAnalyzer
from classroom_monitor.score_accumulator import ScoreAccumulator
from classroom_monitor.spatial_matcher import (
    KalmanBoxTracker,
    SpatialMatcher,
    compute_bbox_iou,
    compute_centroid_distance,
)
from classroom_monitor.video_buffer import EvidenceVideoBuffer


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
        ("no cheating", 0.95),  # Flicker
        ("side peeking", 0.88),
        ("side peeking", 0.92),
        ("no cheating", 0.90),  # Flicker
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
# 3. State Machine Transitions & Cooldown Tests
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
    analyzer = RoomContextAnalyzer(
        ClassroomConfig(collective_suppress_ratio=0.4, cluster_distance_threshold=100.0, cluster_min_size=3)
    )

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


# ==============================================================================
# 6. Kalman Filter 2D Kinematics Tests
# ==============================================================================
def test_kalman_filter_box_kinematics():
    """Verify 2D Kalman Filter correctly predicts motion and updates box state."""
    init_box = (100, 100, 200, 200)
    kf = KalmanBoxTracker(init_box)

    # Predict next state without measurement
    pred_box = kf.predict()
    assert abs(pred_box[0] - 100) <= 2
    assert abs(pred_box[1] - 100) <= 2

    # Feed moving measurements (moving right by 10px per frame)
    for step in range(1, 6):
        new_box = (100 + step * 10, 100, 200 + step * 10, 200)
        kf.predict()
        kf.update(new_box)

    # Kalman filter should predict next position around 160
    final_pred = kf.predict()
    assert final_pred[0] >= 150  # Velocity has been successfully integrated


# ==============================================================================
# 7. Spatial Coasting during Temporary Occlusion (Anti-ID Switch)
# ==============================================================================
def test_spatial_coasting_during_occlusion():
    """Verify that when a proctor walks past and occludes a student for 3 frames,

    the student's track enters coasting mode and resumes with the SAME track ID.
    """
    matcher = SpatialMatcher(
        iou_threshold=0.3,
        max_missing_frames=10,
        max_coasting_frames=8,
        tracker_type="kalman_iou",
    )

    # Frame 0: Student 1 detected
    det0 = [Detection(2, "no cheating", 0.92, (100, 100, 160, 180), 0)]
    t0 = matcher.update(det0, 0)
    assert len(t0) == 1
    assert t0[0].track_id == 1

    # Frames 1, 2, 3: Proctor walks past -> 0 detections
    for f in range(1, 4):
        t_miss = matcher.update([], f)
        assert len(t_miss) == 0
        assert 1 in matcher.tracks
        assert matcher.tracks[1].state == TrackState.COASTING
        assert matcher.tracks[1].missing_frames == f

    # Frame 4: Student 1 reappears at essentially same desk location
    det4 = [Detection(2, "no cheating", 0.90, (102, 101, 162, 181), 4)]
    t4 = matcher.update(det4, 4)
    assert len(t4) == 1
    # Crucial assertion: ID Switch eliminated, Track ID remains 1!
    assert t4[0].track_id == 1
    assert matcher.tracks[1].state == TrackState.CONFIRMED


# ==============================================================================
# 8. Time-Aware Millisecond Sliding Window under Variable FPS
# ==============================================================================
def test_time_based_millisecond_accumulator():
    """Verify ScoreAccumulator maintains exact time span even when FPS fluctuates."""
    acc = ScoreAccumulator(
        window_duration_ms=1000.0,
        min_duration_ms=400.0,
        score_threshold=3.0,
        cheating_ratio_threshold=0.5,
    )

    # Feed 10 detections at 100ms intervals (1.0 second span)
    for i in range(10):
        ts = i * 100.0  # 0ms to 900ms
        acc.add("side peeking", 0.85, timestamp_ms=ts, frame_idx=i)

    assert acc.time_span_ms == 900.0
    assert acc.is_suspicious() is True
    assert acc.dominant_behavior == "side peeking"

    # Add a normal frame at 1200ms -> oldest frames older than 200ms should be pruned
    acc.add("no cheating", 0.90, timestamp_ms=1200.0, frame_idx=10)
    # Oldest record in window should now be >= 200ms
    assert acc.time_span_ms <= 1000.0


# ==============================================================================
# 9. Silent Background Tracking & Recidivism Escalation
# ==============================================================================
def test_silent_background_tracking_and_recidivism_escalation():
    """Verify that cheating during cooldown triggers instant Recidivism Escalation to HIGH."""
    config = ClassroomConfig(
        score_threshold=2.0,
        min_frames_in_window=3,
        cooldown_seconds=10.0,
        silent_cooldown_tracking=True,
        recidivism_escalation=True,
        recidivism_score_threshold=1.5,
    )
    tracker = PersonBehaviorTracker(track_id=7, fps=30.0, config=config)

    # 1. Trigger first violation
    det_phone = Detection(3, "phone using", 0.95, (50, 50, 100, 100), 0)
    det_normal = Detection(2, "no cheating", 0.90, (50, 50, 100, 100), 0)

    tracker.update(det_phone, frame_idx=0)
    tracker.update(det_phone, frame_idx=1)
    tracker.update(det_phone, frame_idx=2)
    assert tracker.state == StudentState.SUSPICIOUS

    # Clear to enter COOLDOWN
    for f in range(3, 20):
        tracker.update(det_normal, frame_idx=f)
    assert tracker.state == StudentState.COOLDOWN

    # 2. Student attempts to cheat AGAIN during Cooldown (Frame 25 < Cooldown 300 frames)
    # Background silent tracking should capture it and trigger Recidivism Escalation
    recidivist_event = None
    for f in range(21, 26):
        res = tracker.update(det_phone, frame_idx=f)
        if res is not None:
            recidivist_event = res

    assert recidivist_event is not None
    assert recidivist_event.is_recidivist is True
    assert recidivist_event.severity == "HIGH"
    assert tracker.state in (StudentState.FLAGGED_FOR_HUMAN_REVIEW, StudentState.SUSPICIOUS)


# ==============================================================================
# 10. Evidence Video Ring Buffer and MP4 Generation
# ==============================================================================
def test_evidence_video_ring_buffer():
    """Verify that EvidenceVideoBuffer captures pre- and post-event frames and produces MP4."""
    with tempfile.TemporaryDirectory() as tmpdir:
        buf = EvidenceVideoBuffer(
            pre_event_seconds=1.0,
            post_event_seconds=1.0,
            fps=10.0,
            output_dir=tmpdir,
        )

        dummy_frame = np.zeros((240, 320, 3), dtype=np.uint8)

        # Ingest 10 pre-event frames
        for f in range(10):
            buf.add_frame(dummy_frame, frame_idx=f, timestamp_ms=f * 100.0)

        # Trigger event at frame 10
        job = buf.trigger_clip(
            event_id="EVT-TEST-001",
            track_id=2,
            behavior="phone using",
            frame_idx=10,
            timestamp_ms=1000.0,
        )

        # Ingest 10 post-event frames -> should trigger video file write
        completed = []
        for f in range(11, 22):
            res = buf.add_frame(dummy_frame, frame_idx=f, timestamp_ms=f * 100.0)
            if res:
                completed.extend(res)

        assert len(completed) >= 1
        saved_path = Path(completed[0].saved_file_path)
        assert saved_path.exists()
        assert saved_path.stat().st_size > 0


def test_sahi_high_res_slicing_and_nms():
    """Verify SAHI dynamic slicing and NMS merging on simulated large image."""
    config = ClassroomConfig(
        enable_sahi_tiling=True,
        sahi_slice_size=320,
        sahi_overlap_ratio=0.20,
        sahi_min_resolution=600,
        nms_iou_threshold=0.45,
    )
    detector = ClassroomDetector(config=config)

    # 1. Test NMS deduplication directly
    overlapping_dets = [
        Detection(3, "phone using", 0.92, (100, 100, 180, 180), 0),
        Detection(3, "phone using", 0.81, (105, 102, 182, 184), 0),  # Overlap IoU > 0.8 -> suppressed
        Detection(4, "side peeking", 0.88, (300, 300, 380, 380), 0), # Distinct box -> kept
    ]
    merged = detector._apply_nms(overlapping_dets, iou_thresh=0.45)
    assert len(merged) == 2
    assert merged[0].confidence == 0.92
    assert merged[1].class_name == "side peeking"

    # 2. Test mock detect with large frame
    detector._is_mock = True
    large_frame = np.zeros((800, 1200, 3), dtype=np.uint8)
    dets = detector.detect(large_frame, frame_index=0)
    assert len(dets) >= 1
    assert all(d.confidence > 0 for d in dets)


# ==============================================================================
# 11. Two-Stage Pose Estimation & Multi-Cue Behavior Tests
# ==============================================================================
def test_head_pose_yaw_pitch_computation():
    """Verify trigonometric calculation of Head Yaw and Head Pitch from 2D keypoints."""
    # Keypoints: 0: Nose, 1: L_Eye, 2: R_Eye, 3: L_Ear, 4: R_Ear, 5: L_Shoulder, 6: R_Shoulder
    # Scenario 1: Straight neutral head facing camera
    kps_neutral = np.zeros((17, 3), dtype=np.float32)
    kps_neutral[0] = [100, 80, 0.9]   # Nose
    kps_neutral[3] = [70, 75, 0.9]    # L Ear
    kps_neutral[4] = [130, 75, 0.9]   # R Ear
    kps_neutral[5] = [60, 150, 0.9]   # L Shoulder
    kps_neutral[6] = [140, 150, 0.9]  # R Shoulder

    yaw_neutral, pitch_neutral = calculate_head_pose_yaw_pitch(kps_neutral)
    assert abs(yaw_neutral) < 5.0  # Straight ahead

    # Scenario 2: Head turned right (Nose shifted towards right ear)
    kps_turned = np.copy(kps_neutral)
    kps_turned[0] = [125, 80, 0.9]  # Nose shifted significantly right
    yaw_turned, _ = calculate_head_pose_yaw_pitch(kps_turned)
    assert yaw_turned > 25.0  # Significant right yaw (side peeking)


def test_phone_posture_multicue_classification():
    """Verify multi-cue fusion distinguishes normal writing from phone cheating posture."""
    kps = np.zeros((17, 3), dtype=np.float32)
    kps[5] = [80, 100, 0.9]   # L Shoulder
    kps[6] = [160, 100, 0.9]  # R Shoulder (width = 80px)

    # Scenario 1: Normal exam writing (hands on table, Y_wrist not deep low, pitch neutral)
    kps[9] = [110, 120, 0.9]  # L Wrist
    kps[10] = [130, 120, 0.9] # R Wrist (dist = 20px, but Y_wrist is high, pitch = 5 deg)
    assert not check_phone_posture_multicue(kps, pitch=5.0, wrist_ratio_threshold=0.28, pitch_threshold=20.0)

    # Scenario 2: Phone cheating (hands low in lap, close together, head pitched down 25 deg)
    kps[9] = [115, 160, 0.9]  # L Wrist low
    kps[10] = [125, 160, 0.9] # R Wrist low (dist = 10px / 80px = 0.125 < 0.28)
    assert check_phone_posture_multicue(kps, pitch=25.0, wrist_ratio_threshold=0.28, pitch_threshold=20.0)


def test_pose_classroom_detector_and_factory():
    """Verify PoseClassroomDetector initialization, factory creation, and detection pipeline."""
    config_1stage = ClassroomConfig(pipeline_mode="1stage_yolo")
    config_2stage = ClassroomConfig(pipeline_mode="2stage_pose")

    detector_1 = create_detector(config=config_1stage)
    detector_2 = create_detector(config=config_2stage)

    assert isinstance(detector_1, ClassroomDetector)
    assert isinstance(detector_2, PoseClassroomDetector)

    # Mock detection run
    detector_2.allow_mock = True
    detector_2._is_mock = True
    dummy_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    dets_f0 = detector_2.detect(dummy_frame, frame_index=0)
    assert len(dets_f0) == 3
    assert all(d.class_name == "no cheating" for d in dets_f0)

    # Frame 45 triggers student 2 side peeking
    dets_f45 = detector_2.detect(dummy_frame, frame_index=45)
    assert any(d.class_name == "side peeking" for d in dets_f45)
    assert len(detector_2.detect_cheating_only(dummy_frame, frame_index=45)) >= 1


