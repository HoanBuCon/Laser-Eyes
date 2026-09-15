"""Kalman-Enhanced Spatial IoU Matcher & Multi-Object Person Tracker.

Associates per-frame object detections with persistent student track IDs using
a 2D kinematic Kalman Filter combined with Intersection-over-Union (IoU) and
spatial centroid continuity to eliminate ID switches during classroom occlusions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import numpy as np

from classroom_monitor.models import Detection, TrackState, TrackedDetection


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


def compute_centroid_distance(
    box_a: Tuple[int, int, int, int], box_b: Tuple[int, int, int, int]
) -> float:
    """Compute Euclidean distance between centers of two boxes."""
    cxa = (box_a[0] + box_a[2]) / 2.0
    cya = (box_a[1] + box_a[3]) / 2.0
    cxb = (box_b[0] + box_b[2]) / 2.0
    cyb = (box_b[1] + box_b[3]) / 2.0
    return float(math.hypot(cxa - cxb, cya - cyb))


class KalmanBoxTracker:
    """2D Constant-Velocity Kinematic Kalman Filter for Bounding Box Tracking.

    State vector: [cx, cy, w, h, v_cx, v_cy, v_w, v_h]^T (8 dimensions)
    Measurement vector: [cx, cy, w, h]^T (4 dimensions)
    """

    def __init__(
        self,
        bbox: Tuple[int, int, int, int],
        process_noise_scale: float = 1e-2,
        measurement_noise_scale: float = 1e-1,
    ):
        # State transition matrix F (8x8)
        self.F = np.eye(8, dtype=np.float32)
        for i in range(4):
            self.F[i, i + 4] = 1.0  # pos += vel * dt (dt = 1)

        # Measurement matrix H (4x8)
        self.H = np.zeros((4, 8), dtype=np.float32)
        for i in range(4):
            self.H[i, i] = 1.0

        # State vector x (8x1)
        cx = (bbox[0] + bbox[2]) / 2.0
        cy = (bbox[1] + bbox[3]) / 2.0
        w = max(1.0, float(bbox[2] - bbox[0]))
        h = max(1.0, float(bbox[3] - bbox[1]))
        self.x = np.array([cx, cy, w, h, 0.0, 0.0, 0.0, 0.0], dtype=np.float32).reshape(8, 1)

        # Process noise covariance Q (8x8)
        self.Q = np.eye(8, dtype=np.float32) * process_noise_scale
        for i in range(4, 8):
            self.Q[i, i] *= 2.0

        # Measurement noise covariance R (4x4)
        self.R = np.eye(4, dtype=np.float32) * measurement_noise_scale

        # Estimation error covariance P (8x8)
        self.P = np.eye(8, dtype=np.float32) * 10.0
        for i in range(4, 8):
            self.P[i, i] *= 50.0

    def predict(self) -> Tuple[int, int, int, int]:
        """Advance the state vector and error covariance using kinematics."""
        self.x = self.F @ self.x
        self.P = self.F @ self.P @ self.F.T + self.Q
        return self.get_bbox()

    def update(self, bbox: Tuple[int, int, int, int]) -> None:
        """Correct the predicted state with an observed bounding box measurement."""
        cx = (bbox[0] + bbox[2]) / 2.0
        cy = (bbox[1] + bbox[3]) / 2.0
        w = max(1.0, float(bbox[2] - bbox[0]))
        h = max(1.0, float(bbox[3] - bbox[1]))
        z = np.array([cx, cy, w, h], dtype=np.float32).reshape(4, 1)

        # Innovation (residual)
        y = z - self.H @ self.x

        # Innovation covariance
        S = self.H @ self.P @ self.H.T + self.R

        # Kalman gain
        K = self.P @ self.H.T @ np.linalg.inv(S)

        # Updated state and covariance
        self.x = self.x + K @ y
        I = np.eye(8, dtype=np.float32)
        self.P = (I - K @ self.H) @ self.P

    def get_bbox(self) -> Tuple[int, int, int, int]:
        """Convert current state estimate to integer bounding box coordinates."""
        cx = float(self.x[0, 0])
        cy = float(self.x[1, 0])
        w = max(4.0, float(self.x[2, 0]))
        h = max(4.0, float(self.x[3, 0]))

        x1 = int(round(cx - w / 2.0))
        y1 = int(round(cy - h / 2.0))
        x2 = int(round(cx + w / 2.0))
        y2 = int(round(cy + h / 2.0))
        return (x1, y1, x2, y2)


@dataclass
class TrackedPerson:
    """Internal state and kinematic tracking for an individual student."""

    track_id: int
    last_bbox: Tuple[int, int, int, int]
    last_seen_frame: int
    missing_frames: int = 0
    coasting_frames: int = 0
    total_sightings: int = 1
    state: TrackState = TrackState.CONFIRMED
    predicted_bbox: Optional[Tuple[int, int, int, int]] = None
    kalman: Optional[KalmanBoxTracker] = None
    recent_bboxes: List[Tuple[int, int, int, int]] = field(default_factory=list)

    def __post_init__(self):
        if self.kalman is None:
            self.kalman = KalmanBoxTracker(self.last_bbox)
        self.predicted_bbox = self.last_bbox

    def predict(self) -> Tuple[int, int, int, int]:
        """Predict next frame position via Kalman Filter."""
        if self.kalman is not None:
            self.predicted_bbox = self.kalman.predict()
        else:
            self.predicted_bbox = self.last_bbox
        return self.predicted_bbox

    def update(self, bbox: Tuple[int, int, int, int], frame_idx: int) -> None:
        """Update track with a newly matched detection."""
        self.last_bbox = bbox
        self.predicted_bbox = bbox
        self.last_seen_frame = frame_idx
        self.missing_frames = 0
        self.coasting_frames = 0
        self.total_sightings += 1
        self.state = TrackState.CONFIRMED

        if self.kalman is not None:
            self.kalman.update(bbox)

        self.recent_bboxes.append(bbox)
        if len(self.recent_bboxes) > 15:
            self.recent_bboxes.pop(0)

    def coast(self, frame_idx: int) -> Tuple[int, int, int, int]:
        """Advance track in coasting mode during temporary occlusion."""
        self.missing_frames += 1
        self.coasting_frames += 1
        self.state = TrackState.COASTING
        if self.predicted_bbox is not None:
            self.last_bbox = self.predicted_bbox
        return self.last_bbox


class SpatialMatcher:
    """Enterprise multi-student spatial matcher using Kalman predictions & hybrid matching."""

    def __init__(
        self,
        iou_threshold: float = 0.28,
        max_missing_frames: int = 15,
        max_coasting_frames: int = 12,
        tracker_type: str = "kalman_iou",
        distance_cost_weight: float = 0.15,
    ):
        self.iou_threshold = iou_threshold
        self.max_missing_frames = max_missing_frames
        self.max_coasting_frames = max_coasting_frames
        self.tracker_type = tracker_type
        self.distance_cost_weight = distance_cost_weight

        self.tracks: Dict[int, TrackedPerson] = {}
        self.next_track_id: int = 1

    def update(
        self, detections: List[Detection], frame_idx: int
    ) -> List[TrackedDetection]:
        """Match new detections to existing tracks and return tracked detections."""
        # Step 1: Kinematic prediction for all existing active tracks
        for track in self.tracks.values():
            if self.tracker_type == "kalman_iou":
                track.predict()
            else:
                track.predicted_bbox = track.last_bbox

        if not detections:
            # Handle frame without detections
            to_delete: List[int] = []
            for track_id, track in self.tracks.items():
                track.coast(frame_idx)
                if track.missing_frames > self.max_missing_frames:
                    to_delete.append(track_id)
            for t_id in to_delete:
                del self.tracks[t_id]
            return []

        active_track_ids = list(self.tracks.keys())
        tracked_detections: List[TrackedDetection] = []

        if not active_track_ids:
            # First initialization: spawn new tracks for all detections
            for det in detections:
                t_id = self.next_track_id
                self.next_track_id += 1
                self.tracks[t_id] = TrackedPerson(
                    track_id=t_id,
                    last_bbox=det.bbox,
                    last_seen_frame=frame_idx,
                )
                tracked_detections.append(
                    TrackedDetection(track_id=t_id, detection=det, is_coasting=False)
                )
            return tracked_detections

        # Step 2: Build Hybrid Matching Score Matrix
        # Rows: Existing tracks, Cols: New detections
        num_tracks = len(active_track_ids)
        num_dets = len(detections)
        score_matrix = np.zeros((num_tracks, num_dets), dtype=np.float32)

        for i, t_id in enumerate(active_track_ids):
            track = self.tracks[t_id]
            ref_box = track.predicted_bbox or track.last_bbox

            for j, det in enumerate(detections):
                iou = compute_bbox_iou(ref_box, det.bbox)
                # Distance penalty to disambiguate dense desk rows
                dist = compute_centroid_distance(ref_box, det.bbox)
                norm_dist = min(1.0, dist / 400.0)
                score = iou - (self.distance_cost_weight * norm_dist)
                score_matrix[i, j] = score if iou >= self.iou_threshold else -1.0

        # Step 3: Greedy bipartite match with highest scores
        matched_tracks: Set[int] = set()
        matched_dets: Set[int] = set()

        flat_indices = np.argsort(score_matrix.ravel())[::-1]

        for flat_idx in flat_indices:
            score_val = score_matrix.ravel()[flat_idx]
            if score_val < 0.0:
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
            tracked_detections.append(
                TrackedDetection(track_id=t_id, detection=det, is_coasting=False)
            )

        # Step 4: Handle unmatched detections -> spawn new student tracks
        for j, det in enumerate(detections):
            if j not in matched_dets:
                t_id = self.next_track_id
                self.next_track_id += 1
                self.tracks[t_id] = TrackedPerson(
                    track_id=t_id,
                    last_bbox=det.bbox,
                    last_seen_frame=frame_idx,
                )
                tracked_detections.append(
                    TrackedDetection(track_id=t_id, detection=det, is_coasting=False)
                )

        # Step 5: Handle unmatched tracks -> Spatial Coasting / Pruning
        to_delete = []
        for i, t_id in enumerate(active_track_ids):
            if i not in matched_tracks:
                track = self.tracks[t_id]
                track.coast(frame_idx)
                if track.missing_frames > self.max_missing_frames:
                    to_delete.append(t_id)

        for t_id in to_delete:
            del self.tracks[t_id]

        return tracked_detections

    def reset(self) -> None:
        """Clear all active tracks and reset ID counter."""
        self.tracks.clear()
        self.next_track_id = 1
