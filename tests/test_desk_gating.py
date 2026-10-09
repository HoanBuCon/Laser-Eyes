"""Test Suite for Desk Geometry Capability Gating (DG1 - DG5).

Ensures P0 safety rules for desk boundary & capability gating:
- DG1: boundary-only configuration classifies as CapabilityStatus.DEGRADED.
- DG2: degraded wrist observation does NOT contribute to Review Priority risk.
- DG3: degraded wrist CANNOT create BELOW_DESK_INTERACTION Review Incident / Pattern.
- DG4: polygon calibrated configuration classifies as CapabilityStatus.ENABLED and adds risk.
- DG5: unavailable desk geometry produces WristZone.UNKNOWN and zero risk.
"""

from __future__ import annotations

import numpy as np
import pytest

from classroom_monitor.behavior_pattern_engine import BehaviorPatternEngine, PatternType
from classroom_monitor.models import Detection
from classroom_monitor.observation_extractor import ObservationExtractor, WristZone
from classroom_monitor.scene_context import CapabilityStatus, DeskGeometry, SeatCapabilities, SeatContext
from classroom_monitor.seat_risk_tracker import SeatRiskTracker
from classroom_monitor.temporal_episode_engine import EpisodeState, EpisodeType, TemporalEpisode, TemporalEpisodeEngine


def test_dg1_boundary_only_is_degraded():
    """DG1: boundary-only configuration classifies as CapabilityStatus.DEGRADED."""
    ctx = SeatContext.from_dict(
        {
            "seat_id": "SEAT-01",
            "roi": [100, 100, 300, 400],
            "desk_boundary_y": 350.0,
        }
    )
    assert ctx.capabilities.desk_hand_interaction == CapabilityStatus.DEGRADED
    assert ctx.desk_geometry is not None
    assert ctx.desk_geometry.desk_boundary_y == 350.0
    assert ctx.desk_geometry.writing_zone_polygon is None


def test_dg2_degraded_wrist_does_not_contribute_risk():
    """DG2: degraded wrist observation does NOT contribute risk points to SeatRiskTracker."""
    risk_tracker = SeatRiskTracker(room_id="ROOM-01")
    ctx = SeatContext.from_dict(
        {
            "seat_id": "SEAT-01",
            "roi": [100, 100, 300, 400],
            "desk_boundary_y": 350.0,  # DEGRADED
        }
    )

    # Create a WRIST_BELOW_DESK episode with degraded metadata
    ep = TemporalEpisode(
        episode_id="ep-wrist-01",
        seat_id="SEAT-01",
        episode_type=EpisodeType.WRIST_BELOW_DESK.value,
        state=EpisodeState.ACTIVE,
        start_timestamp_ms=100.0,
        quality=1.0,
        confidence=1.0,
        metadata={"desk_capability": CapabilityStatus.DEGRADED.value},
    )

    event = risk_tracker.update_seat(
        seat_id="SEAT-01",
        active_episodes=[ep],
        detected_patterns=[],
        timestamp_ms=500.0,
        seat_context=ctx,
    )

    prof = risk_tracker.get_or_create_profile("SEAT-01")
    assert prof.risk_score == 0.0, "DEGRADED wrist must NOT add any risk score"
    assert event is None, "DEGRADED wrist alone must never generate a Review Incident"


def test_dg3_degraded_wrist_cannot_create_below_desk_pattern():
    """DG3: degraded wrist CANNOT create BELOW_DESK_INTERACTION BehaviorPattern."""
    pattern_engine = BehaviorPatternEngine()
    ctx = SeatContext.from_dict(
        {
            "seat_id": "SEAT-01",
            "roi": [100, 100, 300, 400],
            "desk_boundary_y": 350.0,  # DEGRADED
        }
    )

    ep = TemporalEpisode(
        episode_id="ep-wrist-02",
        seat_id="SEAT-01",
        episode_type=EpisodeType.WRIST_BELOW_DESK.value,
        state=EpisodeState.ACTIVE,
        start_timestamp_ms=0.0,
        duration_ms=3000.0,
        quality=1.0,
        confidence=1.0,
    )

    patterns = pattern_engine.ingest_episodes(
        active_episodes=[ep],
        completed_episodes=[],
        seat_context=ctx,
        timestamp_ms=3000.0,
    )

    below_patterns = [p for p in patterns if p.pattern_type == PatternType.BELOW_DESK_INTERACTION.value]
    assert len(below_patterns) == 0, "DEGRADED desk geometry must NEVER emit BELOW_DESK_INTERACTION pattern"


def test_dg4_polygon_calibrated_is_enabled_and_adds_risk():
    """DG4: polygon-calibrated configuration classifies as ENABLED and contributes risk."""
    ctx = SeatContext.from_dict(
        {
            "seat_id": "SEAT-01",
            "roi": [100, 100, 300, 400],
            "desk_polygon": [[100, 300], [300, 300], [300, 400], [100, 400]],
            "writing_zone_polygon": [[120, 310], [280, 310], [280, 370], [120, 370]],
        }
    )
    assert ctx.capabilities.desk_hand_interaction == CapabilityStatus.ENABLED

    risk_tracker = SeatRiskTracker(room_id="ROOM-01")
    ep = TemporalEpisode(
        episode_id="ep-wrist-03",
        seat_id="SEAT-01",
        episode_type=EpisodeType.WRIST_BELOW_DESK.value,
        state=EpisodeState.ACTIVE,
        start_timestamp_ms=100.0,
        quality=1.0,
        confidence=1.0,
        metadata={"desk_capability": CapabilityStatus.ENABLED.value},
    )

    risk_tracker.update_seat(
        seat_id="SEAT-01",
        active_episodes=[ep],
        detected_patterns=[],
        timestamp_ms=500.0,
        seat_context=ctx,
    )

    prof = risk_tracker.get_or_create_profile("SEAT-01")
    assert prof.risk_score > 0.0, "ENABLED polygon desk geometry must add risk when active"


def test_dg5_unavailable_produces_unknown():
    """DG5: unavailable desk geometry produces WristZone.UNKNOWN and zero risk."""
    ctx = SeatContext.from_dict({"seat_id": "SEAT-01", "roi": [100, 100, 300, 400]})
    assert ctx.capabilities.desk_hand_interaction == CapabilityStatus.DISABLED
    assert ctx.desk_geometry is None

    extractor = ObservationExtractor()
    kp = np.array([200.0, 380.0, 0.95])
    zone = extractor._evaluate_wrist_zone(kp, ctx.desk_geometry, ctx.capabilities.desk_hand_interaction)
    assert zone == WristZone.UNKNOWN
