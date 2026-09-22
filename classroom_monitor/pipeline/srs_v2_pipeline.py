"""Single semantic source for Classroom SRS v2 frame processing.

CLI and web adapters retain their own lifecycle, evidence, rendering and
delivery concerns.  Perception through incident generation lives here so the
same frame/config produces the same episodes, patterns and incidents.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from classroom_monitor.behavior_pattern_engine import BehaviorPattern, BehaviorPatternEngine
from classroom_monitor.demo.config import DemoVideoConfig
from classroom_monitor.detector import PoseClassroomDetector
from classroom_monitor.head_pose_provider import HeadOrientationEstimate, create_head_pose_provider
from classroom_monitor.models import ClassroomEvent, Detection
from classroom_monitor.observation_extractor import ObservationExtractor, RawObservation
from classroom_monitor.scene_context import CapabilityStatus, SceneProfile, SeatContext, SeatGraph
from classroom_monitor.seat_manager import SeatManager, SeatState
from classroom_monitor.seat_risk_tracker import SeatRiskTracker
from classroom_monitor.temporal_episode_engine import TemporalEpisode, TemporalEpisodeEngine


@dataclass
class SRSv2FrameResult:
    detections: List[Detection] = field(default_factory=list)
    roaming_detections: List[Detection] = field(default_factory=list)
    observations: Dict[str, List[RawObservation]] = field(default_factory=dict)
    active_episodes: List[TemporalEpisode] = field(default_factory=list)
    patterns: List[BehaviorPattern] = field(default_factory=list)
    incidents: List[ClassroomEvent] = field(default_factory=list)
    occupied_seat_count: int = 0
    timings_ms: Dict[str, float] = field(default_factory=dict)


class SRSv2Pipeline:
    """Stateful, adapter-neutral SRS v2 processor."""

    def __init__(
        self,
        *,
        config: DemoVideoConfig,
        runtime_config: Dict[str, Any],
        seat_graph: SeatGraph,
        seat_manager: SeatManager,
        detector: Optional[PoseClassroomDetector] = None,
        head_provider: Optional[Any] = None,
        observation_extractor: Optional[ObservationExtractor] = None,
        episode_engine: Optional[TemporalEpisodeEngine] = None,
        pattern_engine: Optional[BehaviorPatternEngine] = None,
        risk_tracker: Optional[SeatRiskTracker] = None,
    ):
        self.config = config
        self.runtime_config = runtime_config
        self.seat_graph = seat_graph
        self.seat_manager = seat_manager
        self.detector = detector or PoseClassroomDetector(
            confidence_threshold=config.pose_conf,
            allow_mock=config.allow_mock,
        )
        self.detector.ensure_available()
        self.head_provider = head_provider or create_head_pose_provider(config.head_provider)
        self.observation_extractor = observation_extractor or ObservationExtractor(
            head_pose_provider=self.head_provider
        )
        self.episode_engine = episode_engine or TemporalEpisodeEngine(**runtime_config["temporal"])
        self.pattern_engine = pattern_engine or BehaviorPatternEngine(
            seat_graph=seat_graph,
            **runtime_config["patterns"],
        )
        self.risk_tracker = risk_tracker or SeatRiskTracker(
            room_id=config.room_code,
            camera_id=config.camera_id,
            **runtime_config["risk"],
        )
        self.hpe_interval_ms = (1000.0 / config.hpe_hz) if config.hpe_hz > 0 else 200.0
        self.hpe_max_age_ms = runtime_config["head_pose"]["cache_max_age_ms"]
        self.seat_hpe_cache: Dict[str, Tuple[HeadOrientationEstimate, float]] = {}
        self.last_hpe_time: Dict[str, float] = {}
        self.scheduled_hpe_cycles = 0

    @property
    def capability_health(self) -> Dict[str, str]:
        hpe_ready = not (
            self.config.head_provider.lower().replace("-", "_") in ("sixdrepnet", "6drepnet", "sixd")
            and getattr(self.head_provider.__class__, "_shared_model", None) is None
        )
        return self.detector.capability_health | {
            "head_orientation": "REAL" if hpe_ready else "DEGRADED"
        }

    def process_frame(self, frame: np.ndarray, frame_idx: int, timestamp_ms: float) -> SRSv2FrameResult:
        timings: Dict[str, float] = {}

        started = time.perf_counter()
        detections = self.detector.detect(frame, frame_index=frame_idx)
        timings["perception"] = (time.perf_counter() - started) * 1000.0

        _mapped, roaming = self.seat_manager.map_detections_to_seats(
            detections=detections,
            timestamp_ms=timestamp_ms,
            frame_idx=frame_idx,
        )
        started = time.perf_counter()
        requests: List[Dict[str, Any]] = []
        for seat_code, occupancy in self.seat_manager.occupancies.items():
            if occupancy.state != SeatState.OCCUPIED or occupancy.assigned_detection is None:
                continue
            context = self.seat_graph.get_context(seat_code)
            if context is None or context.capabilities.head_orientation == CapabilityStatus.DISABLED:
                continue
            if (timestamp_ms - self.last_hpe_time.get(seat_code, -100000.0)) < self.hpe_interval_ms:
                continue
            requests.append(
                {
                    "seat_id": seat_code,
                    "keypoints": occupancy.assigned_detection.keypoints,
                    "bbox": occupancy.assigned_detection.bbox,
                    "seat_baseline_yaw": context.reference_directions.baseline_yaw,
                    "seat_baseline_pitch": context.reference_directions.baseline_pitch,
                }
            )
            self.last_hpe_time[seat_code] = timestamp_ms

        if requests:
            self.scheduled_hpe_cycles += 1
            for seat_id, estimate in self.head_provider.estimate_batch(requests=requests, frame=frame).items():
                self.seat_hpe_cache[seat_id] = (estimate, timestamp_ms)

        observations: Dict[str, List[RawObservation]] = {}
        for seat_code, occupancy in self.seat_manager.occupancies.items():
            context = self.seat_graph.get_context(seat_code) or SeatContext(seat_id=seat_code)
            cached = self.seat_hpe_cache.get(seat_code)
            estimate = None
            if cached and (timestamp_ms - cached[1]) <= self.hpe_max_age_ms:
                estimate = cached[0]
            observations[seat_code] = self.observation_extractor.extract(
                detection=occupancy.assigned_detection,
                seat_context=context,
                timestamp_ms=timestamp_ms,
                occupancy_state=occupancy.state,
                nearby_person_count=len(occupancy.candidate_detections),
                frame=frame,
                precomputed_head_estimate=estimate,
            )
        timings["head_observation"] = (time.perf_counter() - started) * 1000.0

        started = time.perf_counter()
        active_episodes: List[TemporalEpisode] = []
        for seat_observations in observations.values():
            active_episodes.extend(
                self.episode_engine.process_observations(seat_observations, timestamp_ms=timestamp_ms)
            )
        timings["temporal"] = (time.perf_counter() - started) * 1000.0

        started = time.perf_counter()
        patterns: List[BehaviorPattern] = []
        for seat_code in self.seat_manager.occupancies:
            context = self.seat_graph.get_context(seat_code) or SeatContext(seat_id=seat_code)
            patterns.extend(
                self.pattern_engine.ingest_episodes(
                    active_episodes=active_episodes,
                    completed_episodes=self.episode_engine.completed_episodes,
                    seat_context=context,
                    timestamp_ms=timestamp_ms,
                )
            )
        timings["patterns"] = (time.perf_counter() - started) * 1000.0

        started = time.perf_counter()
        incidents: List[ClassroomEvent] = []
        for seat_code, occupancy in self.seat_manager.occupancies.items():
            context = self.seat_graph.get_context(seat_code) or SeatContext(seat_id=seat_code)
            incident = self.risk_tracker.update_seat(
                seat_id=seat_code,
                active_episodes=active_episodes,
                detected_patterns=patterns,
                timestamp_ms=timestamp_ms,
                detection=occupancy.assigned_detection,
                frame_image=frame,
                camera_id=self.config.camera_id,
                seat_context=context,
            )
            if incident is not None:
                incidents.append(incident)
        timings["risk"] = (time.perf_counter() - started) * 1000.0

        return SRSv2FrameResult(
            detections=detections,
            roaming_detections=roaming,
            observations=observations,
            active_episodes=active_episodes,
            patterns=patterns,
            incidents=incidents,
            occupied_seat_count=sum(
                1 for occupancy in self.seat_manager.occupancies.values()
                if occupancy.state == SeatState.OCCUPIED
            ),
            timings_ms=timings,
        )

    def flush(self, timestamp_ms: float) -> List[TemporalEpisode]:
        return self.episode_engine.flush_all(timestamp_ms=timestamp_ms)
