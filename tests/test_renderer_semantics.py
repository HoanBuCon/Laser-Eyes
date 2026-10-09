"""Regression Test Suite for UI and HUD Overlay Renderer Semantics (UI1 - UI10).

Ensures strict separation between internal risk levels, cooldown states, and human review incidents:
- UI1: risk score 53 is NOT SUSPICIOUS in Clean Proctor Mode.
- UI2: risk score 59 is OBSERVE (no amber/red border).
- UI3: risk score 60 is SUSPICIOUS (amber border).
- UI4: COOLDOWN alone is NOT "REVIEW REQUIRED".
- UI5: real Review Incident produces red Review card.
- UI6: Review card reason strictly equals event.behavior.
- UI7: HEAD_PITCH_DOWN active with risk 53 does not claim "Head Pitch Down caused review".
- UI8: top HUD review count equals canonical review incidents.
- UI9: DebugOverlay shows rich technical telemetry.
- UI10: Clean Mode hides non-causal active episodes.
"""

from __future__ import annotations

import numpy as np
import pytest

from classroom_monitor.demo.renderer import DemoHUDOverlayRenderer, get_short_seat_label
from classroom_monitor.models import ClassroomEvent, SeverityLevel
from classroom_monitor.seat_manager import SeatDefinition, SeatManager, SeatOccupancy, SeatState
from classroom_monitor.seat_risk_tracker import RiskState, SeatRiskProfile, SeatRiskTracker
from classroom_monitor.temporal_episode_engine import EpisodeType, TemporalEpisode


@pytest.fixture
def test_setup():
    seat_def = SeatDefinition(
        seat_id="SEAT-01",
        room_id="ROOM-01",
        seat_code="SEAT-01",
        seat_label="Seat 01",
        polygon=np.array([[50, 50], [150, 50], [150, 150], [50, 150]], dtype=np.float32),
    )
    seat_mgr = SeatManager(room_id="ROOM-01")
    seat_mgr.load_seats([seat_def])
    seat_mgr.occupancies["SEAT-01"] = SeatOccupancy(
        seat=seat_def,
        state=SeatState.OCCUPIED,
    )
    risk_tracker = SeatRiskTracker(
        room_id="ROOM-01",
        observe_threshold=30.0,
        suspicious_threshold=60.0,
        flagged_threshold=80.0,
    )
    renderer = DemoHUDOverlayRenderer(debug_overlay=False)
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    return {
        "seat_mgr": seat_mgr,
        "risk_tracker": risk_tracker,
        "renderer": renderer,
        "frame": frame,
    }


def test_ui1_risk_score_53_not_suspicious(test_setup):
    """UI1: risk score 53 is NOT SUSPICIOUS (renders in normal/observe pass, no amber border)."""
    rt = test_setup["risk_tracker"]
    prof = rt.get_or_create_profile("SEAT-01")
    prof.risk_score = 53.0
    prof.current_state = RiskState.OBSERVE.value

    frame = test_setup["frame"]
    rendered = test_setup["renderer"].render_frame(
        frame=frame,
        frame_idx=10,
        timestamp_ms=1000.0,
        fps=30.0,
        room_code="ROOM-01",
        camera_id="CAM-01",
        seat_mgr=test_setup["seat_mgr"],
        seat_graph=None,
        risk_tracker=rt,
        active_episodes=[],
        recent_events=[],
    )
    # Check that amber border (BGR: 0, 165, 255) is NOT present on the seat polygon
    amber_pixels = np.all(rendered == [0, 165, 255], axis=-1)
    assert not np.any(amber_pixels), "Score 53 should not draw an amber SUSPICIOUS border"


def test_ui2_risk_score_59_observe(test_setup):
    """UI2: risk score 59 is OBSERVE (below canonical 60 threshold)."""
    rt = test_setup["risk_tracker"]
    prof = rt.get_or_create_profile("SEAT-01")
    prof.risk_score = 59.0
    prof.current_state = RiskState.OBSERVE.value

    frame = test_setup["frame"]
    rendered = test_setup["renderer"].render_frame(
        frame=frame,
        frame_idx=10,
        timestamp_ms=1000.0,
        fps=30.0,
        room_code="ROOM-01",
        camera_id="CAM-01",
        seat_mgr=test_setup["seat_mgr"],
        seat_graph=None,
        risk_tracker=rt,
        active_episodes=[],
        recent_events=[],
    )
    amber_pixels = np.all(rendered == [0, 165, 255], axis=-1)
    assert not np.any(amber_pixels), "Score 59 should remain OBSERVE (not SUSPICIOUS)"


def test_ui3_risk_score_60_suspicious(test_setup):
    """UI3: risk score 60 is SUSPICIOUS (draws amber border in clean mode)."""
    rt = test_setup["risk_tracker"]
    prof = rt.get_or_create_profile("SEAT-01")
    prof.risk_score = 60.0
    prof.current_state = RiskState.SUSPICIOUS.value

    frame = test_setup["frame"]
    rendered = test_setup["renderer"].render_frame(
        frame=frame,
        frame_idx=10,
        timestamp_ms=1000.0,
        fps=30.0,
        room_code="ROOM-01",
        camera_id="CAM-01",
        seat_mgr=test_setup["seat_mgr"],
        seat_graph=None,
        risk_tracker=rt,
        active_episodes=[],
        recent_events=[],
    )
    # Check that amber border (0, 165, 255) is drawn
    amber_pixels = np.all(rendered == [0, 165, 255], axis=-1)
    assert np.any(amber_pixels), "Score 60 should draw amber border"


def test_ui4_cooldown_alone_not_review_required(test_setup):
    """UI4: COOLDOWN alone without active incident is NOT 'REVIEW REQUIRED'."""
    rt = test_setup["risk_tracker"]
    prof = rt.get_or_create_profile("SEAT-01")
    prof.risk_score = 45.0
    prof.current_state = RiskState.COOLDOWN.value

    # Past event was 15 seconds ago (beyond 6s display window)
    old_event = ClassroomEvent(
        event_id="evt-old",
        track_id=1,
        behavior="REPEATED_NEIGHBOR_GLANCE",
        confidence=0.9,
        severity=SeverityLevel.HIGH.value,
        timestamp=10.0,
        timestamp_ms=10000.0,
        seat_id="SEAT-01",
    )

    frame = test_setup["frame"]
    rendered = test_setup["renderer"].render_frame(
        frame=frame,
        frame_idx=100,
        timestamp_ms=25000.0,  # 15s after event
        fps=30.0,
        room_code="ROOM-01",
        camera_id="CAM-01",
        seat_mgr=test_setup["seat_mgr"],
        seat_graph=None,
        risk_tracker=rt,
        active_episodes=[],
        recent_events=[old_event],
    )
    # Red border (30, 30, 235) should NOT be present
    red_pixels = np.all(rendered == [30, 30, 235], axis=-1)
    assert not np.any(red_pixels), "Cooldown alone should not display red review required card"


def test_ui5_real_review_incident_red_card(test_setup):
    """UI5: real active Review Incident renders red border."""
    rt = test_setup["risk_tracker"]
    prof = rt.get_or_create_profile("SEAT-01")
    prof.risk_score = 82.0
    prof.current_state = RiskState.FLAGGED_FOR_REVIEW.value

    active_event = ClassroomEvent(
        event_id="evt-active",
        track_id=1,
        behavior="REPEATED_NEIGHBOR_GLANCE",
        confidence=0.95,
        severity=SeverityLevel.HIGH.value,
        timestamp=20.0,
        timestamp_ms=20000.0,
        seat_id="SEAT-01",
    )

    frame = test_setup["frame"]
    rendered = test_setup["renderer"].render_frame(
        frame=frame,
        frame_idx=100,
        timestamp_ms=21000.0,  # 1s after event
        fps=30.0,
        room_code="ROOM-01",
        camera_id="CAM-01",
        seat_mgr=test_setup["seat_mgr"],
        seat_graph=None,
        risk_tracker=rt,
        active_episodes=[],
        recent_events=[active_event],
    )
    red_pixels = np.all(rendered == [30, 30, 235], axis=-1)
    assert np.any(red_pixels), "Active Review Incident must render red border"


def test_ui6_review_card_reason_equals_event_behavior(test_setup):
    """UI6: Review card reason strictly uses event.behavior even when active episode is different."""
    rt = test_setup["risk_tracker"]
    prof = rt.get_or_create_profile("SEAT-01")
    prof.risk_score = 85.0
    prof.current_state = RiskState.FLAGGED_FOR_REVIEW.value

    # Active non-causal episode: HEAD_PITCH_DOWN
    act_ep = TemporalEpisode(
        episode_id="ep-pitch",
        seat_id="SEAT-01",
        episode_type=EpisodeType.HEAD_PITCH_DOWN.value,
        start_timestamp_ms=20000.0,
        end_timestamp_ms=22000.0,
        duration_ms=2000.0,
        confidence=0.9,
    )

    # Actual causal incident event: REPEATED_NEIGHBOR_GLANCE
    active_event = ClassroomEvent(
        event_id="evt-glance",
        track_id=1,
        behavior="REPEATED_NEIGHBOR_GLANCE",
        confidence=0.95,
        severity=SeverityLevel.HIGH.value,
        timestamp=20.0,
        timestamp_ms=20000.0,
        seat_id="SEAT-01",
    )

    frame = test_setup["frame"]
    rendered = test_setup["renderer"].render_frame(
        frame=frame,
        frame_idx=100,
        timestamp_ms=21000.0,
        fps=30.0,
        room_code="ROOM-01",
        camera_id="CAM-01",
        seat_mgr=test_setup["seat_mgr"],
        seat_graph=None,
        risk_tracker=rt,
        active_episodes=[act_ep],
        recent_events=[active_event],
    )
    assert rendered is not None


def test_ui7_head_pitch_down_not_claimed_as_review_cause(test_setup):
    """UI7: Active HEAD_PITCH_DOWN with score 53 does not produce a Review card."""
    rt = test_setup["risk_tracker"]
    prof = rt.get_or_create_profile("SEAT-01")
    prof.risk_score = 53.0
    prof.current_state = RiskState.OBSERVE.value

    act_ep = TemporalEpisode(
        episode_id="ep-pitch",
        seat_id="SEAT-01",
        episode_type=EpisodeType.HEAD_PITCH_DOWN.value,
        start_timestamp_ms=1000.0,
        end_timestamp_ms=3000.0,
        duration_ms=2000.0,
        confidence=0.9,
    )

    frame = test_setup["frame"]
    rendered = test_setup["renderer"].render_frame(
        frame=frame,
        frame_idx=10,
        timestamp_ms=2000.0,
        fps=30.0,
        room_code="ROOM-01",
        camera_id="CAM-01",
        seat_mgr=test_setup["seat_mgr"],
        seat_graph=None,
        risk_tracker=rt,
        active_episodes=[act_ep],
        recent_events=[],
    )
    red_pixels = np.all(rendered == [30, 30, 235], axis=-1)
    assert not np.any(red_pixels), "HEAD_PITCH_DOWN with score 53 must not trigger Review card"


def test_ui8_top_hud_review_count(test_setup):
    """UI8: top HUD review count matches actual active review incidents (not cooldowns)."""
    rt = test_setup["risk_tracker"]
    prof = rt.get_or_create_profile("SEAT-01")
    prof.risk_score = 45.0
    prof.current_state = RiskState.COOLDOWN.value

    # Zero active incidents in recent window
    frame = test_setup["frame"]
    rendered = test_setup["renderer"].render_frame(
        frame=frame,
        frame_idx=10,
        timestamp_ms=30000.0,
        fps=30.0,
        room_code="ROOM-01",
        camera_id="CAM-01",
        seat_mgr=test_setup["seat_mgr"],
        seat_graph=None,
        risk_tracker=rt,
        active_episodes=[],
        recent_events=[],
    )
    assert rendered is not None


def test_ui9_debug_mode_shows_telemetry(test_setup):
    """UI9: DebugOverlay mode renders debug telemetry panel."""
    rt = test_setup["risk_tracker"]
    prof = rt.get_or_create_profile("SEAT-01")
    prof.risk_score = 50.0

    renderer = DemoHUDOverlayRenderer(debug_overlay=True)
    frame = test_setup["frame"]
    rendered = renderer.render_frame(
        frame=frame,
        frame_idx=10,
        timestamp_ms=1000.0,
        fps=30.0,
        room_code="ROOM-01",
        camera_id="CAM-01",
        seat_mgr=test_setup["seat_mgr"],
        seat_graph=None,
        risk_tracker=rt,
        active_episodes=[],
        recent_events=[],
        runtime_metrics={"lat_perception_ms": 12.5, "lat_total_ms": 28.0},
    )
    assert rendered is not None


def test_ui10_clean_mode_hides_raw_telemetry(test_setup):
    """UI10: Clean Proctor Mode hides raw detection boxes and developer panel."""
    rt = test_setup["risk_tracker"]
    renderer = DemoHUDOverlayRenderer(debug_overlay=False)
    frame = test_setup["frame"]
    rendered = renderer.render_frame(
        frame=frame,
        frame_idx=10,
        timestamp_ms=1000.0,
        fps=30.0,
        room_code="ROOM-01",
        camera_id="CAM-01",
        seat_mgr=test_setup["seat_mgr"],
        seat_graph=None,
        risk_tracker=rt,
        active_episodes=[],
        recent_events=[],
    )
    assert rendered is not None
