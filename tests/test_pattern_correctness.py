"""Comprehensive Test Suite for Pattern Correctness (PAT1 - PAT10).

Verifies:
- PAT1: One head-turn episode does not produce repeated glance.
- PAT2: Same episode snapshot repeated does not count twice.
- PAT3: Two distinct LEFT episodes + left neighbor produce pattern.
- PAT4: LEFT episodes without left neighbor cannot produce pattern.
- PAT5: LEFT + RIGHT does not count as two same-direction glances.
- PAT6: Continuous LEFT turn remains one episode.
- PAT7: Neighbor lean requires correct direction neighbor.
- PAT8: Transient multi-person does not produce dwell.
- PAT9: OCCLUDED state does not produce SEAT_LEFT.
- PAT10: Missing wrist does not produce below-desk pattern.
"""

from __future__ import annotations

import pytest

from classroom_monitor.behavior_pattern_engine import BehaviorPatternEngine, PatternType
from classroom_monitor.scene_context import CapabilityStatus, DeskGeometry, SeatCapabilities, SeatContext, SeatGraph, SeatNeighbors
from classroom_monitor.temporal_episode_engine import EpisodeState, EpisodeType, TemporalEpisode, TemporalEpisodeEngine


def test_pat1_single_head_turn_does_not_produce_repeated_glance():
    """PAT1: One head-turn episode does not satisfy min_glance_episodes=2."""
    graph = SeatGraph(room_id="ROOM1")
    ctx = SeatContext(seat_id="S01", neighbors=SeatNeighbors(left_neighbor_id="S02"))
    graph.seats_context["S01"] = ctx

    engine = BehaviorPatternEngine(seat_graph=graph, min_glance_episodes=2)

    ep1 = TemporalEpisode("ep1", "S01", EpisodeType.HEAD_TURN_LEFT.value, EpisodeState.ACTIVE, start_timestamp_ms=100.0)
    patterns = engine.ingest_episodes(active_episodes=[ep1], completed_episodes=[], seat_context=ctx, timestamp_ms=500.0)

    assert not any(p.pattern_type == PatternType.REPEATED_NEIGHBOR_GLANCE.value for p in patterns)


def test_pat2_same_episode_snapshot_not_counted_twice():
    """PAT2: Ingesting the same episode id in both active and completed does not count as 2 glances."""
    graph = SeatGraph(room_id="ROOM1")
    ctx = SeatContext(seat_id="S01", neighbors=SeatNeighbors(left_neighbor_id="S02"))
    graph.seats_context["S01"] = ctx

    engine = BehaviorPatternEngine(seat_graph=graph, min_glance_episodes=2)

    ep1 = TemporalEpisode("ep1", "S01", EpisodeType.HEAD_TURN_LEFT.value, EpisodeState.ACTIVE, start_timestamp_ms=100.0)
    # Passed in both active and completed with same ID
    patterns = engine.ingest_episodes(active_episodes=[ep1], completed_episodes=[ep1], seat_context=ctx, timestamp_ms=500.0)

    assert not any(p.pattern_type == PatternType.REPEATED_NEIGHBOR_GLANCE.value for p in patterns)


def test_pat3_two_distinct_left_episodes_with_neighbor_produce_pattern():
    """PAT3: Two distinct LEFT episodes with a configured left neighbor produce REPEATED_NEIGHBOR_GLANCE."""
    graph = SeatGraph(room_id="ROOM1")
    ctx = SeatContext(seat_id="S01", neighbors=SeatNeighbors(left_neighbor_id="S02"))
    graph.seats_context["S01"] = ctx

    engine = BehaviorPatternEngine(seat_graph=graph, min_glance_episodes=2)

    ep1 = TemporalEpisode("ep1", "S01", EpisodeType.HEAD_TURN_LEFT.value, EpisodeState.ENDED, start_timestamp_ms=100.0, end_timestamp_ms=600.0)
    ep2 = TemporalEpisode("ep2", "S01", EpisodeType.HEAD_TURN_LEFT.value, EpisodeState.ACTIVE, start_timestamp_ms=1500.0)

    patterns = engine.ingest_episodes(active_episodes=[ep2], completed_episodes=[ep1], seat_context=ctx, timestamp_ms=2000.0)

    glance_pats = [p for p in patterns if p.pattern_type == PatternType.REPEATED_NEIGHBOR_GLANCE.value]
    assert len(glance_pats) == 1
    assert glance_pats[0].primary_direction == "LEFT"
    assert glance_pats[0].target_neighbor_id == "S02"


def test_pat4_left_episodes_without_left_neighbor_gated():
    """PAT4: Repeated LEFT head turns without a configured left neighbor (e.g. wall/window) are suppressed."""
    graph = SeatGraph(room_id="ROOM1")
    # No left neighbor
    ctx = SeatContext(seat_id="S01", neighbors=SeatNeighbors(left_neighbor_id=None, right_neighbor_id="S02"))
    graph.seats_context["S01"] = ctx

    engine = BehaviorPatternEngine(seat_graph=graph, min_glance_episodes=2)

    ep1 = TemporalEpisode("ep1", "S01", EpisodeType.HEAD_TURN_LEFT.value, EpisodeState.ENDED, start_timestamp_ms=100.0, end_timestamp_ms=600.0)
    ep2 = TemporalEpisode("ep2", "S01", EpisodeType.HEAD_TURN_LEFT.value, EpisodeState.ACTIVE, start_timestamp_ms=1500.0)

    patterns = engine.ingest_episodes(active_episodes=[ep2], completed_episodes=[ep1], seat_context=ctx, timestamp_ms=2000.0)
    assert not any(p.pattern_type == PatternType.REPEATED_NEIGHBOR_GLANCE.value for p in patterns)


def test_pat5_left_and_right_turns_do_not_mix():
    """PAT5: One LEFT turn and one RIGHT turn do not count as two glances in the same direction."""
    graph = SeatGraph(room_id="ROOM1")
    ctx = SeatContext(seat_id="S01", neighbors=SeatNeighbors(left_neighbor_id="S02", right_neighbor_id="S03"))
    graph.seats_context["S01"] = ctx

    engine = BehaviorPatternEngine(seat_graph=graph, min_glance_episodes=2)

    ep_left = TemporalEpisode("ep-l", "S01", EpisodeType.HEAD_TURN_LEFT.value, EpisodeState.ENDED, start_timestamp_ms=100.0, end_timestamp_ms=600.0)
    ep_right = TemporalEpisode("ep-r", "S01", EpisodeType.HEAD_TURN_RIGHT.value, EpisodeState.ACTIVE, start_timestamp_ms=1500.0)

    patterns = engine.ingest_episodes(active_episodes=[ep_right], completed_episodes=[ep_left], seat_context=ctx, timestamp_ms=2000.0)
    assert not any(p.pattern_type == PatternType.REPEATED_NEIGHBOR_GLANCE.value for p in patterns)


def test_pat6_continuous_left_turn_remains_single_episode():
    """PAT6: A continuous 5s head turn produces exactly 1 long episode, not multiple episodes."""
    temp_engine = TemporalEpisodeEngine(min_persistence_ms=200.0)

    # Ingest 20 frames of continuous turn
    for i in range(20):
        t = i * 100.0
        from classroom_monitor.observation_extractor import ObservationType, RawObservation
        obs = [RawObservation("S01", t, ObservationType.HEAD_YAW_RELATIVE.value, -35.0, 0.9, 0.9)]
        eps = temp_engine.process_observations(obs, timestamp_ms=t)

    assert len(eps) == 1
    assert eps[0].duration_ms >= 1800.0
    assert len(temp_engine.completed_episodes) == 0  # Still ongoing, not fragmented


def test_pat7_neighbor_lean_requires_correct_neighbor():
    """PAT7: Torso lean left produces NEIGHBOR_ORIENTED_LEAN only if left neighbor exists."""
    graph = SeatGraph(room_id="ROOM1")
    ctx_no_left = SeatContext(seat_id="S01", neighbors=SeatNeighbors(left_neighbor_id=None, right_neighbor_id="S02"))
    ctx_with_left = SeatContext(seat_id="S02", neighbors=SeatNeighbors(left_neighbor_id="S01"))

    engine = BehaviorPatternEngine(seat_graph=graph)

    ep_lean_l = TemporalEpisode("lean1", "S01", EpisodeType.TORSO_LEAN_LEFT.value, EpisodeState.ACTIVE, start_timestamp_ms=100.0, duration_ms=1000.0)

    # Without left neighbor -> no pattern
    pats1 = engine.ingest_episodes(active_episodes=[ep_lean_l], completed_episodes=[], seat_context=ctx_no_left, timestamp_ms=1500.0)
    assert not any(p.pattern_type == PatternType.NEIGHBOR_ORIENTED_LEAN.value for p in pats1)

    # With left neighbor -> produces pattern
    ep_lean_l2 = TemporalEpisode("lean2", "S02", EpisodeType.TORSO_LEAN_LEFT.value, EpisodeState.ACTIVE, start_timestamp_ms=100.0, duration_ms=1000.0)
    pats2 = engine.ingest_episodes(active_episodes=[ep_lean_l2], completed_episodes=[], seat_context=ctx_with_left, timestamp_ms=1500.0)
    assert any(p.pattern_type == PatternType.NEIGHBOR_ORIENTED_LEAN.value for p in pats2)


def test_pat8_transient_multi_person_does_not_produce_dwell():
    """PAT8: Short multi-person detection (< 2500ms dwell) does not trigger MULTI_PERSON_DWELL."""
    graph = SeatGraph(room_id="ROOM1")
    ctx = SeatContext(seat_id="S01")
    engine = BehaviorPatternEngine(seat_graph=graph, multi_person_dwell_ms=2500.0)

    ep_short = TemporalEpisode("mp1", "S01", EpisodeType.MULTI_PERSON_NEAR_SEAT.value, EpisodeState.ACTIVE, start_timestamp_ms=100.0, duration_ms=1000.0)
    pats = engine.ingest_episodes(active_episodes=[ep_short], completed_episodes=[], seat_context=ctx, timestamp_ms=1100.0)
    assert not any(p.pattern_type == PatternType.MULTI_PERSON_DWELL_NEAR_SEAT.value for p in pats)


def test_pat9_occluded_state_does_not_produce_seat_left():
    """PAT9: Transient empty state under 15.0s does not produce SEAT_LEFT."""
    graph = SeatGraph(room_id="ROOM1")
    ctx = SeatContext(seat_id="S01")
    engine = BehaviorPatternEngine(seat_graph=graph, seat_left_timeout_ms=15000.0)

    ep_empty_short = TemporalEpisode("emp1", "S01", EpisodeType.SEAT_EMPTY.value, EpisodeState.ACTIVE, start_timestamp_ms=100.0, duration_ms=4000.0)
    pats = engine.ingest_episodes(active_episodes=[ep_empty_short], completed_episodes=[], seat_context=ctx, timestamp_ms=4100.0)
    assert not any(p.pattern_type == PatternType.SEAT_LEFT.value for p in pats)


def test_pat10_missing_wrist_does_not_produce_below_desk():
    """PAT10: When hand interaction capability is disabled, below-desk patterns are gated."""
    graph = SeatGraph(room_id="ROOM1")
    ctx = SeatContext(seat_id="S01", capabilities=SeatCapabilities(desk_hand_interaction=CapabilityStatus.DISABLED))
    engine = BehaviorPatternEngine(seat_graph=graph)

    ep_wrist = TemporalEpisode("wr1", "S01", EpisodeType.WRIST_BELOW_DESK.value, EpisodeState.ACTIVE, start_timestamp_ms=100.0, duration_ms=2000.0)
    pats = engine.ingest_episodes(active_episodes=[ep_wrist], completed_episodes=[], seat_context=ctx, timestamp_ms=2100.0)
    assert not any(p.pattern_type == PatternType.BELOW_DESK_INTERACTION.value for p in pats)
