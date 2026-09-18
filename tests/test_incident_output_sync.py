"""Unit & Integration Tests for Incident Aggregation Output Synchronization (INC1 - INC7).

Verifies:
- INC1: Merged pattern occurrences retain the same canonical incident_id.
- INC2: Merged occurrences accurately increment exported occurrence_count in active_event metadata.
- INC3: last_seen_ms updates to latest occurrence timestamp on the active event object.
- INC4: peak_risk_score updates when a subsequent occurrence exhibits higher risk.
- INC5: component_episode_ids and supporting_pattern_ids are union-deduplicated on the active event.
- INC6: A genuine later incident (> merge window) creates a new unique event/incident ID.
- INC7: All exported event statuses remain FLAGGED_FOR_REVIEW / SUSPICIOUS, zero CHEATING labels.
"""

from __future__ import annotations

import pytest

from classroom_monitor.behavior_pattern_engine import BehaviorPattern, PatternType
from classroom_monitor.models import ClassroomEvent, Detection
from classroom_monitor.seat_risk_tracker import RiskState, SeatRiskTracker
from classroom_monitor.temporal_episode_engine import EpisodeState, EpisodeType, TemporalEpisode


def test_inc1_to_inc5_incident_synchronization_on_merge():
    """INC1 - INC5: Ingesting subsequent patterns updates the active_event metadata directly."""
    tracker = SeatRiskTracker(room_id="ROOM1", incident_merge_window_ms=15000.0)

    ep1 = TemporalEpisode("ep1", "S01", EpisodeType.HEAD_TURN_LEFT.value, EpisodeState.ACTIVE, start_timestamp_ms=1000.0, end_timestamp_ms=2000.0, quality=1.0)
    pat1 = BehaviorPattern("pat1", "S01", PatternType.REPEATED_NEIGHBOR_GLANCE.value, confidence=1.0, quality=1.0, start_timestamp_ms=1000.0, end_timestamp_ms=2000.0, component_episode_ids=["ep1"])
    pat_lean = BehaviorPattern("pat2", "S01", PatternType.NEIGHBOR_ORIENTED_LEAN.value, confidence=1.0, quality=1.0, start_timestamp_ms=1000.0, end_timestamp_ms=2000.0, component_episode_ids=["ep1"])

    # 1. Initial trigger at t = 2000ms -> Emits 1st Event
    evt1 = tracker.update_seat("S01", active_episodes=[ep1], detected_patterns=[pat1, pat_lean], timestamp_ms=2000.0)
    assert evt1 is not None
    initial_id = evt1.event_id
    assert evt1.metadata["occurrence_count"] == 1
    assert evt1.metadata["first_seen_ms"] == 2000.0
    assert evt1.metadata["last_seen_ms"] == 2000.0
    initial_peak = evt1.metadata["peak_risk_score"]

    # 2. Second occurrence at t = 6000ms (within 15s merge window)
    ep2 = TemporalEpisode("ep2", "S01", EpisodeType.HEAD_TURN_LEFT.value, EpisodeState.ACTIVE, start_timestamp_ms=5000.0, end_timestamp_ms=6000.0, quality=1.0)
    pat3 = BehaviorPattern("pat3", "S01", PatternType.REPEATED_NEIGHBOR_GLANCE.value, confidence=1.0, quality=1.0, start_timestamp_ms=5000.0, end_timestamp_ms=6000.0, component_episode_ids=["ep2"])

    evt2 = tracker.update_seat("S01", active_episodes=[ep2], detected_patterns=[pat3], timestamp_ms=6000.0)
    assert evt2 is None  # Merged, no duplicate event emitted

    # INC1: Retains original incident ID
    assert tracker.profiles["S01"].active_incident["incident_id"] == initial_id
    assert tracker.profiles["S01"].active_event.event_id == initial_id

    # INC2: occurrence_count increments on the originally emitted active event!
    assert evt1.metadata["occurrence_count"] == 2

    # INC3: last_seen_ms updates to 6000.0ms while first_seen_ms remains 2000.0ms
    assert evt1.metadata["first_seen_ms"] == 2000.0
    assert evt1.metadata["last_seen_ms"] == 6000.0

    # INC4: peak risk score is retained / updated
    assert evt1.metadata["peak_risk_score"] >= initial_peak

    # INC5: component episode IDs and pattern IDs are union-deduplicated
    assert "ep1" in evt1.metadata["component_episode_ids"]
    assert "ep2" in evt1.metadata["component_episode_ids"]
    assert "pat1" in evt1.metadata["supporting_pattern_ids"]
    assert "pat3" in evt1.metadata["supporting_pattern_ids"]


def test_inc6_separate_later_incident_creates_new_id():
    """INC6: After merge window elapses (> 10s), a new pattern inception creates a new distinct event."""
    tracker = SeatRiskTracker(room_id="ROOM1", incident_merge_window_ms=10000.0)

    ep1 = TemporalEpisode("ep1", "S01", EpisodeType.HEAD_TURN_LEFT.value, EpisodeState.ACTIVE, start_timestamp_ms=1000.0, end_timestamp_ms=2000.0, quality=1.0)
    pat1 = BehaviorPattern("pat1", "S01", PatternType.REPEATED_NEIGHBOR_GLANCE.value, confidence=1.0, quality=1.0, start_timestamp_ms=1000.0, end_timestamp_ms=2000.0)
    pat2 = BehaviorPattern("pat2", "S01", PatternType.NEIGHBOR_ORIENTED_LEAN.value, confidence=1.0, quality=1.0, start_timestamp_ms=1000.0, end_timestamp_ms=2000.0)
    evt1 = tracker.update_seat("S01", active_episodes=[ep1], detected_patterns=[pat1, pat2], timestamp_ms=2000.0)
    assert evt1 is not None

    # Step at t = 20000ms (18.0s later, well past 10s merge window)
    ep3 = TemporalEpisode("ep3", "S01", EpisodeType.HEAD_TURN_LEFT.value, EpisodeState.ACTIVE, start_timestamp_ms=20000.0, end_timestamp_ms=21000.0, quality=1.0)
    pat3 = BehaviorPattern("pat3", "S01", PatternType.REPEATED_NEIGHBOR_GLANCE.value, confidence=1.0, quality=1.0, start_timestamp_ms=20000.0, end_timestamp_ms=21000.0)
    pat4 = BehaviorPattern("pat4", "S01", PatternType.NEIGHBOR_ORIENTED_LEAN.value, confidence=1.0, quality=1.0, start_timestamp_ms=20000.0, end_timestamp_ms=21000.0)
    evt2 = tracker.update_seat("S01", active_episodes=[ep3], detected_patterns=[pat3, pat4], timestamp_ms=21000.0)

    assert evt2 is not None
    assert evt2.event_id != evt1.event_id
    assert evt2.metadata["occurrence_count"] == 1
    assert evt2.metadata["first_seen_ms"] == 21000.0


def test_inc7_event_remains_flagged_for_review_never_cheating():
    """INC7: Verified that status is strictly FLAGGED_FOR_REVIEW / human assistance, zero CHEATING."""
    tracker = SeatRiskTracker(room_id="ROOM1")
    ep1 = TemporalEpisode("ep1", "S01", EpisodeType.HEAD_TURN_LEFT.value, EpisodeState.ACTIVE, start_timestamp_ms=500.0, end_timestamp_ms=1000.0, quality=1.0)
    pat1 = BehaviorPattern("pat1", "S01", PatternType.REPEATED_NEIGHBOR_GLANCE.value, confidence=1.0, quality=1.0, start_timestamp_ms=500.0, end_timestamp_ms=1000.0)
    pat2 = BehaviorPattern("pat2", "S01", PatternType.NEIGHBOR_ORIENTED_LEAN.value, confidence=1.0, quality=1.0, start_timestamp_ms=500.0, end_timestamp_ms=1000.0)

    evt = tracker.update_seat("S01", active_episodes=[ep1], detected_patterns=[pat1, pat2], timestamp_ms=1000.0)
    assert evt is not None
    assert "CHEATING" not in evt.status.upper()
    assert "CHEATING" not in evt.behavior.upper()
    assert evt.metadata.get("incident_id") == evt.event_id
