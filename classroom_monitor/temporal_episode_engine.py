"""Temporal Episode Engine for Time-Bounded Behavior Modeling.

Implements Scope 07 (FR-EP-001 to FR-EP-005) of SRS v2.0:
- Converts raw continuous observations into time-bounded episodes.
- Episode Lifecycle: INACTIVE -> CANDIDATE -> ACTIVE -> ENDING -> ENDED.
- Pure millisecond timestamping (frame-rate independent).
- Dual-threshold hysteresis (activation vs release) and minimum persistence filtering.
- Eliminates frame spamming (one 10s movement produces 1 episode, not 300 frame alarms).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

import numpy as np

from classroom_monitor.observation_extractor import ObservationType, RawObservation, WristZone

logger = logging.getLogger("TemporalEpisodeEngine")


class EpisodeState(str, Enum):
    INACTIVE = "INACTIVE"
    CANDIDATE = "CANDIDATE"
    ACTIVE = "ACTIVE"
    ENDING = "ENDING"
    ENDED = "ENDED"


class EpisodeType(str, Enum):
    HEAD_TURN_LEFT = "HEAD_TURN_LEFT"
    HEAD_TURN_RIGHT = "HEAD_TURN_RIGHT"
    HEAD_PITCH_DOWN = "HEAD_PITCH_DOWN"
    TORSO_LEAN_LEFT = "TORSO_LEAN_LEFT"
    TORSO_LEAN_RIGHT = "TORSO_LEAN_RIGHT"
    WRIST_BELOW_DESK = "WRIST_BELOW_DESK"
    WRIST_WRITING = "WRIST_WRITING"
    SEAT_EMPTY = "SEAT_EMPTY"
    MULTI_PERSON_NEAR_SEAT = "MULTI_PERSON_NEAR_SEAT"


@dataclass
class TemporalEpisode:
    """Time-bounded episodic behavior representation for a Seat Actor."""

    episode_id: str
    seat_id: str
    episode_type: str
    state: EpisodeState = EpisodeState.ACTIVE
    start_timestamp_ms: float = 0.0
    peak_timestamp_ms: float = 0.0
    end_timestamp_ms: Optional[float] = None
    duration_ms: float = 0.0
    confidence: float = 1.0
    quality: float = 1.0
    peak_intensity: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "seat_id": self.seat_id,
            "episode_type": self.episode_type,
            "state": self.state.value,
            "start_timestamp_ms": round(self.start_timestamp_ms, 1),
            "peak_timestamp_ms": round(self.peak_timestamp_ms, 1),
            "end_timestamp_ms": round(self.end_timestamp_ms, 1) if self.end_timestamp_ms else None,
            "duration_ms": round(self.duration_ms, 1),
            "confidence": round(self.confidence, 3),
            "quality": round(self.quality, 3),
            "peak_intensity": round(self.peak_intensity, 2),
            "metadata": self.metadata,
        }


@dataclass
class _EpisodeTrackerState:
    """Internal state machine tracking an ongoing potential episode."""

    episode_type: str
    seat_id: str
    state: EpisodeState = EpisodeState.INACTIVE
    candidate_start_ms: float = 0.0
    active_start_ms: float = 0.0
    peak_timestamp_ms: float = 0.0
    peak_intensity: float = 0.0
    ending_start_ms: float = 0.0
    missing_start_ms: Optional[float] = None
    last_valid_timestamp_ms: Optional[float] = None
    confidences: List[float] = field(default_factory=list)
    qualities: List[float] = field(default_factory=list)
    current_episode_id: Optional[str] = None


class TemporalEpisodeEngine:
    """Stateful temporal engine converting raw observations into discrete episodes."""

    def __init__(
        self,
        min_persistence_ms: float = 400.0,       # Min time to promote CANDIDATE -> ACTIVE
        release_hysteresis_ms: float = 350.0,    # Persistence below release threshold to transition -> ENDED
        missing_observation_grace_ms: float = 1200.0, # Grace period before missing/unknown observation degrades episode
        yaw_activation_deg: float = 28.0,
        yaw_release_deg: float = 16.0,
        lean_activation_deg: float = 15.0,
        lean_release_deg: float = 8.0,
        pitch_down_activation_deg: float = 20.0,
    ):
        self.min_persistence_ms = min_persistence_ms
        self.release_hysteresis_ms = release_hysteresis_ms
        self.missing_observation_grace_ms = missing_observation_grace_ms
        self.yaw_activation_deg = yaw_activation_deg
        self.yaw_release_deg = yaw_release_deg
        self.lean_activation_deg = lean_activation_deg
        self.lean_release_deg = lean_release_deg
        self.pitch_down_activation_deg = pitch_down_activation_deg

        # (seat_id, episode_type) -> _EpisodeTrackerState
        self._trackers: Dict[Tuple[str, str], _EpisodeTrackerState] = {}
        self.completed_episodes: List[TemporalEpisode] = []

    def _get_tracker(self, seat_id: str, ep_type: str) -> _EpisodeTrackerState:
        key = (seat_id, ep_type)
        if key not in self._trackers:
            self._trackers[key] = _EpisodeTrackerState(episode_type=ep_type, seat_id=seat_id)
        return self._trackers[key]

    def process_observations(
        self,
        observations: List[RawObservation],
        timestamp_ms: float,
    ) -> List[TemporalEpisode]:
        """Ingest raw observations at timestamp_ms and return newly activated or updated episodes."""
        active_episodes: List[TemporalEpisode] = []

        # Map observations by type
        obs_map: Dict[str, RawObservation] = {obs.observation_type: obs for obs in observations}
        seat_id = observations[0].seat_id if observations else ""
        if not seat_id:
            return []

        # 1. Evaluate Head Turn Left / Right
        head_yaw_obs = obs_map.get(ObservationType.HEAD_YAW_RELATIVE.value)
        is_yaw_missing = (head_yaw_obs is None or head_yaw_obs.value is None)

        if not is_yaw_missing:
            yaw = float(head_yaw_obs.value)
            quality = head_yaw_obs.quality
            confidence = head_yaw_obs.confidence

            # Left Turn (negative yaw)
            is_active_left = yaw <= -self.yaw_activation_deg
            is_released_left = yaw > -self.yaw_release_deg
            ep_left = self._update_channel(
                seat_id=seat_id,
                ep_type=EpisodeType.HEAD_TURN_LEFT.value,
                is_active_condition=is_active_left,
                is_released_condition=is_released_left,
                intensity=abs(yaw),
                quality=quality,
                confidence=confidence,
                timestamp_ms=timestamp_ms,
                is_missing=False,
            )
            if ep_left:
                active_episodes.append(ep_left)

            # Right Turn (positive yaw)
            is_active_right = yaw >= self.yaw_activation_deg
            is_released_right = yaw < self.yaw_release_deg
            ep_right = self._update_channel(
                seat_id=seat_id,
                ep_type=EpisodeType.HEAD_TURN_RIGHT.value,
                is_active_condition=is_active_right,
                is_released_condition=is_released_right,
                intensity=yaw,
                quality=quality,
                confidence=confidence,
                timestamp_ms=timestamp_ms,
                is_missing=False,
            )
            if ep_right:
                active_episodes.append(ep_right)
        else:
            ep_left = self._update_channel(
                seat_id=seat_id,
                ep_type=EpisodeType.HEAD_TURN_LEFT.value,
                is_active_condition=False,
                is_released_condition=False,
                intensity=0.0,
                quality=0.0,
                confidence=0.0,
                timestamp_ms=timestamp_ms,
                is_missing=True,
            )
            if ep_left:
                active_episodes.append(ep_left)

            ep_right = self._update_channel(
                seat_id=seat_id,
                ep_type=EpisodeType.HEAD_TURN_RIGHT.value,
                is_active_condition=False,
                is_released_condition=False,
                intensity=0.0,
                quality=0.0,
                confidence=0.0,
                timestamp_ms=timestamp_ms,
                is_missing=True,
            )
            if ep_right:
                active_episodes.append(ep_right)

        # 2. Evaluate Head Pitch Down (Contextual Observation Episode)
        pitch_obs = obs_map.get(ObservationType.HEAD_PITCH_RELATIVE_DOWN.value)
        is_pitch_missing = (pitch_obs is None or pitch_obs.value is None)

        if not is_pitch_missing:
            pitch = float(pitch_obs.value)
            is_down = pitch >= self.pitch_down_activation_deg
            is_released = pitch < (self.pitch_down_activation_deg - 8.0)
            ep_pitch = self._update_channel(
                seat_id=seat_id,
                ep_type=EpisodeType.HEAD_PITCH_DOWN.value,
                is_active_condition=is_down,
                is_released_condition=is_released,
                intensity=pitch,
                quality=pitch_obs.quality,
                confidence=pitch_obs.confidence,
                timestamp_ms=timestamp_ms,
                is_missing=False,
            )
            if ep_pitch:
                active_episodes.append(ep_pitch)
        else:
            ep_pitch = self._update_channel(
                seat_id=seat_id,
                ep_type=EpisodeType.HEAD_PITCH_DOWN.value,
                is_active_condition=False,
                is_released_condition=False,
                intensity=0.0,
                quality=0.0,
                confidence=0.0,
                timestamp_ms=timestamp_ms,
                is_missing=True,
            )
            if ep_pitch:
                active_episodes.append(ep_pitch)

        # 3. Evaluate Torso Lean Left / Right
        lean_obs = obs_map.get(ObservationType.TORSO_LEAN_X.value)
        is_lean_missing = (lean_obs is None or lean_obs.value is None)

        if not is_lean_missing:
            angle = float(lean_obs.value)
            # Left Lean
            is_lean_left = angle <= -self.lean_activation_deg
            is_rel_left = angle > -self.lean_release_deg
            ep_lean_l = self._update_channel(
                seat_id=seat_id,
                ep_type=EpisodeType.TORSO_LEAN_LEFT.value,
                is_active_condition=is_lean_left,
                is_released_condition=is_rel_left,
                intensity=abs(angle),
                quality=lean_obs.quality,
                confidence=lean_obs.confidence,
                timestamp_ms=timestamp_ms,
                is_missing=False,
            )
            if ep_lean_l:
                active_episodes.append(ep_lean_l)

            # Right Lean
            is_lean_right = angle >= self.lean_activation_deg
            is_rel_right = angle < self.lean_release_deg
            ep_lean_r = self._update_channel(
                seat_id=seat_id,
                ep_type=EpisodeType.TORSO_LEAN_RIGHT.value,
                is_active_condition=is_lean_right,
                is_released_condition=is_rel_right,
                intensity=angle,
                quality=lean_obs.quality,
                confidence=lean_obs.confidence,
                timestamp_ms=timestamp_ms,
                is_missing=False,
            )
            if ep_lean_r:
                active_episodes.append(ep_lean_r)
        else:
            ep_lean_l = self._update_channel(
                seat_id=seat_id,
                ep_type=EpisodeType.TORSO_LEAN_LEFT.value,
                is_active_condition=False,
                is_released_condition=False,
                intensity=0.0,
                quality=0.0,
                confidence=0.0,
                timestamp_ms=timestamp_ms,
                is_missing=True,
            )
            if ep_lean_l:
                active_episodes.append(ep_lean_l)

            ep_lean_r = self._update_channel(
                seat_id=seat_id,
                ep_type=EpisodeType.TORSO_LEAN_RIGHT.value,
                is_active_condition=False,
                is_released_condition=False,
                intensity=0.0,
                quality=0.0,
                confidence=0.0,
                timestamp_ms=timestamp_ms,
                is_missing=True,
            )
            if ep_lean_r:
                active_episodes.append(ep_lean_r)

        # 4. Evaluate Wrist Zones
        lw_obs = obs_map.get(ObservationType.LEFT_WRIST_ZONE.value)
        rw_obs = obs_map.get(ObservationType.RIGHT_WRIST_ZONE.value)
        has_wrist_obs = (lw_obs is not None and lw_obs.value != WristZone.UNKNOWN.value) or (
            rw_obs is not None and rw_obs.value != WristZone.UNKNOWN.value
        )

        if has_wrist_obs:
            is_under_desk = (lw_obs and lw_obs.value == WristZone.UNDER_DESK.value) or (
                rw_obs and rw_obs.value == WristZone.UNDER_DESK.value
            )
            is_writing = (lw_obs and lw_obs.value == WristZone.WRITING.value) or (
                rw_obs and rw_obs.value == WristZone.WRITING.value
            )

            ep_under = self._update_channel(
                seat_id=seat_id,
                ep_type=EpisodeType.WRIST_BELOW_DESK.value,
                is_active_condition=bool(is_under_desk),
                is_released_condition=bool(is_writing or not is_under_desk),
                intensity=1.0,
                quality=max(lw_obs.quality if lw_obs else 0.0, rw_obs.quality if rw_obs else 0.0),
                confidence=1.0,
                timestamp_ms=timestamp_ms,
                is_missing=False,
            )
            if ep_under:
                active_episodes.append(ep_under)
        else:
            ep_under = self._update_channel(
                seat_id=seat_id,
                ep_type=EpisodeType.WRIST_BELOW_DESK.value,
                is_active_condition=False,
                is_released_condition=False,
                intensity=0.0,
                quality=0.0,
                confidence=0.0,
                timestamp_ms=timestamp_ms,
                is_missing=True,
            )
            if ep_under:
                active_episodes.append(ep_under)

        # 5. Evaluate Seat Empty
        occ_obs = obs_map.get(ObservationType.SEAT_OCCUPANCY.value)
        if occ_obs is not None and occ_obs.value in ("EMPTY", "OCCUPIED", "OCCLUDED", "MULTIPLE_PERSON"):
            is_empty = (occ_obs.value == "EMPTY")
            is_released = (occ_obs.value != "EMPTY")
            ep_empty = self._update_channel(
                seat_id=seat_id,
                ep_type=EpisodeType.SEAT_EMPTY.value,
                is_active_condition=bool(is_empty),
                is_released_condition=bool(is_released),
                intensity=1.0,
                quality=1.0,
                confidence=1.0,
                timestamp_ms=timestamp_ms,
                is_missing=False,
            )
            if ep_empty:
                active_episodes.append(ep_empty)
        else:
            ep_empty = self._update_channel(
                seat_id=seat_id,
                ep_type=EpisodeType.SEAT_EMPTY.value,
                is_active_condition=False,
                is_released_condition=False,
                intensity=0.0,
                quality=0.0,
                confidence=0.0,
                timestamp_ms=timestamp_ms,
                is_missing=True,
            )
            if ep_empty:
                active_episodes.append(ep_empty)

        # 6. Evaluate Multiple Persons Near Seat
        pcount_obs = obs_map.get(ObservationType.PERSON_COUNT_NEAR_SEAT.value)
        if pcount_obs is not None and pcount_obs.value is not None:
            count = int(pcount_obs.value or 0)
            is_multi = (count > 1)
            is_released = (count <= 1)
            ep_multi = self._update_channel(
                seat_id=seat_id,
                ep_type=EpisodeType.MULTI_PERSON_NEAR_SEAT.value,
                is_active_condition=bool(is_multi),
                is_released_condition=bool(is_released),
                intensity=float(count),
                quality=1.0,
                confidence=1.0,
                timestamp_ms=timestamp_ms,
                is_missing=False,
            )
            if ep_multi:
                active_episodes.append(ep_multi)
        else:
            ep_multi = self._update_channel(
                seat_id=seat_id,
                ep_type=EpisodeType.MULTI_PERSON_NEAR_SEAT.value,
                is_active_condition=False,
                is_released_condition=False,
                intensity=0.0,
                quality=0.0,
                confidence=0.0,
                timestamp_ms=timestamp_ms,
                is_missing=True,
            )
            if ep_multi:
                active_episodes.append(ep_multi)

        return active_episodes

    def _update_channel(
        self,
        seat_id: str,
        ep_type: str,
        is_active_condition: bool,
        is_released_condition: bool,
        intensity: float,
        quality: float,
        confidence: float,
        timestamp_ms: float,
        is_missing: bool = False,
    ) -> Optional[TemporalEpisode]:
        """Generic dual-threshold state machine for an episodic channel with missing grace mechanism."""
        tracker = self._get_tracker(seat_id, ep_type)

        if is_missing:
            # Handle Missing / Unknown observation
            if tracker.state == EpisodeState.CANDIDATE:
                if tracker.missing_start_ms is None:
                    tracker.missing_start_ms = timestamp_ms
                if (timestamp_ms - tracker.missing_start_ms) > self.missing_observation_grace_ms:
                    tracker.state = EpisodeState.INACTIVE
                    tracker.missing_start_ms = None

            elif tracker.state in (EpisodeState.ACTIVE, EpisodeState.ENDING):
                if tracker.missing_start_ms is None:
                    tracker.missing_start_ms = timestamp_ms
                if (timestamp_ms - tracker.missing_start_ms) > self.missing_observation_grace_ms:
                    # Missing period persisted beyond grace timeout -> gracefully close episode
                    tracker.state = EpisodeState.ENDED
                    end_time = tracker.last_valid_timestamp_ms or timestamp_ms
                    duration = max(0.0, end_time - tracker.active_start_ms)
                    mean_conf = float(np.mean(tracker.confidences)) if tracker.confidences else 1.0
                    mean_qual = float(np.mean(tracker.qualities)) if tracker.qualities else 1.0

                    completed_ep = TemporalEpisode(
                        episode_id=tracker.current_episode_id or str(uuid.uuid4()),
                        seat_id=seat_id,
                        episode_type=ep_type,
                        state=EpisodeState.ENDED,
                        start_timestamp_ms=tracker.active_start_ms,
                        peak_timestamp_ms=tracker.peak_timestamp_ms,
                        end_timestamp_ms=end_time,
                        duration_ms=duration,
                        confidence=mean_conf,
                        quality=mean_qual,
                        peak_intensity=tracker.peak_intensity,
                    )
                    self.completed_episodes.append(completed_ep)
                    tracker.missing_start_ms = None
                    return completed_ep

            # If inside grace period, preserve active status
            if tracker.state in (EpisodeState.ACTIVE, EpisodeState.ENDING):
                duration = max(0.0, (tracker.last_valid_timestamp_ms or timestamp_ms) - tracker.active_start_ms)
                mean_conf = float(np.mean(tracker.confidences)) if tracker.confidences else 1.0
                mean_qual = float(np.mean(tracker.qualities)) if tracker.qualities else 1.0
                return TemporalEpisode(
                    episode_id=tracker.current_episode_id or str(uuid.uuid4()),
                    seat_id=seat_id,
                    episode_type=ep_type,
                    state=tracker.state,
                    start_timestamp_ms=tracker.active_start_ms,
                    peak_timestamp_ms=tracker.peak_timestamp_ms,
                    end_timestamp_ms=None,
                    duration_ms=duration,
                    confidence=mean_conf,
                    quality=mean_qual,
                    peak_intensity=tracker.peak_intensity,
                )

            return None

        # Observation is VALID (not missing)
        tracker.missing_start_ms = None
        tracker.last_valid_timestamp_ms = timestamp_ms

        if is_active_condition:
            tracker.confidences.append(confidence)
            tracker.qualities.append(quality)
            if intensity > tracker.peak_intensity:
                tracker.peak_intensity = intensity
                tracker.peak_timestamp_ms = timestamp_ms

            if tracker.state == EpisodeState.INACTIVE or tracker.state == EpisodeState.ENDED:
                tracker.state = EpisodeState.CANDIDATE
                tracker.candidate_start_ms = timestamp_ms
                tracker.current_episode_id = str(uuid.uuid4())
                tracker.peak_intensity = intensity
                tracker.peak_timestamp_ms = timestamp_ms
                tracker.confidences = [confidence]
                tracker.qualities = [quality]

            elif tracker.state == EpisodeState.CANDIDATE:
                if (timestamp_ms - tracker.candidate_start_ms) >= self.min_persistence_ms:
                    tracker.state = EpisodeState.ACTIVE
                    tracker.active_start_ms = tracker.candidate_start_ms

            elif tracker.state == EpisodeState.ENDING:
                # Cancel ending transition and resume active
                tracker.state = EpisodeState.ACTIVE

        elif is_released_condition:
            if tracker.state == EpisodeState.CANDIDATE:
                # Dropped before meeting persistence threshold
                tracker.state = EpisodeState.INACTIVE

            elif tracker.state == EpisodeState.ACTIVE:
                tracker.state = EpisodeState.ENDING
                tracker.ending_start_ms = timestamp_ms

            elif tracker.state == EpisodeState.ENDING:
                if (timestamp_ms - tracker.ending_start_ms) >= self.release_hysteresis_ms:
                    tracker.state = EpisodeState.ENDED
                    duration = max(0.0, timestamp_ms - tracker.active_start_ms)
                    mean_conf = float(np.mean(tracker.confidences)) if tracker.confidences else 1.0
                    mean_qual = float(np.mean(tracker.qualities)) if tracker.qualities else 1.0

                    completed_ep = TemporalEpisode(
                        episode_id=tracker.current_episode_id or str(uuid.uuid4()),
                        seat_id=seat_id,
                        episode_type=ep_type,
                        state=EpisodeState.ENDED,
                        start_timestamp_ms=tracker.active_start_ms,
                        peak_timestamp_ms=tracker.peak_timestamp_ms,
                        end_timestamp_ms=timestamp_ms,
                        duration_ms=duration,
                        confidence=mean_conf,
                        quality=mean_qual,
                        peak_intensity=tracker.peak_intensity,
                    )
                    self.completed_episodes.append(completed_ep)
                    return completed_ep

        # Return live active episode object if currently ACTIVE or ENDING
        if tracker.state in (EpisodeState.ACTIVE, EpisodeState.ENDING):
            duration = max(0.0, timestamp_ms - tracker.active_start_ms)
            mean_conf = float(np.mean(tracker.confidences)) if tracker.confidences else 1.0
            mean_qual = float(np.mean(tracker.qualities)) if tracker.qualities else 1.0
            return TemporalEpisode(
                episode_id=tracker.current_episode_id or str(uuid.uuid4()),
                seat_id=seat_id,
                episode_type=ep_type,
                state=tracker.state,
                start_timestamp_ms=tracker.active_start_ms,
                peak_timestamp_ms=tracker.peak_timestamp_ms,
                end_timestamp_ms=None,
                duration_ms=duration,
                confidence=mean_conf,
                quality=mean_qual,
                peak_intensity=tracker.peak_intensity,
            )

        return None
