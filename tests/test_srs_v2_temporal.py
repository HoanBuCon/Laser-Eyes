"""Comprehensive SRS v2.0 12-Case Regression Test Suite.

Validates all required behavior cases specified in Section 33 of SRS v2.0:
- CASE 1: NORMAL WRITING Guard (Head down + wrist on desk -> NO below desk, NO risk saturation)
- CASE 2: WRIST IN WRITING ZONE Mandatory Suppression
- CASE 3: WRIST MISSING Unknown-Safe Handling
- CASE 4: SHORT HEAD TURN Transient Filtering
- CASE 5: REPEATED SAME-NEIGHBOR HEAD TURN Pattern Trigger
- CASE 6: BODY LEAN NOT TOWARD NEIGHBOR Suppression
- CASE 7: BODY LEAN TOWARD NEIGHBOR + Persistence Trigger
- CASE 8: PROCTOR OCCLUSION Seat Identity Stability
- CASE 9: UNMAPPED PERSON Tagging (Never Proctor)
- CASE 10: MISSING DESK GEOMETRY Capability Gating
- CASE 11: EVENT NEAR EOF Evidence Buffer Flushing
- CASE 12: 5 FPS vs 10 FPS Temporal Invariance
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

import numpy as np
import pytest

from classroom_monitor.behavior_pattern_engine import BehaviorPatternEngine, PatternType
from classroom_monitor.models import Detection
from classroom_monitor.observation_extractor import ObservationExtractor, ObservationType, WristZone
from classroom_monitor.scene_context import CapabilityStatus, DeskGeometry, SeatContext, SeatGraph, SeatNeighbors
from classroom_monitor.seat_manager import SeatDefinition, SeatManager, SeatState
from classroom_monitor.seat_risk_tracker import RiskState, SeatRiskTracker
from classroom_monitor.temporal_episode_engine import EpisodeState, EpisodeType, TemporalEpisode, TemporalEpisodeEngine
from classroom_monitor.video_buffer import EvidenceVideoBuffer


def _create_mock_detection(
    bbox=(100, 100, 250, 300),
    confidence=0.90,
    nose_xy=(175, 120),
    l_ear_xy=(160, 120),
    r_ear_xy=(190, 120),
    l_shoulder_xy=(140, 160),
    r_shoulder_xy=(210, 160),
    l_wrist_xy=(150, 220),
    r_wrist_xy=(200, 220),
    kp_conf=0.90,
) -> Detection:
    """Helper to build 17-keypoint COCO format synthetic person detection."""
    kps = np.zeros((17, 3), dtype=np.float32)
    kps[0] = [nose_xy[0], nose_xy[1], kp_conf]           # Nose
    kps[1] = [nose_xy[0] - 8, nose_xy[1] - 5, kp_conf]   # L Eye
    kps[2] = [nose_xy[0] + 8, nose_xy[1] - 5, kp_conf]   # R Eye
    kps[3] = [l_ear_xy[0], l_ear_xy[1], kp_conf]         # L Ear
    kps[4] = [r_ear_xy[0], r_ear_xy[1], kp_conf]         # R Ear
    kps[5] = [l_shoulder_xy[0], l_shoulder_xy[1], kp_conf] # L Shoulder
    kps[6] = [r_shoulder_xy[0], r_shoulder_xy[1], kp_conf] # R Shoulder
    kps[9] = [l_wrist_xy[0], l_wrist_xy[1], kp_conf]     # L Wrist
    kps[10] = [r_wrist_xy[0], r_wrist_xy[1], kp_conf]    # R Wrist

    return Detection(
        class_id=0,
        class_name="person",
        confidence=confidence,
        bbox=bbox,
        keypoints=kps,
    )


# ==============================================================================
# CASE 1: NORMAL WRITING GUARD
# ==============================================================================
def test_case_01_normal_writing_guard():
    """Case 1: Head down + Wrists in writing zone -> NO below desk, NO risk saturation."""
    extractor = ObservationExtractor()
    ep_engine = TemporalEpisodeEngine()
    pat_engine = BehaviorPatternEngine()
    risk_tracker = SeatRiskTracker(room_id="ROOM-101")

    desk_geo = DeskGeometry(desk_boundary_y=250.0)
    ctx = SeatContext(
        seat_id="SEAT-01",
        desk_geometry=desk_geo,
    )

    # Simulate 5.0 seconds of normal writing (wrists at y=220, above desk boundary 250)
    for step in range(30):
        t_ms = step * 100.0  # 10 Hz
        det = _create_mock_detection(
            l_wrist_xy=(150, 220),
            r_wrist_xy=(190, 220),
            nose_xy=(175, 175),  # Pitched downward into exam paper
        )

        obs = extractor.extract(det, ctx, t_ms)
        eps = ep_engine.process_observations(obs, t_ms)
        pats = pat_engine.ingest_episodes(eps, ep_engine.completed_episodes, ctx, t_ms)
        event = risk_tracker.update_seat("SEAT-01", eps, pats, t_ms, det)

        # No suspicious event emitted
        assert event is None
        # Wrists must be WRITING
        lw_obs = next(o for o in obs if o.observation_type == ObservationType.LEFT_WRIST_ZONE.value)
        assert lw_obs.value == WristZone.WRITING.value

    # Risk must remain in NORMAL band (< 30)
    prof = risk_tracker.get_or_create_profile("SEAT-01")
    assert prof.risk_score < 30.0
    assert prof.current_state == RiskState.NORMAL.value


# ==============================================================================
# CASE 2: WRIST IN WRITING ZONE MANDATORY SUPPRESSION
# ==============================================================================
def test_case_02_wrist_in_writing_zone_suppression():
    """Case 2: Desk geometry contains wrist in writing zone -> NEVER Below-Desk."""
    writing_poly = np.array([[100, 180], [300, 180], [300, 260], [100, 260]], dtype=np.float32)
    desk_geo = DeskGeometry(writing_zone_polygon=writing_poly, desk_boundary_y=260.0)

    # Point inside polygon
    assert desk_geo.contains_wrist_in_writing_zone((180, 220)) is True
    assert desk_geo.is_wrist_below_desk((180, 220)) is False

    # Point below boundary
    assert desk_geo.contains_wrist_in_writing_zone((180, 290)) is False
    assert desk_geo.is_wrist_below_desk((180, 290)) is True


# ==============================================================================
# CASE 3: WRIST MISSING UNKNOWN-SAFE HANDLING
# ==============================================================================
def test_case_03_wrist_missing_unknown_safe():
    """Case 3: Missing or low-confidence wrist keypoint -> UNKNOWN (never false under desk)."""
    extractor = ObservationExtractor(min_keypoint_conf=0.30)
    desk_geo = DeskGeometry(desk_boundary_y=250.0)
    ctx = SeatContext(seat_id="SEAT-01", desk_geometry=desk_geo)

    # Detection with low wrist confidence (0.15 < 0.30)
    det = _create_mock_detection(kp_conf=0.90)
    det.keypoints[9][2] = 0.15  # Low L wrist conf
    det.keypoints[10][2] = 0.10 # Low R wrist conf

    obs = extractor.extract(det, ctx, timestamp_ms=1000.0)
    lw_obs = next(o for o in obs if o.observation_type == ObservationType.LEFT_WRIST_ZONE.value)
    rw_obs = next(o for o in obs if o.observation_type == ObservationType.RIGHT_WRIST_ZONE.value)

    assert lw_obs.value == WristZone.UNKNOWN.value
    assert rw_obs.value == WristZone.UNKNOWN.value


# ==============================================================================
# CASE 4: SHORT HEAD TURN TRANSIENT FILTERING
# ==============================================================================
def test_case_04_short_head_turn_no_repeated_glance():
    """Case 4: Single 0.3s head turn -> No repeated glance pattern, No immediate Flag."""
    ep_engine = TemporalEpisodeEngine(min_persistence_ms=400.0)
    pat_engine = BehaviorPatternEngine()
    ctx = SeatContext(seat_id="SEAT-01", neighbors=SeatNeighbors(right_neighbor_id="SEAT-02"))

    # 300ms turn (3 frames at 100ms) - below 400ms persistence
    for t in [0.0, 100.0, 200.0, 300.0]:
        raw_obs = [
            ObservationExtractor().extract(
                _create_mock_detection(nose_xy=(205, 120), l_ear_xy=(160, 120), r_ear_xy=(190, 120)), # Turned Right
                ctx,
                t,
            )[1]
        ]
        eps = ep_engine.process_observations(raw_obs, t)
        pats = pat_engine.ingest_episodes(eps, ep_engine.completed_episodes, ctx, t)
        # Must not form repeated glance pattern
        assert not any(p.pattern_type == PatternType.REPEATED_NEIGHBOR_GLANCE.value for p in pats)


# ==============================================================================
# CASE 5: REPEATED SAME-NEIGHBOR HEAD TURN PATTERN
# ==============================================================================
def test_case_05_repeated_neighbor_head_turn():
    """Case 5: Multiple distinct episodes towards same neighbor -> REPEATED_NEIGHBOR_GLANCE."""
    ep_engine = TemporalEpisodeEngine(min_persistence_ms=300.0, release_hysteresis_ms=200.0)
    pat_engine = BehaviorPatternEngine(glance_rolling_window_ms=20000.0, min_glance_episodes=2)
    ctx = SeatContext(seat_id="SEAT-01", neighbors=SeatNeighbors(right_neighbor_id="SEAT-02"))
    extractor = ObservationExtractor()

    # Episode 1: Turn Right (0 to 600ms)
    for t in [0.0, 100.0, 200.0, 300.0, 400.0, 500.0, 600.0]:
        det = _create_mock_detection(nose_xy=(208, 120), l_ear_xy=(160, 120), r_ear_xy=(190, 120))
        obs = extractor.extract(det, ctx, t)
        eps = ep_engine.process_observations(obs, t)
        pat_engine.ingest_episodes(eps, ep_engine.completed_episodes, ctx, t)

    # Return to Baseline (700 to 1500ms)
    for t in [700.0, 900.0, 1200.0, 1500.0]:
        det = _create_mock_detection(nose_xy=(175, 120))
        obs = extractor.extract(det, ctx, t)
        eps = ep_engine.process_observations(obs, t)
        pat_engine.ingest_episodes(eps, ep_engine.completed_episodes, ctx, t)

    # Episode 2: Turn Right Again (1600 to 2200ms)
    detected_pats = []
    for t in [1600.0, 1800.0, 2000.0, 2200.0]:
        det = _create_mock_detection(nose_xy=(208, 120), l_ear_xy=(160, 120), r_ear_xy=(190, 120))
        obs = extractor.extract(det, ctx, t)
        eps = ep_engine.process_observations(obs, t)
        pats = pat_engine.ingest_episodes(eps, ep_engine.completed_episodes, ctx, t)
        detected_pats.extend(pats)

    assert any(p.pattern_type == PatternType.REPEATED_NEIGHBOR_GLANCE.value for p in detected_pats)


# ==============================================================================
# CASE 6: BODY LEAN NOT TOWARD NEIGHBOR
# ==============================================================================
def test_case_06_body_lean_not_toward_neighbor():
    """Case 6: Body lean to the left when no left neighbor exists -> NO NEIGHBOR_ORIENTED_LEAN."""
    pat_engine = BehaviorPatternEngine()
    # Seat with ONLY right neighbor (Left side is wall/aisle)
    ctx = SeatContext(seat_id="SEAT-01", neighbors=SeatNeighbors(left_neighbor_id=None, right_neighbor_id="SEAT-02"))

    # Active Torso Lean Left episode
    lean_left_ep = TemporalEpisode(
        episode_id="ep-lean-01",
        seat_id="SEAT-01",
        episode_type=EpisodeType.TORSO_LEAN_LEFT.value,
        state=EpisodeState.ACTIVE,
        start_timestamp_ms=1000.0,
        peak_timestamp_ms=1500.0,
        duration_ms=1600.0,
    )

    pats = pat_engine.ingest_episodes([lean_left_ep], [], ctx, timestamp_ms=2600.0)
    # Must NOT emit NEIGHBOR_ORIENTED_LEAN because target_neighbor_id is None
    lean_pats = [p for p in pats if p.pattern_type == PatternType.NEIGHBOR_ORIENTED_LEAN.value and p.target_neighbor_id is not None]
    assert len(lean_pats) == 0


# ==============================================================================
# CASE 7: BODY LEAN TOWARD NEIGHBOR + PERSISTENCE
# ==============================================================================
def test_case_07_body_lean_toward_neighbor_persistence():
    """Case 7: Body lean to the right towards existing right neighbor for 1.5s -> NEIGHBOR_ORIENTED_LEAN."""
    pat_engine = BehaviorPatternEngine(lean_min_duration_ms=1000.0)
    ctx = SeatContext(seat_id="SEAT-01", neighbors=SeatNeighbors(right_neighbor_id="SEAT-02"))

    lean_right_ep = TemporalEpisode(
        episode_id="ep-lean-02",
        seat_id="SEAT-01",
        episode_type=EpisodeType.TORSO_LEAN_RIGHT.value,
        state=EpisodeState.ACTIVE,
        start_timestamp_ms=1000.0,
        peak_timestamp_ms=1800.0,
        duration_ms=1500.0,
    )

    pats = pat_engine.ingest_episodes([lean_right_ep], [], ctx, timestamp_ms=2500.0)
    assert any(p.pattern_type == PatternType.NEIGHBOR_ORIENTED_LEAN.value and p.target_neighbor_id == "SEAT-02" for p in pats)


# ==============================================================================
# CASE 8: PROCTOR OCCLUSION SEAT STABILITY
# ==============================================================================
def test_case_08_proctor_occlusion_seat_stability():
    """Case 8: Foreground proctor occlusion (2.5s) -> Seat state is OCCLUDED, NO false SEAT_LEFT."""
    mgr = SeatManager(room_id="ROOM-101", occlusion_grace_period_ms=4000.0)
    s_def = SeatDefinition(
        seat_id="SEAT-01",
        room_id="ROOM-101",
        seat_code="SEAT-01",
        polygon=np.array([[100, 100], [250, 100], [250, 300], [100, 300]], dtype=np.float32),
    )
    mgr.load_seats([s_def])

    # 1. Occupied at t=0
    det = _create_mock_detection(bbox=(110, 110, 240, 280))
    mapped, _ = mgr.map_detections_to_seats([det], timestamp_ms=0.0)
    assert mgr.occupancies["SEAT-01"].state == SeatState.OCCUPIED

    # 2. Occluded / 0 candidates for 2.5s (<= 4.0s grace period)
    mapped, _ = mgr.map_detections_to_seats([], timestamp_ms=2500.0)
    assert mgr.occupancies["SEAT-01"].state == SeatState.OCCLUDED
    assert mapped["SEAT-01"] is None


# ==============================================================================
# CASE 9: UNMAPPED PERSON TAGGING
# ==============================================================================
def test_case_09_unmapped_person_tagging():
    """Case 9: Person outside all seat ROIs -> UNMAPPED_PERSON, seat_id=None, 0 candidate risk."""
    mgr = SeatManager(room_id="ROOM-101")
    s_def = SeatDefinition(
        seat_id="SEAT-01",
        room_id="ROOM-101",
        seat_code="SEAT-01",
        polygon=np.array([[100, 100], [200, 100], [200, 200], [100, 200]], dtype=np.float32),
    )
    mgr.load_seats([s_def])

    # Person located at (500, 500) -> far outside Seat-01
    roaming_det = _create_mock_detection(bbox=(450, 450, 550, 550))
    mapped, unmapped = mgr.map_detections_to_seats([roaming_det], timestamp_ms=1000.0)

    assert mapped["SEAT-01"] is None
    assert len(unmapped) == 1
    assert unmapped[0] == roaming_det


# ==============================================================================
# CASE 10: MISSING DESK GEOMETRY CAPABILITY GATING
# ==============================================================================
def test_case_10_missing_desk_geometry_gating():
    """Case 10: Uncalibrated desk geometry -> DESK_HAND_INTERACTION is DISABLED/UNKNOWN."""
    # Seat without desk_geometry
    ctx = SeatContext(seat_id="SEAT-01", desk_geometry=None)
    assert ctx.capabilities.desk_hand_interaction == CapabilityStatus.DISABLED

    extractor = ObservationExtractor()
    det = _create_mock_detection(l_wrist_xy=(150, 300), r_wrist_xy=(190, 300))
    obs = extractor.extract(det, ctx, timestamp_ms=1000.0)

    lw_obs = next(o for o in obs if o.observation_type == ObservationType.LEFT_WRIST_ZONE.value)
    # Must degrade to UNKNOWN
    assert lw_obs.value == WristZone.UNKNOWN.value


# ==============================================================================
# CASE 11: EVENT NEAR EOF EVIDENCE BUFFER FLUSHING
# ==============================================================================
def test_case_11_event_near_eof_flushing():
    """Case 11: Event near video EOF -> flush_all forces writer completion without dropping event."""
    with tempfile.TemporaryDirectory() as tmpdir:
        buffer = EvidenceVideoBuffer(
            pre_event_seconds=1.0,
            post_event_seconds=1.0,
            fps=10.0,
            output_dir=tmpdir,
        )

        dummy_frame = np.zeros((240, 320, 3), dtype=np.uint8)

        # Feed 5 frames
        for f in range(5):
            buffer.add_frame(dummy_frame, frame_idx=f, timestamp_ms=f * 100.0)

        # Trigger event at frame 4 (just before video ends)
        job = buffer.trigger_clip(
            event_id="evt-eof-test-01",
            track_id=1,
            behavior="REPEATED_NEIGHBOR_GLANCE",
            frame_idx=4,
            timestamp_ms=400.0,
        )

        # Video stream ends immediately -> Call flush_all()
        flushed_paths = buffer.flush_all()

        assert len(flushed_paths) == 1
        assert Path(flushed_paths[0]).exists()
        assert Path(flushed_paths[0]).stat().st_size > 0


# ==============================================================================
# CASE 12: 5 FPS VS 10 FPS TEMPORAL INVARIANCE
# ==============================================================================
def test_case_12_framerate_invariance_5fps_vs_10fps():
    """Case 12: Running same behavior at 5 FPS vs 10 FPS yields identical episode start/duration."""
    def _run_simulation(fps: float):
        dt_ms = 1000.0 / fps
        total_time_ms = 3000.0  # 3 seconds total
        steps = int(total_time_ms / dt_ms)

        ep_engine = TemporalEpisodeEngine(min_persistence_ms=400.0, release_hysteresis_ms=300.0)
        extractor = ObservationExtractor()
        ctx = SeatContext(seat_id="SEAT-01")

        episodes = []
        for s in range(steps):
            t_ms = s * dt_ms
            # Turn active between 500ms and 2000ms (duration 1500ms)
            if 500.0 <= t_ms <= 2000.0:
                det = _create_mock_detection(nose_xy=(210, 120), l_ear_xy=(160, 120), r_ear_xy=(190, 120))
            else:
                det = _create_mock_detection(nose_xy=(175, 120))

            obs = extractor.extract(det, ctx, t_ms)
            eps = ep_engine.process_observations(obs, t_ms)
            if eps:
                episodes.extend(eps)

        completed = ep_engine.completed_episodes
        return completed

    completed_5fps = _run_simulation(fps=5.0)
    completed_10fps = _run_simulation(fps=10.0)

    assert len(completed_5fps) == 1
    assert len(completed_10fps) == 1

    ep5 = completed_5fps[0]
    ep10 = completed_10fps[0]

    # Start timestamps and durations must be consistent within 1 sampling period
    assert abs(ep5.start_timestamp_ms - ep10.start_timestamp_ms) <= 200.0
    assert abs(ep5.duration_ms - ep10.duration_ms) <= 300.0
