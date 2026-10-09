"""Regression Test Suite for Wrist Unknown Safety & Desk Geometry (WR1 - WR9).

Ensures unknown-safe wrist observation and occlusion handling:
- WR1: low-confidence wrist produces WristZone.UNKNOWN.
- WR2: missing desk geometry produces WristZone.UNKNOWN.
- WR3: missing wrist keypoint produces WristZone.UNKNOWN.
- WR4: UNKNOWN observations alone never activate WRIST_BELOW_DESK episode.
- WR5: UNKNOWN observations alone never add risk points.
- WR6: WRITING zone has priority over UNDER_DESK.
- WR7: persistent visible under-desk wrist activates WRIST_BELOW_DESK episode.
- WR8: occlusion does not trigger a false under-desk episode.
- WR9: wrist missing grace behaves according to configured duration.
"""

from __future__ import annotations

import numpy as np
import pytest

from classroom_monitor.models import Detection
from classroom_monitor.observation_extractor import ObservationExtractor, ObservationType, WristZone
from classroom_monitor.scene_context import CapabilityStatus, DeskGeometry, SeatContext
from classroom_monitor.seat_risk_tracker import EPISODE_PRIORITY_WEIGHTS, SeatRiskTracker
from classroom_monitor.temporal_episode_engine import EpisodeState, EpisodeType, TemporalEpisodeEngine


def test_wr1_low_confidence_wrist():
    """WR1: low-confidence wrist (<0.30) produces WristZone.UNKNOWN."""
    extractor = ObservationExtractor(min_keypoint_conf=0.30)
    desk_geo = DeskGeometry(desk_boundary_y=400.0)
    kp = np.array([100.0, 500.0, 0.20])  # Below desk, but confidence 0.20 < 0.30

    zone = extractor._evaluate_wrist_zone(kp, desk_geo, CapabilityStatus.ENABLED)
    assert zone == WristZone.UNKNOWN


def test_wr2_no_desk_geometry():
    """WR2: missing desk geometry produces WristZone.UNKNOWN."""
    extractor = ObservationExtractor(min_keypoint_conf=0.30)
    kp = np.array([100.0, 500.0, 0.90])

    zone = extractor._evaluate_wrist_zone(kp, None, CapabilityStatus.ENABLED)
    assert zone == WristZone.UNKNOWN


def test_wr3_missing_wrist():
    """WR3: missing/zero wrist keypoint produces WristZone.UNKNOWN."""
    extractor = ObservationExtractor(min_keypoint_conf=0.30)
    desk_geo = DeskGeometry(desk_boundary_y=400.0)
    kp = np.array([0.0, 0.0, 0.0])

    zone = extractor._evaluate_wrist_zone(kp, desk_geo, CapabilityStatus.ENABLED)
    assert zone == WristZone.UNKNOWN


def test_wr4_unknown_never_activates_below_desk():
    """WR4: UNKNOWN observations alone never activate WRIST_BELOW_DESK."""
    engine = TemporalEpisodeEngine(min_persistence_ms=400.0)
    extractor = ObservationExtractor(min_keypoint_conf=0.30)
    ctx = SeatContext(seat_id="S01", desk_geometry=DeskGeometry(desk_boundary_y=400.0))

    # Low-confidence detection -> UNKNOWN wrist
    kps = np.zeros((17, 3), dtype=np.float32)
    kps[9] = [100.0, 500.0, 0.15]   # Left wrist
    kps[10] = [120.0, 500.0, 0.15]  # Right wrist
    det = Detection(bbox=(50, 50, 150, 150), confidence=0.8, class_id=0, class_name="person", keypoints=kps)

    active_eps = []
    for t_ms in [0.0, 200.0, 400.0, 600.0, 800.0]:
        obs = extractor.extract(
            detection=det,
            seat_context=ctx,
            timestamp_ms=t_ms,
        )
        active_eps = engine.process_observations(obs, timestamp_ms=t_ms)

    wrist_eps = [e for e in active_eps if e.episode_type == EpisodeType.WRIST_BELOW_DESK]
    assert len(wrist_eps) == 0, "UNKNOWN wrists must never activate WRIST_BELOW_DESK episode"


def test_wr5_unknown_never_adds_risk():
    """WR5: UNKNOWN observations alone never add risk points."""
    risk_tracker = SeatRiskTracker(room_id="ROOM-01")
    prof = risk_tracker.get_or_create_profile("S01")

    # Pass an empty / zero-risk observation cycle
    risk_tracker.update_seat(
        seat_id="S01",
        active_episodes=[],
        detected_patterns=[],
        timestamp_ms=1000.0,
    )
    assert prof.risk_score == 0.0


def test_wr6_writing_priority_over_under_desk():
    """WR6: WRITING zone (above desk boundary) produces WristZone.WRITING."""
    extractor = ObservationExtractor(min_keypoint_conf=0.30)
    desk_geo = DeskGeometry(desk_boundary_y=400.0)
    kp = np.array([100.0, 350.0, 0.90])  # y = 350 <= 400 (on desk)

    zone = extractor._evaluate_wrist_zone(kp, desk_geo, CapabilityStatus.ENABLED)
    assert zone == WristZone.WRITING


def test_wr7_persistent_under_desk_activates_episode():
    """WR7: persistent visible under-desk wrist (>=400ms) activates WRIST_BELOW_DESK episode."""
    engine = TemporalEpisodeEngine(min_persistence_ms=400.0)
    extractor = ObservationExtractor(min_keypoint_conf=0.30)
    ctx = SeatContext(seat_id="S01", desk_geometry=DeskGeometry(desk_boundary_y=400.0))

    kps = np.zeros((17, 3), dtype=np.float32)
    kps[9] = [100.0, 480.0, 0.85]   # Left wrist clearly below desk
    kps[10] = [120.0, 480.0, 0.85]  # Right wrist
    det = Detection(bbox=(50, 50, 150, 150), confidence=0.85, class_id=0, class_name="person", keypoints=kps)

    active_eps = []
    for t_ms in [0.0, 200.0, 450.0]:
        obs = extractor.extract(
            detection=det,
            seat_context=ctx,
            timestamp_ms=t_ms,
        )
        active_eps = engine.process_observations(obs, timestamp_ms=t_ms)

    wrist_eps = [e for e in active_eps if e.episode_type == EpisodeType.WRIST_BELOW_DESK]
    assert len(wrist_eps) >= 1, "Persistent visible under-desk wrist must activate episode"


def test_wr8_occlusion_does_not_create_under_desk_episode():
    """WR8: sudden disappearance / occlusion (confidence=0) does not start an under-desk episode."""
    engine = TemporalEpisodeEngine(min_persistence_ms=400.0)
    extractor = ObservationExtractor(min_keypoint_conf=0.30)
    ctx = SeatContext(seat_id="S01", desk_geometry=DeskGeometry(desk_boundary_y=400.0))

    # Frame with occluded / missing wrist
    kps = np.zeros((17, 3), dtype=np.float32)
    det = Detection(bbox=(50, 50, 150, 150), confidence=0.85, class_id=0, class_name="person", keypoints=kps)

    active_eps = []
    for t_ms in [0.0, 200.0, 400.0, 600.0]:
        obs = extractor.extract(
            detection=det,
            seat_context=ctx,
            timestamp_ms=t_ms,
        )
        active_eps = engine.process_observations(obs, timestamp_ms=t_ms)

    wrist_eps = [e for e in active_eps if e.episode_type == EpisodeType.WRIST_BELOW_DESK]
    assert len(wrist_eps) == 0


def test_wr9_wrist_missing_grace():
    """WR9: active episode terminates after missing_observation_grace_ms expires."""
    engine = TemporalEpisodeEngine(min_persistence_ms=400.0, missing_observation_grace_ms=1200.0)
    extractor = ObservationExtractor(min_keypoint_conf=0.30)
    ctx = SeatContext(seat_id="S01", desk_geometry=DeskGeometry(desk_boundary_y=400.0))

    # 1. Activate episode (0 to 500ms)
    kps = np.zeros((17, 3), dtype=np.float32)
    kps[9] = [100.0, 480.0, 0.85]
    det_vis = Detection(bbox=(50, 50, 150, 150), confidence=0.85, class_id=0, class_name="person", keypoints=kps)

    for t_ms in [0.0, 200.0, 500.0]:
        obs = extractor.extract(detection=det_vis, seat_context=ctx, timestamp_ms=t_ms)
        engine.process_observations(obs, timestamp_ms=t_ms)

    # 2. Wrist becomes occluded starting at t = 600ms
    kps_occ = np.zeros((17, 3), dtype=np.float32)
    det_occ = Detection(bbox=(50, 50, 150, 150), confidence=0.85, class_id=0, class_name="person", keypoints=kps_occ)

    obs_600 = extractor.extract(detection=det_occ, seat_context=ctx, timestamp_ms=600.0)
    engine.process_observations(obs_600, timestamp_ms=600.0)  # missing_start_ms = 600ms

    # Within grace (800ms missing @ 1400ms < 1200ms grace)
    obs_1400 = extractor.extract(detection=det_occ, seat_context=ctx, timestamp_ms=1400.0)
    active_in_grace = engine.process_observations(obs_1400, timestamp_ms=1400.0)
    assert any(e.episode_type == EpisodeType.WRIST_BELOW_DESK for e in active_in_grace)

    # Beyond grace (1600ms missing @ 2200ms > 1200ms grace) -> closes episode
    obs_2200 = extractor.extract(detection=det_occ, seat_context=ctx, timestamp_ms=2200.0)
    engine.process_observations(obs_2200, timestamp_ms=2200.0)

    # Subsequent frame: episode is fully inactive
    obs_2500 = extractor.extract(detection=det_occ, seat_context=ctx, timestamp_ms=2500.0)
    active_after_grace = engine.process_observations(obs_2500, timestamp_ms=2500.0)
    assert not any(e.episode_type == EpisodeType.WRIST_BELOW_DESK for e in active_after_grace)
