"""Temporal Risk Prioritization Engine and Canonical Per-Seat State Machine.

Implements Scope 09 (FR-RISK-001 to FR-RISK-009) and Scope 10 of SRS v2.0:
- Review Priority Scoring (0–100), NOT probability of cheating.
- Driven exclusively by Temporal Episodes and Composite Patterns (no frame-weighted spam).
- Exponential time decay with diminishing returns on repetitive evidence.
- Canonical 4-tier state bands:
    0–29:   NORMAL
    30–59:  OBSERVE
    60–79:  SUSPICIOUS
    80–100: FLAGGED_FOR_REVIEW
    COOLDOWN (silent background tracking) -> NORMAL
- Absolutely NO 'CHEATING' state.
- Zero direct Risk contribution from LOOK_DOWN / HEAD_PITCH_DOWN.
"""

from __future__ import annotations

import logging
import math
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from classroom_monitor.behavior_pattern_engine import BehaviorPattern, PatternType
from classroom_monitor.models import ClassroomEvent, Detection, SeverityLevel
from classroom_monitor.temporal_episode_engine import EpisodeState, EpisodeType, TemporalEpisode

logger = logging.getLogger("SeatRiskTracker")


class RiskState(str, Enum):
    NORMAL = "NORMAL"
    OBSERVE = "OBSERVE"
    SUSPICIOUS = "SUSPICIOUS"
    FLAGGED_FOR_REVIEW = "FLAGGED_FOR_REVIEW"
    COOLDOWN = "COOLDOWN"


# Episode base priority weights (one-shot per episode lifecycle)
EPISODE_PRIORITY_WEIGHTS: Dict[str, float] = {
    EpisodeType.HEAD_TURN_LEFT.value: 12.0,
    EpisodeType.HEAD_TURN_RIGHT.value: 12.0,
    EpisodeType.HEAD_PITCH_DOWN.value: 0.0,       # MANDATORY: Zero direct risk contribution
    EpisodeType.TORSO_LEAN_LEFT.value: 14.0,
    EpisodeType.TORSO_LEAN_RIGHT.value: 14.0,
    EpisodeType.WRIST_BELOW_DESK.value: 18.0,
    EpisodeType.WRIST_WRITING.value: 0.0,
    EpisodeType.SEAT_EMPTY.value: 0.0,            # Passive empty state does not contribute risk; prolonged absence handled by SEAT_LEFT
    EpisodeType.MULTI_PERSON_NEAR_SEAT.value: 20.0,
}

# Composite Pattern base priority weights
PATTERN_PRIORITY_WEIGHTS: Dict[str, float] = {
    PatternType.REPEATED_NEIGHBOR_GLANCE.value: 38.0,
    PatternType.NEIGHBOR_ORIENTED_LEAN.value: 32.0,
    PatternType.SEAT_LEFT.value: 45.0,
    PatternType.MULTI_PERSON_DWELL_NEAR_SEAT.value: 40.0,
    PatternType.BELOW_DESK_INTERACTION.value: 35.0,
}


@dataclass
class SeatRiskProfile:
    """Per-seat state machine, episodic risk accumulation, and active incident profile."""

    seat_id: str
    room_id: str = ""
    camera_id: Optional[str] = None

    current_state: str = RiskState.NORMAL.value
    risk_score: float = 0.0  # 0.0 to 100.0
    peak_risk_score: float = 0.0

    last_update_timestamp_ms: float = 0.0
    state_enter_timestamp_ms: float = 0.0
    cooldown_enter_timestamp_ms: float = 0.0

    processed_episode_ids: set[str] = field(default_factory=set)
    processed_pattern_ids: set[str] = field(default_factory=set)
    pattern_history_counts: Dict[str, int] = field(default_factory=dict)
    recent_patterns: deque[BehaviorPattern] = field(default_factory=lambda: deque(maxlen=10))

    peak_detection: Optional[Detection] = None
    peak_frame_image: Optional[np.ndarray] = None
    peak_pattern: Optional[BehaviorPattern] = None

    total_event_count: int = 0
    total_incidents_count: int = 0
    last_event_timestamp_ms: float = 0.0
    is_recidivist: bool = False

    active_incident: Optional[Dict[str, Any]] = None
    active_event: Optional[ClassroomEvent] = None


class SeatRiskTracker:
    """Manages Review Priority Scores and Human-Reviewable Incidents across all monitored seats."""

    def __init__(
        self,
        room_id: str = "",
        camera_id: Optional[str] = None,
        decay_rate_per_sec: float = 2.5,
        cooldown_duration_ms: float = 5000.0,
        recidivism_window_ms: float = 15000.0,
        incident_merge_window_ms: float = 0.0,
        observe_threshold: float = 30.0,
        suspicious_threshold: float = 60.0,
        flagged_threshold: float = 80.0,
        post_event_reset_score: float = 45.0,
    ):
        self.room_id = room_id
        self.camera_id = camera_id
        self.decay_rate_per_sec = decay_rate_per_sec
        self.cooldown_duration_ms = cooldown_duration_ms
        self.recidivism_window_ms = recidivism_window_ms
        self.incident_merge_window_ms = incident_merge_window_ms
        self.observe_threshold = observe_threshold
        self.suspicious_threshold = suspicious_threshold
        self.flagged_threshold = flagged_threshold
        self.post_event_reset_score = post_event_reset_score

        self.profiles: Dict[str, SeatRiskProfile] = {}

    def get_or_create_profile(self, seat_id: str, camera_id: Optional[str] = None) -> SeatRiskProfile:
        if seat_id not in self.profiles:
            self.profiles[seat_id] = SeatRiskProfile(
                seat_id=seat_id,
                room_id=self.room_id,
                camera_id=camera_id or self.camera_id,
            )
        elif camera_id and not self.profiles[seat_id].camera_id:
            self.profiles[seat_id].camera_id = camera_id
        return self.profiles[seat_id]

    def update_seat(
        self,
        seat_id: str,
        active_episodes: List[TemporalEpisode],
        detected_patterns: List[BehaviorPattern],
        timestamp_ms: float,
        detection: Optional[Detection] = None,
        frame_image: Optional[np.ndarray] = None,
        camera_id: Optional[str] = None,
    ) -> Optional[ClassroomEvent]:
        """Update seat risk using temporal decay, episode increments, pattern bonuses, and incident aggregation."""
        profile = self.get_or_create_profile(seat_id, camera_id=camera_id)

        dt_sec = max(0.0, (timestamp_ms - profile.last_update_timestamp_ms) / 1000.0) if profile.last_update_timestamp_ms > 0 else 0.1
        profile.last_update_timestamp_ms = timestamp_ms

        # 1. Check Cooldown
        in_cooldown = (
            profile.current_state == RiskState.COOLDOWN.value
            and (timestamp_ms - profile.cooldown_enter_timestamp_ms) < self.cooldown_duration_ms
        )

        # 2. Check Active Incident Expiration (Merge Window)
        if profile.active_incident is not None:
            if (timestamp_ms - profile.active_incident["last_seen_timestamp_ms"]) > self.incident_merge_window_ms:
                profile.active_incident = None
                profile.active_event = None
                profile.pattern_history_counts.clear()

        # 3. Apply Time Decay
        if dt_sec > 0:
            decay_amount = self.decay_rate_per_sec * dt_sec
            profile.risk_score = max(0.0, profile.risk_score - decay_amount)

        # 4. Ingest New Episode Contributions
        for ep in active_episodes:
            if ep.seat_id == seat_id and ep.episode_id not in profile.processed_episode_ids:
                base_w = EPISODE_PRIORITY_WEIGHTS.get(ep.episode_type, 0.0)
                if base_w > 0:
                    profile.risk_score = min(100.0, profile.risk_score + (base_w * ep.quality))
                profile.processed_episode_ids.add(ep.episode_id)

        # 5. Ingest Composite Pattern Contributions (with Diminishing Returns and Pattern Deduplication)
        for pat in detected_patterns:
            if pat.seat_id == seat_id and pat.pattern_id not in profile.processed_pattern_ids:
                base_pw = PATTERN_PRIORITY_WEIGHTS.get(pat.pattern_type, 30.0)
                history_count = profile.pattern_history_counts.get(pat.pattern_type, 0)
                # Diminishing return factor: 1.0, 0.74, 0.58, 0.49...
                diminishing_factor = 1.0 / (1.0 + 0.35 * history_count)
                increment = base_pw * diminishing_factor * pat.quality * pat.confidence
                profile.risk_score = min(100.0, profile.risk_score + increment)
                profile.pattern_history_counts[pat.pattern_type] = history_count + 1
                profile.processed_pattern_ids.add(pat.pattern_id)
                profile.recent_patterns.append(pat)

                if pat.pattern_type in PATTERN_PRIORITY_WEIGHTS:
                    profile.peak_pattern = pat

                # Update ongoing active incident if present
                if profile.active_incident is not None:
                    profile.active_incident["occurrence_count"] = profile.active_incident.get("occurrence_count", 1) + 1
                    profile.active_incident["last_seen_timestamp_ms"] = timestamp_ms
                    profile.active_incident["peak_risk_score"] = max(profile.active_incident.get("peak_risk_score", profile.risk_score), profile.risk_score)
                    if pat.pattern_id not in profile.active_incident.setdefault("supporting_pattern_ids", []):
                        profile.active_incident["supporting_pattern_ids"].append(pat.pattern_id)
                    if pat.component_episode_ids:
                        for ep_id in pat.component_episode_ids:
                            if ep_id not in profile.active_incident["component_episode_ids"]:
                                profile.active_incident["component_episode_ids"].append(ep_id)

                    # Synchronize active ClassroomEvent in-place so exported events match final incident state
                    if profile.active_event is not None:
                        profile.active_event.metadata["occurrence_count"] = profile.active_incident["occurrence_count"]
                        profile.active_event.metadata["last_seen_ms"] = timestamp_ms
                        profile.active_event.metadata["last_seen_timestamp_ms"] = timestamp_ms
                        profile.active_event.metadata["peak_risk_score"] = round(profile.active_incident["peak_risk_score"], 1)
                        profile.active_event.metadata["component_episode_ids"] = list(profile.active_incident["component_episode_ids"])
                        profile.active_event.metadata["supporting_pattern_ids"] = list(profile.active_incident["supporting_pattern_ids"])

        # 6. Check Evidence Diversity / Correlation Bonus
        active_types = {ep.episode_type for ep in active_episodes if ep.seat_id == seat_id and ep.state == EpisodeState.ACTIVE}
        if (
            (EpisodeType.HEAD_TURN_LEFT.value in active_types or EpisodeType.HEAD_TURN_RIGHT.value in active_types)
            and (EpisodeType.TORSO_LEAN_LEFT.value in active_types or EpisodeType.TORSO_LEAN_RIGHT.value in active_types)
        ):
            # Correlated head + torso movement bonus
            profile.risk_score = min(100.0, profile.risk_score + (3.0 * dt_sec))

        # Track Peak values
        if profile.risk_score > profile.peak_risk_score:
            profile.peak_risk_score = profile.risk_score
            if detection:
                profile.peak_detection = detection
            if frame_image is not None:
                profile.peak_frame_image = frame_image.copy()

        # 7. Recidivism Check (Measured escalation only when not in ongoing incident)
        is_recidivist = (
            profile.total_event_count > 0
            and profile.active_incident is None
            and (timestamp_ms - profile.last_event_timestamp_ms) < self.recidivism_window_ms
        )
        profile.is_recidivist = is_recidivist
        if is_recidivist and profile.risk_score >= self.suspicious_threshold:
            profile.risk_score = min(100.0, profile.risk_score + 10.0)

        # 8. State Machine Evaluation
        previous_state = profile.current_state
        new_state = previous_state

        if in_cooldown:
            new_state = RiskState.COOLDOWN.value
        elif profile.risk_score >= self.flagged_threshold:
            new_state = RiskState.FLAGGED_FOR_REVIEW.value
        elif profile.risk_score >= self.suspicious_threshold:
            new_state = RiskState.SUSPICIOUS.value
        elif profile.risk_score >= self.observe_threshold:
            new_state = RiskState.OBSERVE.value
        else:
            new_state = RiskState.NORMAL.value

        event_to_emit: Optional[ClassroomEvent] = None

        # State Transition & Incident Aggregation Handlers
        if new_state == RiskState.FLAGGED_FOR_REVIEW.value and not in_cooldown:
            profile.current_state = new_state
            profile.state_enter_timestamp_ms = timestamp_ms

            primary_pattern_name = profile.peak_pattern.pattern_type if profile.peak_pattern else "SUSPICIOUS_POSTURE"
            supporting_cues = profile.peak_pattern.supporting_cues if profile.peak_pattern else []
            pat_id = profile.peak_pattern.pattern_id if profile.peak_pattern else ""

            # Check for existing active incident of the same behavior to MERGE
            active_inc = profile.active_incident
            can_merge = (
                self.incident_merge_window_ms > 0
                and active_inc is not None
                and active_inc.get("behavior") == primary_pattern_name
                and (timestamp_ms - active_inc.get("last_seen_timestamp_ms", 0.0)) <= self.incident_merge_window_ms
            )

            if can_merge:
                # Merge into ongoing incident: update occurrence_count, peak risk, last seen
                active_inc["occurrence_count"] = active_inc.get("occurrence_count", 1) + 1
                active_inc["last_seen_timestamp_ms"] = timestamp_ms
                active_inc["peak_risk_score"] = max(active_inc.get("peak_risk_score", profile.risk_score), profile.risk_score)
                if pat_id and pat_id not in active_inc.setdefault("supporting_pattern_ids", []):
                    active_inc["supporting_pattern_ids"].append(pat_id)
                if profile.peak_pattern:
                    for ep_id in profile.peak_pattern.component_episode_ids:
                        if ep_id not in active_inc["component_episode_ids"]:
                            active_inc["component_episode_ids"].append(ep_id)

                # Synchronize existing active event metadata
                if profile.active_event is not None:
                    profile.active_event.metadata["occurrence_count"] = active_inc["occurrence_count"]
                    profile.active_event.metadata["last_seen_ms"] = timestamp_ms
                    profile.active_event.metadata["last_seen_timestamp_ms"] = timestamp_ms
                    profile.active_event.metadata["peak_risk_score"] = round(active_inc["peak_risk_score"], 1)
                    profile.active_event.metadata["component_episode_ids"] = list(active_inc["component_episode_ids"])
                    profile.active_event.metadata["supporting_pattern_ids"] = list(active_inc.get("supporting_pattern_ids", []))

                # Enter Cooldown without emitting a duplicate event
                profile.current_state = RiskState.COOLDOWN.value
                profile.cooldown_enter_timestamp_ms = timestamp_ms
                profile.risk_score = self.post_event_reset_score

            else:
                # Emit a DISTINCT new human incident event
                new_inc_id = str(uuid.uuid4())
                initial_pattern_ids = [p.pattern_id for p in detected_patterns if p.seat_id == seat_id]
                if pat_id and pat_id not in initial_pattern_ids:
                    initial_pattern_ids.append(pat_id)
                initial_ep_ids = []
                for p in detected_patterns:
                    if p.seat_id == seat_id and p.component_episode_ids:
                        for ep_id in p.component_episode_ids:
                            if ep_id not in initial_ep_ids:
                                initial_ep_ids.append(ep_id)
                if profile.peak_pattern:
                    for ep_id in profile.peak_pattern.component_episode_ids:
                        if ep_id not in initial_ep_ids:
                            initial_ep_ids.append(ep_id)

                profile.active_incident = {
                    "incident_id": new_inc_id,
                    "seat_id": seat_id,
                    "behavior": primary_pattern_name,
                    "start_timestamp_ms": timestamp_ms,
                    "last_seen_timestamp_ms": timestamp_ms,
                    "occurrence_count": 1,
                    "peak_risk_score": profile.risk_score,
                    "component_episode_ids": initial_ep_ids,
                    "supporting_pattern_ids": initial_pattern_ids,
                }
                profile.total_incidents_count += 1
                profile.total_event_count += 1
                profile.last_event_timestamp_ms = timestamp_ms

                severity = SeverityLevel.HIGH.value if (profile.risk_score >= 85.0 or is_recidivist) else SeverityLevel.MEDIUM.value

                # Extract genuine actor track ID if available from detector tracking
                actual_track_id = None
                if detection is not None and hasattr(detection, "track_id") and detection.track_id is not None:
                    try:
                        actual_track_id = int(detection.track_id)
                    except (ValueError, TypeError):
                        actual_track_id = None

                event_to_emit = ClassroomEvent(
                    event_id=new_inc_id,
                    track_id=actual_track_id or 0,
                    behavior=primary_pattern_name,
                    confidence=float(profile.peak_pattern.confidence if profile.peak_pattern else 0.85),
                    severity=severity,
                    timestamp=timestamp_ms / 1000.0,
                    timestamp_ms=timestamp_ms,
                    bbox=profile.peak_detection.bbox if profile.peak_detection else (0.0, 0.0, 0.0, 0.0),
                    frame_index=int(timestamp_ms / 33.33),
                    evidence_frame=profile.peak_frame_image if profile.peak_frame_image is not None else frame_image,
                    seat_id=seat_id,
                    room_id=profile.room_id,
                    camera_id=profile.camera_id,
                    is_recidivist=profile.is_recidivist,
                    metadata={
                        "risk_score": round(profile.risk_score, 1),
                        "peak_risk_score": round(profile.peak_risk_score, 1),
                        "primary_pattern": primary_pattern_name,
                        "supporting_cues": supporting_cues,
                        "observation_quality": round(profile.peak_pattern.quality if profile.peak_pattern else 0.85, 3),
                        "component_episode_ids": list(profile.active_incident["component_episode_ids"]),
                        "supporting_pattern_ids": list(profile.active_incident["supporting_pattern_ids"]),
                        "incident_id": new_inc_id,
                        "first_seen_ms": round(timestamp_ms, 1),
                        "last_seen_ms": round(timestamp_ms, 1),
                        "occurrence_count": 1,
                    },
                )
                profile.active_event = event_to_emit

                # Enter Cooldown
                profile.current_state = RiskState.COOLDOWN.value
                profile.cooldown_enter_timestamp_ms = timestamp_ms
                profile.risk_score = self.post_event_reset_score

        elif new_state != previous_state:
            profile.current_state = new_state
            profile.state_enter_timestamp_ms = timestamp_ms

        return event_to_emit

