"""Central Event Processing Orchestrator for Classroom Surveillance.

Connects Spatial Matching, Per-Person State Machines, Anti-Flickering Score Accumulation,
Crowd Room Context, 10-Second Evidence Video Buffering, and Real-Time Event Dispatching.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Set

import numpy as np

from classroom_monitor.behavior_tracker import PersonBehaviorTracker
from classroom_monitor.config import DEFAULT_CONFIG, ClassroomConfig
from classroom_monitor.models import ClassroomEvent, Detection, EventStatus, SeverityLevel, TrackedDetection
from classroom_monitor.room_context import RoomContextAnalyzer
from classroom_monitor.spatial_matcher import SpatialMatcher
from classroom_monitor.video_buffer import EvidenceVideoBuffer

logger = logging.getLogger("EventEngine")


class EventEngine:
    """Orchestrates multi-student behavior analysis and violation event dispatching."""

    def __init__(
        self,
        fps: float = 30.0,
        config: Optional[ClassroomConfig] = None,
    ):
        self.fps = max(1.0, fps)
        self.config = config or DEFAULT_CONFIG

        self.matcher = SpatialMatcher(
            iou_threshold=self.config.iou_match_threshold,
            max_missing_frames=self.config.max_missing_frames,
            max_coasting_frames=self.config.max_coasting_frames,
            tracker_type=self.config.tracker_type,
            distance_cost_weight=self.config.distance_cost_weight,
        )
        self.person_trackers: Dict[int, PersonBehaviorTracker] = {}
        self.room_context = RoomContextAnalyzer(config=self.config)
        self.video_buffer = EvidenceVideoBuffer(
            pre_event_seconds=self.config.pre_event_seconds,
            post_event_seconds=self.config.post_event_seconds,
            fps=self.fps,
            output_dir=self.config.evidence_video_dir,
        )
        self.event_buffer: List[ClassroomEvent] = []

    def process_frame(
        self,
        detections: List[Detection],
        frame_idx: int,
        frame_image: Optional[np.ndarray] = None,
        timestamp_ms: Optional[float] = None,
    ) -> List[ClassroomEvent]:
        """Process detections from one frame, returning any new or updated violation events."""
        ts_ms = (
            float(timestamp_ms)
            if timestamp_ms is not None
            else float(frame_idx / self.fps * 1000.0)
        )

        # 0. Ingest frame into video ring buffer
        if self.config.enable_video_evidence and frame_image is not None:
            completed_jobs = self.video_buffer.add_frame(frame_image, frame_idx, ts_ms)
            # Link completed video clips back to event objects if matched
            for job in completed_jobs:
                if job.saved_file_path:
                    for evt in self.event_buffer:
                        if evt.event_id == job.event_id:
                            evt.evidence_video_path = job.saved_file_path

        # 1. Spatial Tracking & ID Assignment
        tracked_detections: List[TrackedDetection] = self.matcher.update(
            detections, frame_idx
        )

        # 2. Macro Room Crowd Context Analysis
        context_signal = self.room_context.analyze(tracked_detections)

        new_events: List[ClassroomEvent] = []
        active_tracks: Set[int] = set()

        # 3. Per-Person Behavioral Evaluation
        for td in tracked_detections:
            t_id = td.track_id
            active_tracks.add(t_id)

            if t_id not in self.person_trackers:
                self.person_trackers[t_id] = PersonBehaviorTracker(
                    track_id=t_id,
                    fps=self.fps,
                    config=self.config,
                )

            tracker = self.person_trackers[t_id]
            event = tracker.update(
                td.detection, frame_idx, frame_image, timestamp_ms=ts_ms
            )

            if event is not None:
                # Apply Room Context Signals
                if context_signal.suppress and t_id in context_signal.affected_tracks:
                    event.status = EventStatus.SUPPRESSED.value
                    event.room_context = context_signal.reason
                elif context_signal.boost_severity and t_id in context_signal.affected_tracks:
                    event.severity = SeverityLevel.HIGH.value
                    event.room_context = context_signal.reason

                # Trigger video clipping for un-suppressed violations
                if (
                    self.config.enable_video_evidence
                    and frame_image is not None
                    and event.status != EventStatus.SUPPRESSED.value
                ):
                    self.video_buffer.trigger_clip(
                        event_id=event.event_id,
                        track_id=event.track_id,
                        behavior=event.behavior,
                        frame_idx=frame_idx,
                        timestamp_ms=ts_ms,
                    )

                new_events.append(event)

        # 4. Handle Disappeared / Occluded Students
        for t_id in list(self.person_trackers.keys()):
            if t_id not in active_tracks:
                tracker = self.person_trackers[t_id]
                event = tracker.update(
                    None, frame_idx, frame_image, timestamp_ms=ts_ms
                )
                if event is not None:
                    new_events.append(event)

        self.event_buffer.extend(new_events)
        return new_events

    def get_active_tracks_count(self) -> int:
        """Get the number of currently tracked individuals."""
        return len(self.matcher.tracks)

    def reset(self) -> None:
        """Reset all trackers and buffers."""
        self.matcher.reset()
        self.person_trackers.clear()
        self.event_buffer.clear()
        self.video_buffer.reset()
