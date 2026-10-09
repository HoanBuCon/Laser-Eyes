"""Single semantic source for Classroom SRS v2 frame processing.

CLI and web adapters retain their own lifecycle, evidence, rendering and
delivery concerns.  Perception through incident generation lives here so the
same frame/config produces the same episodes, patterns and incidents.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np

from classroom_monitor.behavior_pattern_engine import BehaviorPattern, BehaviorPatternEngine
from classroom_monitor.demo.config import DemoVideoConfig
from classroom_monitor.detector import PoseClassroomDetector
from classroom_monitor.head_pose_provider import (
    AdaptiveYawBaseline,
    HeadOffsetBaseline,
    HeadOrientationEstimate,
    HeadPoseSampleGate,
    create_head_pose_provider,
)
from classroom_monitor.models import ClassroomEvent, Detection
from classroom_monitor.observation_extractor import ObservationExtractor, ObservationType, RawObservation
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
    # Seats whose head-pose samples are currently too noisy to trust
    head_unreliable_seats: Set[str] = field(default_factory=set)
    timings_ms: Dict[str, float] = field(default_factory=dict)


def incident_focus(
    *,
    seat_polygon: np.ndarray,
    person_bbox: Optional[Any],
    current_bbox: Optional[Any],
    frame_shape: Tuple[int, ...],
) -> Dict[str, Any]:
    """Where the reviewer should look: the seat ROI and the flagged person's box.

    Stored with the incident (pixel coordinates of the full camera frame) so the
    review page can highlight the student on the snapshot and the clip without
    altering the archived evidence files.
    """
    def as_box(bbox: Optional[Any]) -> Optional[List[float]]:
        if bbox is None:
            return None
        values = [float(v) for v in bbox]
        if len(values) != 4 or values[2] <= values[0] or values[3] <= values[1]:
            return None
        return values

    box = as_box(person_bbox) or as_box(current_bbox)
    return {
        "frame_size": [int(frame_shape[1]), int(frame_shape[0])],
        "seat_polygon": [[round(float(x), 1), round(float(y), 1)] for x, y in np.asarray(seat_polygon).reshape(-1, 2)],
        "person_bbox": [round(v, 1) for v in box] if box else None,
    }


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
        head_cfg = runtime_config["head_pose"]
        self.hpe_max_age_ms = head_cfg["cache_max_age_ms"]
        self.head_gate: Optional[HeadPoseSampleGate] = (
            HeadPoseSampleGate() if head_cfg.get("plausibility_gate", True) else None
        )
        self.yaw_baseline: Optional[AdaptiveYawBaseline] = (
            AdaptiveYawBaseline(
                window_ms=float(head_cfg.get("baseline_window_ms", 60000.0)),
                min_samples=int(head_cfg.get("baseline_min_samples", 10)),
            )
            if head_cfg.get("adaptive_baseline", True)
            else None
        )
        self.head_offset_baseline = HeadOffsetBaseline()
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
            frame_w=frame.shape[1],
            frame_h=frame.shape[0],
        )
        started = time.perf_counter()
        requests: List[Dict[str, Any]] = []
        for seat_code, occupancy in self.seat_manager.occupancies.items():
            # A seat shared with a desk mate still has its own occupant
            # (SeatManager picks it by continuity), so it is scheduled too.
            if (
                occupancy.state not in (SeatState.OCCUPIED, SeatState.MULTIPLE_PERSON)
                or occupancy.assigned_detection is None
            ):
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
                # Physically implausible samples never reach the baseline or the episodes
                if self.head_gate is not None:
                    estimate = self.head_gate.apply(seat_id, estimate, timestamp_ms)
                if self.yaw_baseline is not None:
                    estimate = self.yaw_baseline.apply(seat_id, estimate, timestamp_ms)
                self.seat_hpe_cache[seat_id] = (estimate, timestamp_ms)

        # A seat that has been empty starts over: the next person has their own neutral.
        for seat_code, occupancy in self.seat_manager.occupancies.items():
            if occupancy.state == SeatState.EMPTY:
                self.head_offset_baseline.reset_seat(seat_code)
                if self.yaw_baseline is not None:
                    self.yaw_baseline.reset_seat(seat_code)
                if self.head_gate is not None:
                    self.head_gate.reset_seat(seat_code)

        observations: Dict[str, List[RawObservation]] = {}
        for seat_code, occupancy in self.seat_manager.occupancies.items():
            context = self.seat_graph.get_context(seat_code) or SeatContext(seat_id=seat_code)
            cached = self.seat_hpe_cache.get(seat_code)
            estimate = None
            sample_ts = None
            if cached and (timestamp_ms - cached[1]) <= self.hpe_max_age_ms:
                estimate, sample_ts = cached
            # Head orientation comes only from the scheduled batch above; a
            # seat without a fresh sample is UNKNOWN rather than triggering an
            # extra per-frame head-model forward.
            observations[seat_code] = self.observation_extractor.extract(
                detection=occupancy.assigned_detection,
                seat_context=context,
                timestamp_ms=timestamp_ms,
                occupancy_state=occupancy.state,
                nearby_person_count=len(occupancy.candidate_detections),
                frame=frame,
                precomputed_head_estimate=estimate,
                allow_head_inference=False,
                head_sample_timestamp_ms=sample_ts,
                expected_person_count=getattr(occupancy, "expected_person_count", None),
            )
            self._relate_head_offset(seat_code, observations[seat_code], timestamp_ms)
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
                # Reviewer highlight is display data: it must never stop the analysis
                try:
                    seat_def = self.seat_manager.seats.get(seat_code)
                    polygon = (
                        seat_def.polygon_for_frame(frame_w=frame.shape[1], frame_h=frame.shape[0])
                        if hasattr(seat_def, "polygon_for_frame") else np.zeros((0, 2))
                    )
                    incident.metadata["focus"] = incident_focus(
                        seat_polygon=polygon,
                        person_bbox=incident.bbox,
                        current_bbox=getattr(occupancy.assigned_detection, "bbox", None),
                        frame_shape=frame.shape,
                    )
                except Exception:
                    pass
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
                if occupancy.state in (SeatState.OCCUPIED, SeatState.MULTIPLE_PERSON)
            ),
            head_unreliable_seats=self.head_gate.unreliable_seats() if self.head_gate is not None else set(),
            timings_ms=timings,
        )

    def _relate_head_offset(self, seat_code: str, seat_observations: List[RawObservation], timestamp_ms: float) -> None:
        """Express the head offset against the student's usual posture, subject-centric.

        Negative is toward the student's own left, as for yaw.  With the camera
        facing the students their left is the image's right.
        """
        for index, obs in enumerate(seat_observations):
            if obs.observation_type != ObservationType.HEAD_OFFSET_X.value:
                continue
            relative = self.head_offset_baseline.apply(seat_code, float(obs.value), timestamp_ms)
            if relative is None:
                del seat_observations[index]
                return
            mirrored = getattr(self.seat_graph, "mirrors_image", True)
            obs.value = -relative if mirrored else relative
            obs.metadata = {**(obs.metadata or {}), "space": "subject", "relative_to": "student_baseline"}
            return

    def flush(self, timestamp_ms: float) -> List[TemporalEpisode]:
        return self.episode_engine.flush_all(timestamp_ms=timestamp_ms)
