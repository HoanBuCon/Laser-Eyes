"""Crowd and Room Context Intelligence Analyzer.

Analyzes whole-room collective behavioral patterns to:
1. Suppress false alarms when a majority of students perform the same action
   (e.g., looking at the blackboard, listening to teacher instructions).
2. Boost severity when suspicious clusters of adjacent students peek simultaneously.
"""

from __future__ import annotations

import math
from typing import Dict, List, Set, Tuple

from classroom_monitor.config import DEFAULT_CONFIG, ClassroomConfig
from classroom_monitor.models import ContextSignal, Detection, TrackedDetection


class RoomContextAnalyzer:
    """Evaluates spatial clustering and macro room dynamics."""

    def __init__(self, config: ClassroomConfig | None = None):
        self.config = config or DEFAULT_CONFIG

    def analyze(self, tracked_detections: List[TrackedDetection]) -> ContextSignal:
        """Analyze frame detections for collective room signals."""
        total_students = len(tracked_detections)
        if total_students < 2:
            return ContextSignal()

        # Group detections by behavior
        behavior_groups: Dict[str, List[TrackedDetection]] = {}
        for td in tracked_detections:
            b = td.detection.class_name
            behavior_groups.setdefault(b, []).append(td)

        # Check 1: Collective Suppression (>40% students exhibiting same behavior)
        for behavior, group in behavior_groups.items():
            if behavior in self.config.cheating_classes:
                ratio = len(group) / float(total_students)
                if ratio >= self.config.collective_suppress_ratio:
                    return ContextSignal(
                        suppress=True,
                        boost_severity=False,
                        reason=(
                            f"Collective room behavior: {ratio:.0%} of students exhibiting '{behavior}' "
                            f"simultaneously (likely instructor interaction or board viewing)."
                        ),
                        affected_tracks=[td.track_id for td in group],
                    )

        # Check 2: Spatial Clustering of Cheating Behaviors (e.g. 3 adjacent students peeking)
        cheating_tracks = [
            td for td in tracked_detections if td.detection.class_name in self.config.cheating_classes
        ]

        if len(cheating_tracks) >= self.config.cluster_min_size:
            clusters = self._find_spatial_clusters(
                cheating_tracks, self.config.cluster_distance_threshold
            )
            for cluster in clusters:
                if len(cluster) >= self.config.cluster_min_size:
                    c_tracks = [td.track_id for td in cluster]
                    return ContextSignal(
                        suppress=False,
                        boost_severity=True,
                        reason=(
                            f"Suspicious spatial cluster: Group of {len(cluster)} adjacent students "
                            f"(Tracks: {c_tracks}) exhibiting cheating behaviors in close proximity."
                        ),
                        affected_tracks=c_tracks,
                    )

        return ContextSignal()

    def _find_spatial_clusters(
        self, items: List[TrackedDetection], max_distance: float
    ) -> List[List[TrackedDetection]]:
        """Group detections into proximity clusters using Euclidean distance."""
        clusters: List[List[TrackedDetection]] = []
        visited: Set[int] = set()

        for i, item_a in enumerate(items):
            if i in visited:
                continue

            current_cluster = [item_a]
            visited.add(i)
            queue = [item_a]

            while queue:
                current = queue.pop(0)
                cx1, cy1 = current.detection.center

                for j, item_b in enumerate(items):
                    if j in visited:
                        continue
                    cx2, cy2 = item_b.detection.center
                    dist = math.hypot(cx1 - cx2, cy1 - cy2)

                    if dist <= max_distance:
                        visited.add(j)
                        current_cluster.append(item_b)
                        queue.append(item_b)

            clusters.append(current_cluster)

        return clusters
