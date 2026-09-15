"""Lightweight Spatial IoU Matcher & Person Tracker for Classroom Surveillance.

Associates per-frame object detections with persistent student track IDs based on
bounding-box intersection over union (IoU) and spatial continuity.
No heavy appearance embedding models required — optimal for stationary classroom cameras.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import numpy as np

from classroom_monitor.models import Detection, TrackedDetection


def compute_bbox_iou(
    box_a: Tuple[int, int, int, int], box_b: Tuple[int, int, int, int]
) -> float:
    """Compute Intersection-over-Union (IoU) between two bounding boxes (x1, y1, x2, y2)."""
    xa = max(box_a[0], box_b[0])
    ya = max(box_a[1], box_b[1])
    xb = min(box_a[2], box_b[2])
    yb = min(box_a[3], box_b[3])

    inter_w = max(0, xb - xa)
    inter_h = max(0, yb - ya)
    inter_area = inter_w * inter_h

    area_a = max(0, box_a[2] - box_a[0]) * max(0, box_a[3] - box_a[1])
    area_b = max(0, box_b[2] - box_b[0]) * max(0, box_b[3] - box_b[1])

    union_area = area_a + area_b - inter_area
    if union_area <= 0:
        return 0.0
    return float(inter_area / union_area)


@dataclass
class TrackedPerson:
    """Internal state for an actively tracked individual."""

    track_id: int
    last_bbox: Tuple[int, int, int, int]
    last_seen_frame: int
    missing_frames: int = 0
    total_sightings: int = 1
    recent_bboxes: List[Tuple[int, int, int, int]] = field(default_factory=list)

    def update(self, bbox: Tuple[int, int, int, int], frame_idx: int) -> None:
        """Update track with a newly matched detection."""
        self.last_bbox = bbox
        self.last_seen_frame = frame_idx
        self.missing_frames = 0
        self.total_sightings += 1
        self.recent_bboxes.append(bbox)
        if len(self.recent_bboxes) > 10:
            self.recent_bboxes.pop(0)


class SpatialMatcher:
    """Spatial continuity matcher using greedy IoU assignment."""

    def __init__(self, iou_threshold: float = 0.28, max_missing_frames: int = 12):
        self.iou_threshold = iou_threshold
        self.max_missing_frames = max_missing_frames
        self.tracks: Dict[int, TrackedPerson] = {}
        self.next_track_id: int = 1

    def update(
        self, detections: List[Detection], frame_idx: int
    ) -> List[TrackedDetection]:
        """Match new detections to existing tracks and return tracked detections."""
        if not detections:
            # Increment missing counters for all active tracks
            to_delete: List[int] = []
            for track_id, track in self.tracks.items():
                track.missing_frames += 1
                if track.missing_frames > self.max_missing_frames:
                    to_delete.append(track_id)
            for t_id in to_delete:
                del self.tracks[t_id]
            return []

        active_track_ids = list(self.tracks.keys())
        tracked_detections: List[TrackedDetection] = []

        if not active_track_ids:
            # No existing tracks, spawn new tracks for all detections
            for det in detections:
                t_id = self.next_track_id
                self.next_track_id += 1
                self.tracks[t_id] = TrackedPerson(
                    track_id=t_id,
                    last_bbox=det.bbox,
                    last_seen_frame=frame_idx,
                )
                tracked_detections.append(TrackedDetection(track_id=t_id, detection=det))
            return tracked_detections

        # Build IoU cost matrix
        # Rows: Existing tracks, Cols: New detections
        num_tracks = len(active_track_ids)
        num_dets = len(detections)
        iou_matrix = np.zeros((num_tracks, num_dets), dtype=np.float32)

        for i, t_id in enumerate(active_track_ids):
            track_box = self.tracks[t_id].last_bbox
            for j, det in enumerate(detections):
                iou_matrix[i, j] = compute_bbox_iou(track_box, det.bbox)

        # Greedy match highest IoU first
        matched_tracks: Set[int] = set()
        matched_dets: Set[int] = set()

        # Sort all (iou, i, j) pairs in descending order
        flat_indices = np.argsort(iou_matrix.ravel())[::-1]

        for flat_idx in flat_indices:
            iou_val = iou_matrix.ravel()[flat_idx]
            if iou_val < self.iou_threshold:
                break
            i = flat_idx // num_dets
            j = flat_idx % num_dets

            if i in matched_tracks or j in matched_dets:
                continue

            t_id = active_track_ids[i]
            det = detections[j]

            # Match confirmed
            self.tracks[t_id].update(det.bbox, frame_idx)
            matched_tracks.add(i)
            matched_dets.add(j)
            tracked_detections.append(TrackedDetection(track_id=t_id, detection=det))

        # Handle unmatched detections -> spawn new tracks
        for j, det in enumerate(detections):
            if j not in matched_dets:
                t_id = self.next_track_id
                self.next_track_id += 1
                self.tracks[t_id] = TrackedPerson(
                    track_id=t_id,
                    last_bbox=det.bbox,
                    last_seen_frame=frame_idx,
                )
                tracked_detections.append(TrackedDetection(track_id=t_id, detection=det))

        # Handle unmatched tracks -> increment missing frames and prune if stale
        to_delete = []
        for i, t_id in enumerate(active_track_ids):
            if i not in matched_tracks:
                self.tracks[t_id].missing_frames += 1
                if self.tracks[t_id].missing_frames > self.max_missing_frames:
                    to_delete.append(t_id)

        for t_id in to_delete:
            del self.tracks[t_id]

        return tracked_detections

    def reset(self) -> None:
        """Clear all active tracks."""
        self.tracks.clear()
        self.next_track_id = 1
