"""Contextual & Relational Pattern Engine for Review-Worthy Suspicious Pattern Detection.

Implements Scope 08 (FR-PAT-001 to FR-PAT-009) of SRS v2.0:
- Synthesizes Temporal Episodes, Seat Graph, Neighbor Relations, and Desk Geometry.
- P0 Pattern: REPEATED_NEIGHBOR_GLANCE (multi-episode glance repetition toward same neighbor).
- P0 Pattern: NEIGHBOR_ORIENTED_LEAN (persistent torso lean toward neighboring candidate).
- P0 Pattern: SEAT_LEFT (verified prolonged absence exceeding timeout without occlusion).
- P0 Pattern: MULTI_PERSON_DWELL_NEAR_SEAT (sustained multi-person dwell near seat).
- P1 Experimental Pattern: BELOW_DESK_INTERACTION (strictly gated by calibrated desk geometry).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

import numpy as np

from classroom_monitor.scene_context import CapabilityStatus, SeatContext, SeatGraph
from classroom_monitor.temporal_episode_engine import EpisodeState, EpisodeType, TemporalEpisode

logger = logging.getLogger("BehaviorPatternEngine")


class PatternType(str, Enum):
    REPEATED_NEIGHBOR_GLANCE = "REPEATED_NEIGHBOR_GLANCE"
    NEIGHBOR_ORIENTED_LEAN = "NEIGHBOR_ORIENTED_LEAN"
    SEAT_LEFT = "SEAT_LEFT"
    MULTI_PERSON_DWELL_NEAR_SEAT = "MULTI_PERSON_DWELL_NEAR_SEAT"
    BELOW_DESK_INTERACTION = "BELOW_DESK_INTERACTION"  # P1 Experimental
    CROSS_SEAT_REACH = "CROSS_SEAT_REACH"              # P1 Experimental
    MUTUAL_ORIENTATION = "MUTUAL_ORIENTATION"          # P1 Experimental


@dataclass
class BehaviorPattern:
    """Review-worthy composite behavior pattern synthesized across time and space."""

    pattern_id: str
    seat_id: str
    pattern_type: str
    start_timestamp_ms: float
    end_timestamp_ms: float
    confidence: float = 1.0
    quality: float = 1.0
    primary_direction: Optional[str] = None
    target_neighbor_id: Optional[str] = None
    component_episode_ids: List[str] = field(default_factory=list)
    supporting_cues: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "seat_id": self.seat_id,
            "pattern_type": self.pattern_type,
            "start_timestamp_ms": round(self.start_timestamp_ms, 1),
            "end_timestamp_ms": round(self.end_timestamp_ms, 1),
            "confidence": round(self.confidence, 3),
            "quality": round(self.quality, 3),
            "primary_direction": self.primary_direction,
            "target_neighbor_id": self.target_neighbor_id,
            "component_episode_ids": self.component_episode_ids,
            "supporting_cues": self.supporting_cues,
            "metadata": self.metadata,
        }


class BehaviorPatternEngine:
    """Evaluates contextual, relational, and recurrence patterns over active/historical episodes."""

    def __init__(
        self,
        seat_graph: Optional[SeatGraph] = None,
        glance_rolling_window_ms: float = 25000.0,
        min_glance_episodes: int = 2,
        lean_min_duration_ms: float = 1000.0,
        seat_left_timeout_ms: float = 15000.0,
        multi_person_dwell_ms: float = 2500.0,
        below_desk_min_duration_ms: float = 1500.0,
    ):
        self.seat_graph = seat_graph or SeatGraph()
        self.glance_rolling_window_ms = glance_rolling_window_ms
        self.min_glance_episodes = min_glance_episodes
        self.lean_min_duration_ms = lean_min_duration_ms
        self.seat_left_timeout_ms = seat_left_timeout_ms
        self.multi_person_dwell_ms = multi_person_dwell_ms
        self.below_desk_min_duration_ms = below_desk_min_duration_ms

        # Historical episode store per seat: seat_id -> List[TemporalEpisode]
        self._episode_history: Dict[str, List[TemporalEpisode]] = {}
        # Emitted pattern cooldown tracking: (seat_id, pattern_type) -> last_emitted_timestamp_ms
        self._pattern_cooldowns: Dict[Tuple[str, str], float] = {}

    def ingest_episodes(
        self,
        active_episodes: List[TemporalEpisode],
        completed_episodes: List[TemporalEpisode],
        seat_context: SeatContext,
        timestamp_ms: float,
    ) -> List[BehaviorPattern]:
        """Synthesize review-worthy patterns for the seat at the current timestamp."""
        seat_id = seat_context.seat_id
        if seat_id not in self._episode_history:
            self._episode_history[seat_id] = []

        # Store newly completed episodes
        for ep in completed_episodes:
            if ep.seat_id == seat_id and ep not in self._episode_history[seat_id]:
                self._episode_history[seat_id].append(ep)

        # Prune episodes older than rolling window
        self._episode_history[seat_id] = [
            ep for ep in self._episode_history[seat_id]
            if (timestamp_ms - (ep.end_timestamp_ms or ep.start_timestamp_ms)) <= self.glance_rolling_window_ms
        ]

        detected_patterns: List[BehaviorPattern] = []

        # 1. Evaluate P0 Pattern: REPEATED_NEIGHBOR_GLANCE
        glance_pattern = self._evaluate_repeated_glances(
            active_episodes=active_episodes,
            seat_context=seat_context,
            timestamp_ms=timestamp_ms,
        )
        if glance_pattern:
            detected_patterns.append(glance_pattern)

        # 2. Evaluate P0 Pattern: NEIGHBOR_ORIENTED_LEAN
        lean_pattern = self._evaluate_neighbor_lean(
            active_episodes=active_episodes,
            seat_context=seat_context,
            timestamp_ms=timestamp_ms,
        )
        if lean_pattern:
            detected_patterns.append(lean_pattern)

        # 3. Evaluate P0 Pattern: SEAT_LEFT
        seat_left_pattern = self._evaluate_seat_left(
            active_episodes=active_episodes,
            seat_context=seat_context,
            timestamp_ms=timestamp_ms,
        )
        if seat_left_pattern:
            detected_patterns.append(seat_left_pattern)

        # 4. Evaluate P0 Pattern: MULTI_PERSON_DWELL_NEAR_SEAT
        multi_dwell_pattern = self._evaluate_multi_person_dwell(
            active_episodes=active_episodes,
            seat_context=seat_context,
            timestamp_ms=timestamp_ms,
        )
        if multi_dwell_pattern:
            detected_patterns.append(multi_dwell_pattern)

        # 5. Evaluate P1 Pattern: BELOW_DESK_INTERACTION (Gated)
        below_desk_pattern = self._evaluate_below_desk_interaction(
            active_episodes=active_episodes,
            seat_context=seat_context,
            timestamp_ms=timestamp_ms,
        )
        if below_desk_pattern:
            detected_patterns.append(below_desk_pattern)

        return detected_patterns

    def _evaluate_repeated_glances(
        self,
        active_episodes: List[TemporalEpisode],
        seat_context: SeatContext,
        timestamp_ms: float,
    ) -> Optional[BehaviorPattern]:
        """P0: Multi-episode head turn repetition toward a specific neighboring candidate.
        
        Strict Gating:
        - head_orientation capability != DISABLED
        - pairwise_relation capability != DISABLED
        - target_neighbor_id must exist in SeatGraph for the glance direction.
        """
        if seat_context.capabilities.head_orientation == CapabilityStatus.DISABLED:
            return None
        if seat_context.capabilities.pairwise_relation == CapabilityStatus.DISABLED:
            return None

        seat_id = seat_context.seat_id
        all_recent = self._episode_history.get(seat_id, []) + [
            ep for ep in active_episodes if ep.seat_id == seat_id and ep.state in (EpisodeState.ACTIVE, EpisodeState.ENDING)
        ]

        # Check Left glances (strictly requires configured left neighbor)
        left_neighbor = seat_context.neighbors.left_neighbor_id
        left_turns = (
            [ep for ep in all_recent if ep.episode_type == EpisodeType.HEAD_TURN_LEFT.value]
            if left_neighbor is not None
            else []
        )

        # Check Right glances (strictly requires configured right neighbor)
        right_neighbor = seat_context.neighbors.right_neighbor_id
        right_turns = (
            [ep for ep in all_recent if ep.episode_type == EpisodeType.HEAD_TURN_RIGHT.value]
            if right_neighbor is not None
            else []
        )

        target_turns: List[TemporalEpisode] = []
        target_dir = ""
        target_neighbor = None

        if len(left_turns) >= self.min_glance_episodes:
            target_turns = left_turns
            target_dir = "LEFT"
            target_neighbor = left_neighbor
        elif len(right_turns) >= self.min_glance_episodes:
            target_turns = right_turns
            target_dir = "RIGHT"
            target_neighbor = right_neighbor

        if not target_turns or target_neighbor is None or len(target_turns) < self.min_glance_episodes:
            return None

        # Check rate limiting cooldown (e.g. 10.0s per pattern)
        cooldown_key = (seat_id, PatternType.REPEATED_NEIGHBOR_GLANCE.value)
        if (timestamp_ms - self._pattern_cooldowns.get(cooldown_key, -100000.0)) < 10000.0:
            return None

        earliest_start = min(ep.start_timestamp_ms for ep in target_turns)
        mean_quality = float(np.mean([ep.quality for ep in target_turns]))
        mean_conf = float(np.mean([ep.confidence for ep in target_turns]))

        pattern = BehaviorPattern(
            pattern_id=str(uuid.uuid4()),
            seat_id=seat_id,
            pattern_type=PatternType.REPEATED_NEIGHBOR_GLANCE.value,
            start_timestamp_ms=earliest_start,
            end_timestamp_ms=timestamp_ms,
            confidence=mean_conf,
            quality=mean_quality,
            primary_direction=target_dir,
            target_neighbor_id=target_neighbor,
            component_episode_ids=[ep.episode_id for ep in target_turns],
            supporting_cues=[f"Glance Count: {len(target_turns)} in {self.glance_rolling_window_ms/1000:.0f}s window"],
        )
        self._pattern_cooldowns[cooldown_key] = timestamp_ms
        return pattern

    def _evaluate_neighbor_lean(
        self,
        active_episodes: List[TemporalEpisode],
        seat_context: SeatContext,
        timestamp_ms: float,
    ) -> Optional[BehaviorPattern]:
        """P0: Persistent torso lean pointing toward a neighboring candidate.
        
        Strict Gating:
        - body_lean capability != DISABLED
        - pairwise_relation capability != DISABLED
        - target_neighbor_id must exist in SeatGraph for the lean direction.
        """
        if seat_context.capabilities.body_lean == CapabilityStatus.DISABLED:
            return None
        if seat_context.capabilities.pairwise_relation == CapabilityStatus.DISABLED:
            return None

        seat_id = seat_context.seat_id
        lean_episodes = [
            ep for ep in active_episodes
            if ep.seat_id == seat_id
            and ep.episode_type in (EpisodeType.TORSO_LEAN_LEFT.value, EpisodeType.TORSO_LEAN_RIGHT.value)
            and ep.duration_ms >= self.lean_min_duration_ms
        ]

        if not lean_episodes:
            return None

        # Filter lean episodes to only those with a real configured neighbor in the lean direction
        valid_lean_episodes: List[Tuple[TemporalEpisode, str, str]] = []
        for ep in lean_episodes:
            direction = "LEFT" if ep.episode_type == EpisodeType.TORSO_LEAN_LEFT.value else "RIGHT"
            neighbor_id = (
                seat_context.neighbors.left_neighbor_id if direction == "LEFT" else seat_context.neighbors.right_neighbor_id
            )
            if neighbor_id is not None:
                valid_lean_episodes.append((ep, direction, neighbor_id))

        if not valid_lean_episodes:
            return None

        target_ep, direction, target_neighbor = valid_lean_episodes[0]

        cooldown_key = (seat_id, PatternType.NEIGHBOR_ORIENTED_LEAN.value)
        if (timestamp_ms - self._pattern_cooldowns.get(cooldown_key, -100000.0)) < 8000.0:
            return None

        # Check supporting cue: Head turn in the same direction
        supporting_cues = []
        expected_head_type = EpisodeType.HEAD_TURN_LEFT.value if direction == "LEFT" else EpisodeType.HEAD_TURN_RIGHT.value
        has_head_turn = any(ep.episode_type == expected_head_type for ep in active_episodes if ep.seat_id == seat_id)
        if has_head_turn:
            supporting_cues.append(f"Corroborated by concurrent HEAD_TURN_{direction}")

        pattern = BehaviorPattern(
            pattern_id=str(uuid.uuid4()),
            seat_id=seat_id,
            pattern_type=PatternType.NEIGHBOR_ORIENTED_LEAN.value,
            start_timestamp_ms=target_ep.start_timestamp_ms,
            end_timestamp_ms=timestamp_ms,
            confidence=target_ep.confidence * (1.15 if has_head_turn else 1.0),
            quality=target_ep.quality,
            primary_direction=direction,
            target_neighbor_id=target_neighbor,
            component_episode_ids=[target_ep.episode_id],
            supporting_cues=supporting_cues,
        )
        self._pattern_cooldowns[cooldown_key] = timestamp_ms
        return pattern

    def _evaluate_seat_left(
        self,
        active_episodes: List[TemporalEpisode],
        seat_context: SeatContext,
        timestamp_ms: float,
    ) -> Optional[BehaviorPattern]:
        """P0: Verified prolonged candidate absence without proctor occlusion."""
        seat_id = seat_context.seat_id
        empty_eps = [
            ep for ep in active_episodes
            if ep.seat_id == seat_id
            and ep.episode_type == EpisodeType.SEAT_EMPTY.value
            and ep.duration_ms >= self.seat_left_timeout_ms
        ]

        if not empty_eps:
            return None

        cooldown_key = (seat_id, PatternType.SEAT_LEFT.value)
        if (timestamp_ms - self._pattern_cooldowns.get(cooldown_key, -100000.0)) < 20000.0:
            return None

        target_ep = empty_eps[0]
        pattern = BehaviorPattern(
            pattern_id=str(uuid.uuid4()),
            seat_id=seat_id,
            pattern_type=PatternType.SEAT_LEFT.value,
            start_timestamp_ms=target_ep.start_timestamp_ms,
            end_timestamp_ms=timestamp_ms,
            confidence=1.0,
            quality=1.0,
            component_episode_ids=[target_ep.episode_id],
            supporting_cues=[f"Empty duration: {target_ep.duration_ms/1000:.1f}s"],
        )
        self._pattern_cooldowns[cooldown_key] = timestamp_ms
        return pattern

    def _evaluate_multi_person_dwell(
        self,
        active_episodes: List[TemporalEpisode],
        seat_context: SeatContext,
        timestamp_ms: float,
    ) -> Optional[BehaviorPattern]:
        """P0: Sustained multi-person dwell in/near seat ROI."""
        seat_id = seat_context.seat_id
        multi_eps = [
            ep for ep in active_episodes
            if ep.seat_id == seat_id
            and ep.episode_type == EpisodeType.MULTI_PERSON_NEAR_SEAT.value
            and ep.duration_ms >= self.multi_person_dwell_ms
        ]

        if not multi_eps:
            return None

        cooldown_key = (seat_id, PatternType.MULTI_PERSON_DWELL_NEAR_SEAT.value)
        if (timestamp_ms - self._pattern_cooldowns.get(cooldown_key, -100000.0)) < 15000.0:
            return None

        target_ep = multi_eps[0]
        pattern = BehaviorPattern(
            pattern_id=str(uuid.uuid4()),
            seat_id=seat_id,
            pattern_type=PatternType.MULTI_PERSON_DWELL_NEAR_SEAT.value,
            start_timestamp_ms=target_ep.start_timestamp_ms,
            end_timestamp_ms=timestamp_ms,
            confidence=1.0,
            quality=1.0,
            component_episode_ids=[target_ep.episode_id],
            supporting_cues=[f"Dwell duration: {target_ep.duration_ms/1000:.1f}s"],
        )
        self._pattern_cooldowns[cooldown_key] = timestamp_ms
        return pattern

    def _evaluate_below_desk_interaction(
        self,
        active_episodes: List[TemporalEpisode],
        seat_context: SeatContext,
        timestamp_ms: float,
    ) -> Optional[BehaviorPattern]:
        """P1 Experimental: Hand beneath desk boundary gated strictly by calibrated desk geometry."""
        if seat_context.capabilities.desk_hand_interaction != CapabilityStatus.ENABLED:
            return None

        seat_id = seat_context.seat_id
        below_eps = [
            ep for ep in active_episodes
            if ep.seat_id == seat_id
            and ep.episode_type == EpisodeType.WRIST_BELOW_DESK.value
            and ep.duration_ms >= self.below_desk_min_duration_ms
        ]

        if not below_eps:
            return None

        target_ep = below_eps[0]
        cooldown_key = (seat_id, PatternType.BELOW_DESK_INTERACTION.value)
        if (timestamp_ms - self._pattern_cooldowns.get(cooldown_key, -100000.0)) < 12000.0:
            return None

        # Check supporting cue: Head pitch down
        supporting_cues = []
        has_head_down = any(ep.episode_type == EpisodeType.HEAD_PITCH_DOWN.value for ep in active_episodes if ep.seat_id == seat_id)
        if has_head_down:
            supporting_cues.append("Corroborated by simultaneous downward head pitch")

        pattern = BehaviorPattern(
            pattern_id=str(uuid.uuid4()),
            seat_id=seat_id,
            pattern_type=PatternType.BELOW_DESK_INTERACTION.value,
            start_timestamp_ms=target_ep.start_timestamp_ms,
            end_timestamp_ms=timestamp_ms,
            confidence=target_ep.confidence,
            quality=target_ep.quality,
            component_episode_ids=[target_ep.episode_id],
            supporting_cues=supporting_cues,
        )
        self._pattern_cooldowns[cooldown_key] = timestamp_ms
        return pattern
