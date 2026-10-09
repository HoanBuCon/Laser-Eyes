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
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

from classroom_monitor.scene_context import CapabilityStatus, SeatContext, SeatGraph
from classroom_monitor.temporal_episode_engine import EpisodeState, EpisodeType, TemporalEpisode

logger = logging.getLogger("BehaviorPatternEngine")


class PatternType(str, Enum):
    REPEATED_NEIGHBOR_GLANCE = "REPEATED_NEIGHBOR_GLANCE"
    NEIGHBOR_ORIENTED_LEAN = "NEIGHBOR_ORIENTED_LEAN"
    SEAT_LEFT = "SEAT_LEFT"
    MULTI_PERSON_DWELL_NEAR_SEAT = "MULTI_PERSON_DWELL_NEAR_SEAT"
    SUSTAINED_NEIGHBOR_ATTENTION = "SUSTAINED_NEIGHBOR_ATTENTION"
    HEADS_TOGETHER = "HEADS_TOGETHER"  # desk mates' heads together (pair_proximity)
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
        glance_merge_gap_ms: float = 500.0,
        attention_min_turn_ms: float = 2000.0,
        attention_min_offset_overlap: float = 0.5,
        attention_repeat_ms: Optional[float] = 3000.0,
    ):
        self.seat_graph = seat_graph or SeatGraph()
        self.glance_rolling_window_ms = glance_rolling_window_ms
        self.min_glance_episodes = min_glance_episodes
        self.lean_min_duration_ms = lean_min_duration_ms
        self.seat_left_timeout_ms = seat_left_timeout_ms
        self.multi_person_dwell_ms = multi_person_dwell_ms
        self.below_desk_min_duration_ms = below_desk_min_duration_ms
        # Same-direction head-turn episodes separated by strictly less than this gap are
        # one glance split by sensor noise or a brief missing sample.
        self.glance_merge_gap_ms = glance_merge_gap_ms
        # A long head turn toward a neighbour counts only when the head also
        # moved toward that neighbour (pose keypoints) for this share of it.
        # Head-pose yaw alone is unreliable when the face is turned down.
        self.attention_min_turn_ms = attention_min_turn_ms
        self.attention_min_offset_overlap = attention_min_offset_overlap
        # While the same look continues, it counts again every this many ms
        # (None: once per head turn), so a longer look weighs more.
        self.attention_repeat_ms = attention_repeat_ms
        # head-turn episode id -> (times counted, last time counted)
        self._attention_emitted_turns: Dict[str, Tuple[int, float]] = {}

        # Historical episode store per seat: seat_id -> List[TemporalEpisode]
        self._episode_history: Dict[str, List[TemporalEpisode]] = {}
        self._seen_episode_ids: Dict[str, Set[str]] = {}
        # Emitted pattern cooldown tracking: (seat_id, pattern_type) -> last_emitted_timestamp_ms
        self._pattern_cooldowns: Dict[Tuple[str, str], float] = {}
        # Stable evidence identity prevents the same component episodes from
        # being re-emitted with a fresh UUID after a time-only cooldown.
        self._emitted_evidence_keys: Dict[Tuple[str, str, Tuple[str, ...]], float] = {}

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
            self._seen_episode_ids[seat_id] = set()

        # Store newly completed episodes
        seen_ids = self._seen_episode_ids[seat_id]
        for ep in completed_episodes:
            if ep.seat_id == seat_id and ep.episode_id not in seen_ids:
                self._episode_history[seat_id].append(ep)
                seen_ids.add(ep.episode_id)

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

        # 2b. Evaluate Pattern: SUSTAINED_NEIGHBOR_ATTENTION
        attention_pattern = self._evaluate_sustained_attention(
            active_episodes=active_episodes,
            seat_context=seat_context,
            timestamp_ms=timestamp_ms,
        )
        if attention_pattern:
            detected_patterns.append(attention_pattern)

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

        for key, expiry in list(self._emitted_evidence_keys.items()):
            if timestamp_ms > expiry:
                del self._emitted_evidence_keys[key]

        unique_patterns: List[BehaviorPattern] = []
        for pattern in detected_patterns:
            evidence_key = (
                pattern.pattern_type,
                pattern.seat_id,
                tuple(sorted(pattern.component_episode_ids)) + (f"segment:{pattern.metadata.get('evidence_segment', 0)}",),
            )
            if evidence_key in self._emitted_evidence_keys:
                continue
            self._emitted_evidence_keys[evidence_key] = timestamp_ms + self.glance_rolling_window_ms
            pattern.metadata["evidence_identity"] = "|".join(
                [pattern.pattern_type, pattern.seat_id, *evidence_key[2]]
            )
            unique_patterns.append(pattern)

        return unique_patterns

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
        ep_dict: Dict[str, TemporalEpisode] = {}
        for ep in self._episode_history.get(seat_id, []):
            ep_dict[ep.episode_id] = ep
        for ep in active_episodes:
            if ep.seat_id == seat_id and ep.state in (EpisodeState.ACTIVE, EpisodeState.ENDING):
                ep_dict[ep.episode_id] = ep
        all_recent = list(ep_dict.values())

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
        left_glances = self._count_distinct_glances(left_turns, timestamp_ms)
        right_glances = self._count_distinct_glances(right_turns, timestamp_ms)

        if left_glances >= self.min_glance_episodes:
            target_turns = left_turns
            target_dir = "LEFT"
            target_neighbor = left_neighbor
            glance_count = left_glances
        elif right_glances >= self.min_glance_episodes:
            target_turns = right_turns
            target_dir = "RIGHT"
            target_neighbor = right_neighbor
            glance_count = right_glances

        if not target_turns or target_neighbor is None:
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
            supporting_cues=[f"Glance Count: {glance_count} in {self.glance_rolling_window_ms/1000:.0f}s window"],
        )
        self._pattern_cooldowns[cooldown_key] = timestamp_ms
        return pattern

    def _count_distinct_glances(self, turns: List[TemporalEpisode], timestamp_ms: float) -> int:
        """Number of separate glances after merging fragments of one head turn."""
        if not turns:
            return 0
        ordered = sorted(turns, key=lambda ep: ep.start_timestamp_ms)
        glances = 1
        current_end = ordered[0].end_timestamp_ms if ordered[0].end_timestamp_ms is not None else timestamp_ms
        for ep in ordered[1:]:
            ep_end = ep.end_timestamp_ms if ep.end_timestamp_ms is not None else timestamp_ms
            if ep.start_timestamp_ms - current_end >= self.glance_merge_gap_ms:
                glances += 1
                current_end = ep_end
            else:
                current_end = max(current_end, ep_end)
        return glances

    def _evaluate_sustained_attention(
        self,
        active_episodes: List[TemporalEpisode],
        seat_context: SeatContext,
        timestamp_ms: float,
    ) -> Optional[BehaviorPattern]:
        """A head turn toward a configured neighbour held for seconds, with the
        head moved toward that neighbour over the shoulders for much of it.

        Two independent sources must agree: the head-pose model (yaw) and the
        pose keypoints (head offset).  Each is noisy on its own, rarely in the
        same direction at the same time.
        """
        if seat_context.capabilities.head_orientation == CapabilityStatus.DISABLED:
            return None
        if seat_context.capabilities.pairwise_relation == CapabilityStatus.DISABLED:
            return None

        seat_id = seat_context.seat_id
        current = [ep for ep in active_episodes if ep.seat_id == seat_id]
        known = {ep.episode_id: ep for ep in self._episode_history.get(seat_id, [])}
        known.update({ep.episode_id: ep for ep in current})

        for direction, neighbor_id in (
            ("LEFT", seat_context.neighbors.left_neighbor_id),
            ("RIGHT", seat_context.neighbors.right_neighbor_id),
        ):
            if neighbor_id is None:
                continue
            turn_type = EpisodeType.HEAD_TURN_LEFT.value if direction == "LEFT" else EpisodeType.HEAD_TURN_RIGHT.value
            offset_type = EpisodeType.HEAD_OFFSET_LEFT.value if direction == "LEFT" else EpisodeType.HEAD_OFFSET_RIGHT.value
            offsets = [ep for ep in known.values() if ep.episode_type == offset_type]
            for turn in current:
                if (
                    turn.episode_type != turn_type
                    or turn.state not in (EpisodeState.ACTIVE, EpisodeState.ENDING)
                    or turn.duration_ms < self.attention_min_turn_ms
                ):
                    continue
                times_counted, last_counted = self._attention_emitted_turns.get(turn.episode_id, (0, 0.0))
                if times_counted and (
                    self.attention_repeat_ms is None or timestamp_ms - last_counted < self.attention_repeat_ms
                ):
                    continue
                turn_end = turn.start_timestamp_ms + turn.duration_ms
                overlap = sum(
                    max(0.0, min(turn_end, off.end_timestamp_ms or timestamp_ms) - max(turn.start_timestamp_ms, off.start_timestamp_ms))
                    for off in offsets
                )
                share = overlap / max(turn.duration_ms, 1.0)
                if share < self.attention_min_offset_overlap:
                    continue

                cooldown_key = (seat_id, PatternType.SUSTAINED_NEIGHBOR_ATTENTION.value)
                if not times_counted and (timestamp_ms - self._pattern_cooldowns.get(cooldown_key, -100000.0)) < 10000.0:
                    return None
                self._pattern_cooldowns[cooldown_key] = timestamp_ms
                self._attention_emitted_turns[turn.episode_id] = (times_counted + 1, timestamp_ms)
                pattern = BehaviorPattern(
                    pattern_id=str(uuid.uuid4()),
                    seat_id=seat_id,
                    pattern_type=PatternType.SUSTAINED_NEIGHBOR_ATTENTION.value,
                    start_timestamp_ms=turn.start_timestamp_ms,
                    end_timestamp_ms=timestamp_ms,
                    confidence=turn.confidence,
                    quality=turn.quality,
                    primary_direction=direction,
                    target_neighbor_id=neighbor_id,
                    component_episode_ids=[turn.episode_id],
                    supporting_cues=[
                        f"Head turned {direction.lower()} for {turn.duration_ms / 1000:.1f}s",
                        f"Head moved toward the neighbour for {100 * min(1.0, share):.0f}% of it",
                    ],
                )
                # Each further stretch of the same look is its own evidence
                pattern.metadata["evidence_segment"] = times_counted
                return pattern
        return None

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
            confidence=min(1.0, target_ep.confidence * (1.15 if has_head_turn else 1.0)),
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
