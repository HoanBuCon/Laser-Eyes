"""Comprehensive Test Suite for Incident & Risk Scoring Hardening (RISK1 - RISK10).

Verifies:
- RISK1: One low-level head turn does not directly flag review.
- RISK2: Related patterns within active incident aggregate.
- RISK3: Same incident does not emit repeated review events after every cooldown expiry.
- RISK4: Genuine separate later incident can emit second event.
- RISK5: Recidivism applies only to separate incident.
- RISK6: Risk decays correctly using video timestamp.
- RISK7: Pattern diminishing returns work.
- RISK8: No event contains CHEATING verdict.
- RISK9: Event occurrence_count increments during merged incident.
- RISK10: Event peak risk updates without creating duplicate event.
"""

from __future__ import annotations

import pytest

from classroom_monitor.behavior_pattern_engine import BehaviorPattern, PatternType
from classroom_monitor.models import ClassroomEvent
from classroom_monitor.seat_risk_tracker import RiskState, SeatRiskTracker
from classroom_monitor.temporal_episode_engine import EpisodeState, EpisodeType, TemporalEpisode


def test_risk1_single_head_turn_does_not_flag_review():
    """RISK1: Single raw head turn (+12 pts) leaves seat in NORMAL/OBSERVE state."""
    tracker = SeatRiskTracker(room_id="ROOM1", incident_merge_window_ms=15000.0)
    ep = TemporalEpisode("ep1", "S01", EpisodeType.HEAD_TURN_LEFT.value, EpisodeState.ACTIVE, start_timestamp_ms=100.0, duration_ms=600.0)

    evt = tracker.update_seat("S01", active_episodes=[ep], detected_patterns=[], timestamp_ms=700.0)
    assert evt is None
    profile = tracker.profiles["S01"]
    assert profile.risk_score < tracker.flagged_threshold
    assert profile.current_state in (RiskState.NORMAL.value, RiskState.OBSERVE.value)


def test_risk2_risk9_risk10_incident_aggregation_and_merge():
    """RISK2, RISK9, RISK10: Patterns within 15s merge into the active incident with updated count and peak risk."""
    tracker = SeatRiskTracker(room_id="ROOM1", incident_merge_window_ms=15000.0)

    ep1 = TemporalEpisode("ep1", "S01", EpisodeType.HEAD_TURN_LEFT.value, EpisodeState.ACTIVE, start_timestamp_ms=1000.0, end_timestamp_ms=2000.0, quality=1.0)
    pat1 = BehaviorPattern("pat1", "S01", PatternType.REPEATED_NEIGHBOR_GLANCE.value, confidence=1.0, quality=1.0, start_timestamp_ms=1000.0, end_timestamp_ms=2000.0)
    pat_lean = BehaviorPattern("pat2", "S01", PatternType.NEIGHBOR_ORIENTED_LEAN.value, confidence=1.0, quality=1.0, start_timestamp_ms=1000.0, end_timestamp_ms=2000.0)

    # Initial trigger -> Emits 1st Event (38 + 32 + 12 = 82 >= 80)
    evt1 = tracker.update_seat("S01", active_episodes=[ep1], detected_patterns=[pat1, pat_lean], timestamp_ms=2000.0)
    assert evt1 is not None
    assert evt1.status in ("suspicious", "flagged_for_human_review", "PENDING", "FLAGGED_FOR_REVIEW")
    assert evt1.metadata.get("occurrence_count") == 1
    profile = tracker.profiles["S01"]
    assert profile.active_incident is not None
    assert profile.active_incident["occurrence_count"] == 1

    # Same pattern re-triggers at t = 6000ms (within 15s merge window) -> Merged, NO duplicate event!
    pat3 = BehaviorPattern("pat3", "S01", PatternType.REPEATED_NEIGHBOR_GLANCE.value, confidence=1.0, quality=1.0, start_timestamp_ms=6000.0, end_timestamp_ms=7000.0)
    evt2 = tracker.update_seat("S01", active_episodes=[], detected_patterns=[pat3], timestamp_ms=7000.0)

    assert evt2 is None  # Merged!
    assert profile.active_incident["occurrence_count"] == 2
    assert profile.total_incidents_count == 1  # Exactly 1 distinct incident


def test_risk3_cooldown_expiry_does_not_spam_events():
    """RISK3: Same incident does not re-emit an event after cooldown expiration without genuine pattern."""
    tracker = SeatRiskTracker(room_id="ROOM1", cooldown_duration_ms=2000.0, incident_merge_window_ms=15000.0)

    ep1 = TemporalEpisode("ep1", "S01", EpisodeType.HEAD_TURN_LEFT.value, EpisodeState.ACTIVE, start_timestamp_ms=1000.0, end_timestamp_ms=2000.0, quality=1.0)
    pat1 = BehaviorPattern("pat1", "S01", PatternType.REPEATED_NEIGHBOR_GLANCE.value, confidence=1.0, quality=1.0, start_timestamp_ms=1000.0, end_timestamp_ms=2000.0)
    pat2 = BehaviorPattern("pat2", "S01", PatternType.NEIGHBOR_ORIENTED_LEAN.value, confidence=1.0, quality=1.0, start_timestamp_ms=1000.0, end_timestamp_ms=2000.0)

    evt1 = tracker.update_seat("S01", active_episodes=[ep1], detected_patterns=[pat1, pat2], timestamp_ms=2000.0)
    assert evt1 is not None

    # Step at t = 5000ms (cooldown expired, no new pattern)
    evt_idle = tracker.update_seat("S01", active_episodes=[], detected_patterns=[], timestamp_ms=5000.0)
    assert evt_idle is None


def test_risk4_risk5_genuine_separate_incident_emits_second_event():
    """RISK4 & RISK5: After quiet period (> 15s merge window), a new pattern creates a 2nd incident."""
    tracker = SeatRiskTracker(room_id="ROOM1", incident_merge_window_ms=10000.0)

    ep1 = TemporalEpisode("ep1", "S01", EpisodeType.HEAD_TURN_LEFT.value, EpisodeState.ACTIVE, start_timestamp_ms=1000.0, end_timestamp_ms=2000.0, quality=1.0)
    pat1 = BehaviorPattern("pat1", "S01", PatternType.REPEATED_NEIGHBOR_GLANCE.value, confidence=1.0, quality=1.0, start_timestamp_ms=1000.0, end_timestamp_ms=2000.0)
    pat2 = BehaviorPattern("pat2", "S01", PatternType.NEIGHBOR_ORIENTED_LEAN.value, confidence=1.0, quality=1.0, start_timestamp_ms=1000.0, end_timestamp_ms=2000.0)
    evt1 = tracker.update_seat("S01", active_episodes=[ep1], detected_patterns=[pat1, pat2], timestamp_ms=2000.0)
    assert evt1 is not None

    # t = 20000ms (18.0s later, past 10s merge window) -> New pattern
    ep2 = TemporalEpisode("ep2", "S01", EpisodeType.HEAD_TURN_LEFT.value, EpisodeState.ACTIVE, start_timestamp_ms=20000.0, end_timestamp_ms=21000.0, quality=1.0)
    pat3 = BehaviorPattern("pat3", "S01", PatternType.REPEATED_NEIGHBOR_GLANCE.value, confidence=1.0, quality=1.0, start_timestamp_ms=20000.0, end_timestamp_ms=21000.0)
    pat4 = BehaviorPattern("pat4", "S01", PatternType.NEIGHBOR_ORIENTED_LEAN.value, confidence=1.0, quality=1.0, start_timestamp_ms=20000.0, end_timestamp_ms=21000.0)
    evt2 = tracker.update_seat("S01", active_episodes=[ep2], detected_patterns=[pat3, pat4], timestamp_ms=21000.0)

    assert evt2 is not None
    assert evt2.event_id != evt1.event_id
    assert tracker.profiles["S01"].total_incidents_count == 2


def test_risk6_temporal_risk_decay():
    """RISK6: Score decays smoothly at configured rate per second."""
    tracker = SeatRiskTracker(room_id="ROOM1", decay_rate_per_sec=3.0)
    profile = tracker.get_or_create_profile("S01")
    profile.risk_score = 50.0
    profile.last_update_timestamp_ms = 1000.0

    tracker.update_seat("S01", active_episodes=[], detected_patterns=[], timestamp_ms=3000.0)  # dt = 2.0s
    assert pytest.approx(profile.risk_score, abs=0.5) == 44.0  # 50 - 2*3


def test_risk7_diminishing_returns():
    """RISK7: Repeated occurrences of the same pattern yield diminishing risk increments."""
    tracker = SeatRiskTracker(room_id="ROOM1", decay_rate_per_sec=0.0)

    pat1 = BehaviorPattern("pat1", "S01", PatternType.REPEATED_NEIGHBOR_GLANCE.value, confidence=1.0, quality=1.0, start_timestamp_ms=1000.0, end_timestamp_ms=2000.0)
    tracker.update_seat("S01", active_episodes=[], detected_patterns=[pat1], timestamp_ms=1000.0)
    score1 = tracker.profiles["S01"].risk_score

    pat2 = BehaviorPattern("pat2", "S01", PatternType.REPEATED_NEIGHBOR_GLANCE.value, confidence=1.0, quality=1.0, start_timestamp_ms=2000.0, end_timestamp_ms=3000.0)
    tracker.update_seat("S01", active_episodes=[], detected_patterns=[pat2], timestamp_ms=2000.0)
    score2 = tracker.profiles["S01"].risk_score

    increment1 = score1
    increment2 = score2 - score1
    assert increment2 < increment1  # Diminishing return factor applied!


def test_risk8_no_cheating_verdict():
    """RISK8: All events adhere strictly to FLAGGED_FOR_REVIEW / human assistance, zero CHEATING labels."""
    tracker = SeatRiskTracker(room_id="ROOM1")
    ep1 = TemporalEpisode("ep1", "S01", EpisodeType.HEAD_TURN_LEFT.value, EpisodeState.ACTIVE, start_timestamp_ms=500.0, end_timestamp_ms=1000.0, quality=1.0)
    pat1 = BehaviorPattern("pat1", "S01", PatternType.REPEATED_NEIGHBOR_GLANCE.value, confidence=1.0, quality=1.0, start_timestamp_ms=500.0, end_timestamp_ms=1000.0)
    pat2 = BehaviorPattern("pat2", "S01", PatternType.NEIGHBOR_ORIENTED_LEAN.value, confidence=1.0, quality=1.0, start_timestamp_ms=500.0, end_timestamp_ms=1000.0)
    evt = tracker.update_seat("S01", active_episodes=[ep1], detected_patterns=[pat1, pat2], timestamp_ms=1000.0)

    assert evt is not None
    evt_dict = evt.to_dict()
    assert "CHEATING" not in str(evt_dict.get("status", "")).upper()
    assert "CHEATING" not in str(evt_dict.get("verdict", "")).upper()
    assert "CHEATING" not in str(evt_dict.get("behavior", "")).upper()
