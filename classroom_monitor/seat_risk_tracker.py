"""Temporal Risk Scoring Engine and Per-Seat Behavioral State Machine.

Implements Scope 06 (FR-RISK-001 to FR-RISK-009, P0-07, P0-08) of SRS v1.0:
- Per-Seat Identity lifecycle management (resilient to detector ID switches).
- Normalized 0–100 Risk Score accumulator over time-aware sliding window.
- 4-Tier State Bands:
    0–29:   NORMAL
    30–59:  OBSERVE
    60–79:  SUSPICIOUS
    80–100: FLAGGED_FOR_REVIEW
- Weighted signal contributions and smooth exponential/linear decay.
- Silent background tracking during Cooldown (5.0s).
- Rapid Recidivism Escalation.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from classroom_monitor.behavior_signals import BehaviorSignal, SignalType
from classroom_monitor.models import ClassroomEvent, Detection, SeverityLevel

logger = logging.getLogger("SeatRiskTracker")


class RiskState(str, Enum):
    NORMAL = "NORMAL"
    OBSERVE = "OBSERVE"
    SUSPICIOUS = "SUSPICIOUS"
    FLAGGED_FOR_REVIEW = "FLAGGED_FOR_REVIEW"
    COOLDOWN = "COOLDOWN"


@dataclass
class SeatRiskProfile:
    """Live state machine and risk accumulation container for a specific physical seat."""

    seat_id: str
    room_id: str = ""
    camera_id: Optional[str] = None

    current_state: str = RiskState.NORMAL.value
    risk_score: float = 0.0  # 0 to 100
    peak_risk_score: float = 0.0

    last_update_timestamp_ms: float = 0.0
    state_enter_timestamp_ms: float = 0.0
    cooldown_enter_timestamp_ms: float = 0.0

    recent_signals: deque[BehaviorSignal] = field(default_factory=lambda: deque(maxlen=30))
    peak_detection: Optional[Detection] = None
    peak_frame_image: Optional[np.ndarray] = None

    total_event_count: int = 0
    last_event_timestamp_ms: float = 0.0
    is_recidivist: bool = False


class SeatRiskTracker:
    """Manages risk accumulation across all seats in a classroom."""

    def __init__(
        self, 
        room_id: str = "", 
        config: Optional['ClassroomConfig'] = None,
        window_duration_ms: Optional[float] = None,
        cooldown_duration_ms: Optional[float] = None,
        recidivism_window_ms: Optional[float] = None,
        decay_rate_per_sec: Optional[float] = None,
        combination_weights: Optional[Dict[str, float]] = None,
    ):
        from classroom_monitor.config import DEFAULT_CONFIG
        self.config = config or DEFAULT_CONFIG
        self.room_id = room_id
        
        self.window_duration_ms = window_duration_ms if window_duration_ms is not None else self.config.window_duration_ms
        self.cooldown_duration_ms = cooldown_duration_ms if cooldown_duration_ms is not None else (self.config.cooldown_seconds * 1000.0)
        self.recidivism_window_ms = recidivism_window_ms if recidivism_window_ms is not None else (self.config.recidivism_window_seconds * 1000.0)
        self.decay_rate_per_sec = decay_rate_per_sec if decay_rate_per_sec is not None else self.config.decay_rate_per_sec
        self.signal_weights = self.config.risk_weights
        self.combination_weights = combination_weights if combination_weights is not None else getattr(self.config, "combination_weights", {})

        self.profiles: Dict[str, SeatRiskProfile] = {}

    def get_or_create_profile(self, seat_id: str, camera_id: Optional[str] = None) -> SeatRiskProfile:
        if seat_id not in self.profiles:
            self.profiles[seat_id] = SeatRiskProfile(
                seat_id=seat_id,
                room_id=self.room_id,
                camera_id=camera_id,
            )
        return self.profiles[seat_id]

    def update_seat(
        self,
        seat_id: str,
        active_signals: List[BehaviorSignal],
        timestamp_ms: float,
        detection: Optional[Detection] = None,
        frame_image: Optional[np.ndarray] = None,
    ) -> Optional[ClassroomEvent]:
        """Ingest new behavior signals for a seat and return an event if FLAGGED_FOR_REVIEW is triggered."""
        profile = self.get_or_create_profile(seat_id)

        dt_sec = max(0.0, (timestamp_ms - profile.last_update_timestamp_ms) / 1000.0) if profile.last_update_timestamp_ms > 0 else 0.1
        profile.last_update_timestamp_ms = timestamp_ms

        # 1. Check Cooldown timer
        in_cooldown = (
            profile.current_state == RiskState.COOLDOWN.value
            and (timestamp_ms - profile.cooldown_enter_timestamp_ms) < self.cooldown_duration_ms
        )

        if profile.current_state == RiskState.COOLDOWN.value and not in_cooldown:
            # Cooldown expired -> return to NORMAL (or OBSERVE if lingering signals)
            profile.current_state = RiskState.NORMAL.value
            profile.risk_score = min(profile.risk_score, 25.0)

        # 2. Accumulate Score or Decay
        added_risk = 0.0
        valid_signals = [s for s in active_signals if s.is_valid and s.quality >= 0.20]
        active_sig_types = {s.signal_type for s in valid_signals}

        # Check for composite below-desk activity
        has_composite_below_desk = SignalType.SUSPICIOUS_BELOW_DESK_ACTIVITY.value in active_sig_types
        suppress_components = has_composite_below_desk and getattr(self.config, "composite_suppression", True)

        # (a) Base individual signal risk
        for sig in valid_signals:
            if suppress_components and sig.signal_type in (SignalType.LOOK_DOWN_LONG.value, SignalType.LOW_HAND_POSTURE.value):
                # Suppress component risk to avoid double-counting with composite (SRS FR-BEH-006)
                continue
            w = self.signal_weights.get(sig.signal_type, 5.0)
            added_risk += float(w) * sig.raw_score * dt_sec
            profile.recent_signals.append(sig)

        # (b) Contextual combinations bonus (multi-cue co-occurrence when composite is not already active)
        if len(active_sig_types) >= 2 and not has_composite_below_desk:
            for combo_key, bonus_w in self.combination_weights.items():
                parts = combo_key.split("+")
                if len(parts) == 2 and parts[0] in active_sig_types and parts[1] in active_sig_types:
                    added_risk += float(bonus_w) * dt_sec

        if added_risk > 0:
            profile.risk_score = min(100.0, profile.risk_score + added_risk)
            if detection is not None:
                if profile.risk_score >= profile.peak_risk_score:
                    profile.peak_risk_score = profile.risk_score
                    profile.peak_detection = detection
                    if frame_image is not None:
                        profile.peak_frame_image = frame_image.copy()
        else:
            # Decay score over time
            decay_pts = self.decay_rate_per_sec * dt_sec
            profile.risk_score = max(0.0, profile.risk_score - decay_pts)
            if profile.risk_score == 0.0:
                profile.peak_risk_score = 0.0
                profile.peak_detection = None

        # 3. State Transitions
        score = profile.risk_score
        prev_state = profile.current_state

        if in_cooldown:
            # Silent tracking in cooldown
            return None

        if score >= 80:
            new_state = RiskState.FLAGGED_FOR_REVIEW.value
        elif score >= 60:
            new_state = RiskState.SUSPICIOUS.value
        elif score >= 30:
            new_state = RiskState.OBSERVE.value
        else:
            new_state = RiskState.NORMAL.value

        profile.current_state = new_state

        # 4. Event Generation on FLAGGED_FOR_REVIEW
        if new_state == RiskState.FLAGGED_FOR_REVIEW.value and prev_state != RiskState.FLAGGED_FOR_REVIEW.value:
            # Check for recidivism (repeated offense within recidivism window)
            time_since_last_event = (timestamp_ms - profile.last_event_timestamp_ms) if profile.last_event_timestamp_ms > 0 else float("inf")
            is_recidivist = time_since_last_event <= self.recidivism_window_ms
            profile.is_recidivist = is_recidivist
            profile.total_event_count += 1
            profile.last_event_timestamp_ms = timestamp_ms

            # Enter Cooldown immediately to prevent event spamming
            profile.current_state = RiskState.COOLDOWN.value
            profile.cooldown_enter_timestamp_ms = timestamp_ms

            # Prioritize composite and primary suspicious signals over context observations
            priority_order = [
                SignalType.SUSPICIOUS_BELOW_DESK_ACTIVITY.value,
                SignalType.PROLONGED_HEAD_TURN.value,
                SignalType.BODY_LEAN_SIDE.value,
                SignalType.SEAT_LEFT.value,
                SignalType.MULTIPLE_PERSON_NEAR_SEAT.value,
                SignalType.LOW_HAND_POSTURE.value,
                SignalType.LOOK_DOWN_LONG.value,
            ]
            primary_sig_name = valid_signals[0].signal_type if valid_signals else SignalType.PROLONGED_HEAD_TURN.value
            for p_sig in priority_order:
                if p_sig in active_sig_types:
                    primary_sig_name = p_sig
                    break
            severity = SeverityLevel.CRITICAL.value if is_recidivist else (SeverityLevel.HIGH.value if score >= 85 else SeverityLevel.MEDIUM.value)

            peak_bbox = profile.peak_detection.bbox if profile.peak_detection else (detection.bbox if detection else (0, 0, 0, 0))

            event = ClassroomEvent(
                event_id=str(uuid.uuid4()),
                track_id=0,
                behavior=primary_sig_name,
                severity=severity,
                confidence_avg=round(float(np.mean([s.confidence for s in valid_signals] or [0.85])), 3),
                confidence_peak=round(float(max([s.confidence for s in valid_signals] or [0.90])), 3),
                start_frame=detection.frame_index if detection else 0,
                end_frame=detection.frame_index if detection else 0,
                duration_seconds=round(self.window_duration_ms / 1000.0, 2),
                status="PENDING",
                bbox=peak_bbox,
                evidence_frame=profile.peak_frame_image,
                is_recidivist=is_recidivist,
                requires_human_review=True,
                timestamp_ms=timestamp_ms,
            )

            logger.info(
                "Triggered FLAGGED_FOR_REVIEW on Seat %s: Risk=%.0f/100, Signal=%s, Recidivist=%s",
                seat_id,
                score,
                primary_sig_name,
                is_recidivist,
            )
            return event

        return None
