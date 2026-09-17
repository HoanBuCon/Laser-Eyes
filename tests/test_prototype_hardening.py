"""Prototype Detection Hardening Regression Test Suite (P1 - P10).

Validates all 10 Hardening Verification Cases for VIGIL AI Prototype:
- CASE P1: Seat Occupancy Occlusion Protection (OCCLUDED -> never SEAT_EMPTY / SEAT_LEFT)
- CASE P2: True Empty Seat Timeout (Persistent EMPTY -> produces SEAT_EMPTY / SEAT_LEFT)
- CASE P3: Real Multi-Person Count Propagation (0, 1, 2+ -> PERSON_COUNT_NEAR_SEAT)
- CASE P4: Brief Multi-Person Presence Filtering (< 2.5s -> NO MULTI_PERSON_DWELL)
- CASE P5: Head Turn without Neighbor Gating (HEAD_TURN_LEFT without left neighbor -> NO REPEATED_NEIGHBOR_GLANCE)
- CASE P6: Valid Neighbor Gating (HEAD_TURN_LEFT with valid left neighbor -> REPEATED_NEIGHBOR_GLANCE)
- CASE P7: Missing Observation Grace Timeout (> 1200ms missing -> graceful close)
- CASE P8: Missing Observation Short Gap Recovery (< 1200ms gap -> continuous episode)
- CASE P9: Normal Writing Guard (Head down + wrist in writing zone -> Normal risk)
- CASE P10: Unmapped Person Isolation (Unmapped detection -> no Seat risk profile)
"""

from __future__ import annotations

import numpy as np
import pytest

from classroom_monitor.behavior_pattern_engine import BehaviorPatternEngine, PatternType
from classroom_monitor.models import Detection
from classroom_monitor.observation_extractor import (
    ObservationExtractor,
    ObservationType,
    WristZone,
)
from classroom_monitor.scene_context import (
    CapabilityStatus,
    DeskGeometry,
    SeatCapabilities,
    SeatContext,
    SeatGraph,
    SeatNeighbors,
)
from classroom_monitor.seat_manager import (
    SeatDefinition,
    SeatManager,
    SeatOccupancy,
    SeatState,
)
from classroom_monitor.seat_risk_tracker import RiskState, SeatRiskTracker
from classroom_monitor.temporal_episode_engine import (
    EpisodeState,
    EpisodeType,
    TemporalEpisode,
    TemporalEpisodeEngine,
)


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
# CASE P1: Seat Occupancy Occlusion Protection
# ==============================================================================
def test_case_p01_occupancy_occlusion_protection():
    """Case P1: OCCLUDED state must NOT become EMPTY or trigger SEAT_EMPTY / SEAT_LEFT."""
    extractor = ObservationExtractor()
    ep_engine = TemporalEpisodeEngine(min_persistence_ms=500.0)
    ctx = SeatContext(seat_id="SEAT-01")

    # Simulate 3 seconds where detection is None (e.g. proctor walks in front)
    # but SeatManager marks occupancy as OCCLUDED
    active_episodes = []
    for step in range(30):
        t_ms = step * 100.0
        obs = extractor.extract(
            detection=None,
            seat_context=ctx,
            timestamp_ms=t_ms,
            occupancy_state=SeatState.OCCLUDED,
            nearby_person_count=1,
        )

        occ_obs = next(o for o in obs if o.observation_type == ObservationType.SEAT_OCCUPANCY.value)
        assert occ_obs.value == SeatState.OCCLUDED

        eps = ep_engine.process_observations(obs, t_ms)
        active_episodes.extend(eps)

    # Must NOT form any SEAT_EMPTY or SEAT_LEFT episode
    empty_eps = [
        e for e in (active_episodes + ep_engine.completed_episodes)
        if e.episode_type in (EpisodeType.SEAT_EMPTY.value, EpisodeType.SEAT_LEFT.value)
    ]
    assert len(empty_eps) == 0


# ==============================================================================
# CASE P2: True Empty Seat Timeout
# ==============================================================================
def test_case_p02_true_empty_seat_timeout():
    """Case P2: Prolonged true EMPTY state triggers SEAT_EMPTY episode."""
    extractor = ObservationExtractor()
    ep_engine = TemporalEpisodeEngine(min_persistence_ms=500.0)
    ctx = SeatContext(seat_id="SEAT-01")

    # Simulate 4 seconds of genuine EMPTY state
    last_eps = []
    for step in range(40):
        t_ms = step * 100.0
        obs = extractor.extract(
            detection=None,
            seat_context=ctx,
            timestamp_ms=t_ms,
            occupancy_state=SeatState.EMPTY,
            nearby_person_count=0,
        )
        last_eps = ep_engine.process_observations(obs, t_ms)

    # Check active or completed episodes
    all_eps = last_eps + ep_engine.completed_episodes
    empty_eps = [e for e in all_eps if e.episode_type == EpisodeType.SEAT_EMPTY.value]
    assert len(empty_eps) > 0
    assert any(e.state in (EpisodeState.ACTIVE, EpisodeState.ENDED) for e in empty_eps)


# ==============================================================================
# CASE P3: Real Multi-Person Count Propagation
# ==============================================================================
def test_case_p03_real_multi_person_count_propagation():
    """Case P3: Real person counts (0, 1, 2, 3) propagate accurately to PERSON_COUNT_NEAR_SEAT."""
    extractor = ObservationExtractor()
    ctx = SeatContext(seat_id="SEAT-01")

    for count in [0, 1, 2, 3, 5]:
        det = _create_mock_detection() if count > 0 else None
        obs = extractor.extract(
            detection=det,
            seat_context=ctx,
            timestamp_ms=1000.0,
            occupancy_state=SeatState.OCCUPIED if count == 1 else (SeatState.MULTIPLE_PERSON if count > 1 else SeatState.EMPTY),
            nearby_person_count=count,
        )

        cnt_obs = next(o for o in obs if o.observation_type == ObservationType.PERSON_COUNT_NEAR_SEAT.value)
        assert cnt_obs.value == count


# ==============================================================================
# CASE P4: Brief Multi-Person Presence Filtering
# ==============================================================================
def test_case_p04_brief_multi_person_no_dwell():
    """Case P4: Multi-person count = 2 for < 2.5s does not trigger MULTI_PERSON_DWELL_NEAR_SEAT."""
    extractor = ObservationExtractor()
    ep_engine = TemporalEpisodeEngine(min_persistence_ms=400.0)
    pat_engine = BehaviorPatternEngine(multi_person_dwell_ms=2500.0)
    ctx = SeatContext(seat_id="SEAT-01")

    # Scenario A: Brief multi-person for 1.5 seconds (15 frames @ 100ms)
    for step in range(15):
        t_ms = step * 100.0
        obs = extractor.extract(
            detection=_create_mock_detection(),
            seat_context=ctx,
            timestamp_ms=t_ms,
            occupancy_state=SeatState.MULTIPLE_PERSON,
            nearby_person_count=2,
        )
        eps = ep_engine.process_observations(obs, t_ms)
        pats = pat_engine.ingest_episodes(eps, ep_engine.completed_episodes, ctx, t_ms)
        assert not any(p.pattern_type == PatternType.MULTI_PERSON_DWELL_NEAR_SEAT.value for p in pats)

    # Scenario B: Multi-person maintained for >= 2.5s (35 frames @ 100ms = 3.5s total)
    dwell_patterns = []
    for step in range(15, 36):
        t_ms = step * 100.0
        obs = extractor.extract(
            detection=_create_mock_detection(),
            seat_context=ctx,
            timestamp_ms=t_ms,
            occupancy_state=SeatState.MULTIPLE_PERSON,
            nearby_person_count=2,
        )
        eps = ep_engine.process_observations(obs, t_ms)
        pats = pat_engine.ingest_episodes(eps, ep_engine.completed_episodes, ctx, t_ms)
        dwell_patterns.extend([p for p in pats if p.pattern_type == PatternType.MULTI_PERSON_DWELL_NEAR_SEAT.value])

    assert len(dwell_patterns) > 0


# ==============================================================================
# CASE P5: Head Turn without Neighbor Gating
# ==============================================================================
def test_case_p05_head_turn_no_neighbor_gating():
    """Case P5: Repeated HEAD_TURN_LEFT on a seat without left neighbor must NOT trigger REPEATED_NEIGHBOR_GLANCE."""
    extractor = ObservationExtractor()
    ep_engine = TemporalEpisodeEngine(min_persistence_ms=300.0, release_hysteresis_ms=200.0)
    pat_engine = BehaviorPatternEngine(glance_rolling_window_ms=20000.0, min_glance_episodes=2)

    # Seat has NO left neighbor (e.g., aisle/wall)
    ctx = SeatContext(
        seat_id="SEAT-01",
        neighbors=SeatNeighbors(left_neighbor_id=None, right_neighbor_id="SEAT-02"),
    )

    # Repeatedly turn left: 2 distinct episodes
    for ep_round in range(2):
        base_t = ep_round * 3000.0
        # Turn left for 600ms
        for step in range(7):
            t_ms = base_t + step * 100.0
            det = _create_mock_detection(nose_xy=(140, 120), l_ear_xy=(160, 120), r_ear_xy=(190, 120))
            obs = extractor.extract(det, ctx, t_ms)
            eps = ep_engine.process_observations(obs, t_ms)
            pats = pat_engine.ingest_episodes(eps, ep_engine.completed_episodes, ctx, t_ms)
            glances = [p for p in pats if p.pattern_type == PatternType.REPEATED_NEIGHBOR_GLANCE.value]
            assert len(glances) == 0

        # Return to center
        for step in range(7, 15):
            t_ms = base_t + step * 100.0
            det = _create_mock_detection(nose_xy=(175, 120), l_ear_xy=(160, 120), r_ear_xy=(190, 120))
            obs = extractor.extract(det, ctx, t_ms)
            eps = ep_engine.process_observations(obs, t_ms)
            pats = pat_engine.ingest_episodes(eps, ep_engine.completed_episodes, ctx, t_ms)
            glances = [p for p in pats if p.pattern_type == PatternType.REPEATED_NEIGHBOR_GLANCE.value]
            assert len(glances) == 0


# ==============================================================================
# CASE P6: Valid Neighbor Gating
# ==============================================================================
def test_case_p06_valid_neighbor_gating():
    """Case P6: Repeated HEAD_TURN_LEFT with a valid left neighbor correctly triggers REPEATED_NEIGHBOR_GLANCE."""
    extractor = ObservationExtractor()
    ep_engine = TemporalEpisodeEngine(min_persistence_ms=300.0, release_hysteresis_ms=200.0)
    pat_engine = BehaviorPatternEngine(glance_rolling_window_ms=20000.0, min_glance_episodes=2)

    # Seat has VALID left neighbor
    ctx = SeatContext(
        seat_id="SEAT-02",
        neighbors=SeatNeighbors(left_neighbor_id="SEAT-01", right_neighbor_id="SEAT-03"),
        capabilities=SeatCapabilities(pairwise_relation=CapabilityStatus.ENABLED),
    )

    detected_glances = []

    # Episode 1: Turn Left (0 to 600ms)
    for t in [0.0, 100.0, 200.0, 300.0, 400.0, 500.0, 600.0]:
        det = _create_mock_detection(nose_xy=(140, 120), l_ear_xy=(160, 120), r_ear_xy=(190, 120))
        obs = extractor.extract(det, ctx, t)
        eps = ep_engine.process_observations(obs, t)
        pat_engine.ingest_episodes(eps, ep_engine.completed_episodes, ctx, t)

    # Return center (700 to 1500ms)
    for t in [700.0, 900.0, 1200.0, 1500.0]:
        det = _create_mock_detection(nose_xy=(175, 120))
        obs = extractor.extract(det, ctx, t)
        eps = ep_engine.process_observations(obs, t)
        pat_engine.ingest_episodes(eps, ep_engine.completed_episodes, ctx, t)

    # Episode 2: Turn Left Again (1600 to 2200ms)
    for t in [1600.0, 1800.0, 2000.0, 2200.0]:
        det = _create_mock_detection(nose_xy=(140, 120), l_ear_xy=(160, 120), r_ear_xy=(190, 120))
        obs = extractor.extract(det, ctx, t)
        eps = ep_engine.process_observations(obs, t)
        pats = pat_engine.ingest_episodes(eps, ep_engine.completed_episodes, ctx, t)
        detected_glances.extend([p for p in pats if p.pattern_type == PatternType.REPEATED_NEIGHBOR_GLANCE.value])

    assert len(detected_glances) > 0
    assert detected_glances[0].target_neighbor_id == "SEAT-01"


# ==============================================================================
# CASE P7: Missing Head Observations Grace Timeout
# ==============================================================================
def test_case_p07_missing_head_observations_grace_timeout():
    """Case P7: Missing head observations > 1200ms grace gracefully closes active episode."""
    extractor = ObservationExtractor()
    ep_engine = TemporalEpisodeEngine(
        min_persistence_ms=400.0,
        release_hysteresis_ms=300.0,
        missing_observation_grace_ms=1200.0,
    )
    ctx = SeatContext(seat_id="SEAT-01")

    # Start turn right (0 to 600ms)
    active_eps = []
    for step in range(7):
        t_ms = step * 100.0
        det = _create_mock_detection(nose_xy=(210, 120), l_ear_xy=(160, 120), r_ear_xy=(190, 120))
        obs = extractor.extract(det, ctx, t_ms)
        active_eps = ep_engine.process_observations(obs, t_ms)

    assert any(e.episode_type == EpisodeType.HEAD_TURN_RIGHT.value and e.state == EpisodeState.ACTIVE for e in active_eps)

    # Now drop head observations for 1500ms (exceeding 1200ms grace)
    # (e.g. keypoint conf drops or detection becomes None)
    latest_active = []
    for step in range(7, 25):
        t_ms = step * 100.0
        obs = extractor.extract(None, ctx, t_ms, occupancy_state=SeatState.OCCUPIED)
        latest_active = ep_engine.process_observations(obs, t_ms)

    # Episode should be CLOSED / COMPLETED, not remaining active indefinitely
    active_head_eps = [
        e for e in latest_active
        if e.episode_type == EpisodeType.HEAD_TURN_RIGHT.value
    ]
    assert len(active_head_eps) == 0

    completed_head_eps = [
        e for e in ep_engine.completed_episodes
        if e.episode_type == EpisodeType.HEAD_TURN_RIGHT.value
    ]
    assert len(completed_head_eps) > 0


# ==============================================================================
# CASE P8: Missing Head Observations Short Gap Recovery
# ==============================================================================
def test_case_p08_missing_head_observations_short_gap_recovery():
    """Case P8: Short missing gap (< 1200ms) preserves active episode without spurious reset."""
    extractor = ObservationExtractor()
    ep_engine = TemporalEpisodeEngine(
        min_persistence_ms=400.0,
        release_hysteresis_ms=300.0,
        missing_observation_grace_ms=1200.0,
    )
    ctx = SeatContext(seat_id="SEAT-01")

    # Start turn right (0 to 600ms)
    for step in range(7):
        t_ms = step * 100.0
        det = _create_mock_detection(nose_xy=(210, 120), l_ear_xy=(160, 120), r_ear_xy=(190, 120))
        obs = extractor.extract(det, ctx, t_ms)
        ep_engine.process_observations(obs, t_ms)

    # Short drop for 300ms (3 frames @ 100ms)
    for step in range(7, 10):
        t_ms = step * 100.0
        obs = extractor.extract(None, ctx, t_ms, occupancy_state=SeatState.OCCUPIED)
        ep_engine.process_observations(obs, t_ms)

    # Resume valid turn right (1000 to 1500ms)
    latest_eps = []
    for step in range(10, 16):
        t_ms = step * 100.0
        det = _create_mock_detection(nose_xy=(210, 120), l_ear_xy=(160, 120), r_ear_xy=(190, 120))
        obs = extractor.extract(det, ctx, t_ms)
        latest_eps = ep_engine.process_observations(obs, t_ms)

    # There should only be 1 single active episode
    active_head_eps = [
        e for e in latest_eps
        if e.episode_type == EpisodeType.HEAD_TURN_RIGHT.value
    ]
    assert len(active_head_eps) == 1


# ==============================================================================
# CASE P9: Normal Writing Guard
# ==============================================================================
def test_case_p09_normal_writing_guard():
    """Case P9: Head pitched down + wrists on desk/writing zone stays in NORMAL risk band (< 30)."""
    extractor = ObservationExtractor()
    ep_engine = TemporalEpisodeEngine()
    pat_engine = BehaviorPatternEngine()
    risk_tracker = SeatRiskTracker(room_id="ROOM-101")

    desk_geo = DeskGeometry(desk_boundary_y=250.0)
    ctx = SeatContext(seat_id="SEAT-01", desk_geometry=desk_geo)

    for step in range(25):
        t_ms = step * 100.0
        det = _create_mock_detection(
            nose_xy=(175, 170),  # Pitched down into exam sheet
            l_wrist_xy=(150, 220), # Above desk boundary 250
            r_wrist_xy=(190, 220),
        )
        obs = extractor.extract(det, ctx, t_ms, occupancy_state=SeatState.OCCUPIED)
        eps = ep_engine.process_observations(obs, t_ms)
        pats = pat_engine.ingest_episodes(eps, ep_engine.completed_episodes, ctx, t_ms)
        event = risk_tracker.update_seat("SEAT-01", eps, pats, t_ms, det)
        assert event is None

    prof = risk_tracker.get_or_create_profile("SEAT-01")
    assert prof.risk_score < 30.0
    assert prof.current_state == RiskState.NORMAL.value


# ==============================================================================
# CASE P10: Unmapped Person Isolation
# ==============================================================================
def test_case_p10_unmapped_person_isolation():
    """Case P10: Unmapped person detection does not create Seat risk profile or corrupt Seat state."""
    seat_def = SeatDefinition(
        seat_id="SEAT-01",
        room_id="ROOM-101",
        seat_code="SEAT-01",
        polygon=np.array([[100, 100], [300, 100], [300, 300], [100, 300]], dtype=np.float32),
    )
    seat_mgr = SeatManager(room_id="ROOM-101")
    seat_mgr.load_seats([seat_def])
    risk_tracker = SeatRiskTracker(room_id="ROOM-101")

    # Person standing in aisle far away from SEAT-01 polygon
    aisle_det = _create_mock_detection(
        bbox=(800, 500, 950, 700),
        nose_xy=(875, 520),
    )

    mapped_seats, unmapped_dets = seat_mgr.map_detections_to_seats([aisle_det], timestamp_ms=1000.0)

    # Aisle person is unmapped
    assert len(unmapped_dets) == 1
    assert "SEAT-01" in mapped_seats
    assert mapped_seats["SEAT-01"] is None

    # Risk tracker has no profile for aisle person
    profiles = risk_tracker.profiles
    assert "SEAT-01" not in profiles or profiles["SEAT-01"].risk_score == 0.0
