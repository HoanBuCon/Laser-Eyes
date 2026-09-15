"""Central Event Processing Orchestrator for Classroom Surveillance.

Connects Spatial Matching, Per-Person State Machines, Anti-Flickering Score Accumulation,
Crowd Room Context, and Evidence Frame Extraction into a coherent end-to-end pipeline.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Set

import numpy as np

from classroom_monitor.behavior_tracker import PersonBehaviorTracker
from classroom_monitor.config import DEFAULT_CONFIG, ClassroomConfig
from classroom_monitor.models import ClassroomEvent, Detection, TrackedDetection
from classroom_monitor.room_context import RoomContextAnalyzer
from classroom_monitor.spatial_matcher import SpatialMatcher

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
        )
        self.person_trackers: Dict[int, PersonBehaviorTracker] = {}
        self.room_context = RoomContextAnalyzer(config=self.config)
        self.event_buffer: List[ClassroomEvent] = []

    def process_frame(
        self,
        detections: List[Detection],
        frame_idx: int,
        frame_image: Optional[np.ndarray] = None,
    ) -> List[ClassroomEvent]:
        """Process detections from one frame, returning any new or updated violation events."""
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
            event = tracker.update(td.detection, frame_idx, frame_image)

            if event is not None:
                # Apply Room Context
                if context_signal.suppress and t_id in context_signal.affected_tracks:
                    event.status = "suppressed"
                    event.room_context = context_signal.reason
                elif context_signal.boost_severity and t_id in context_signal.affected_tracks:
                    event.severity = "HIGH"
                    event.room_context = context_signal.reason

                new_events.append(event)

        # 4. Handle Disappeared / Occluded Students
        for t_id in list(self.person_trackers.keys()):
            if t_id not in active_tracks:
                tracker = self.person_trackers[t_id]
                event = tracker.update(None, frame_idx, frame_image)
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
