"""Seat ROI Polygon Management and Seat-based Identity Mapping.

Implements Scope 04 (FR-SEAT-001 to FR-SEAT-007) of SRS v1.0:
- Seat ROI Configuration and Polygon Point-in-Polygon testing.
- Person-to-Seat mapping (torso anchor + polygon overlap, duplicate boxes merged,
  people walking the room kept out of seat counts).
- Stable seat identity under temporary proctor occlusions.
- Multiple-person anomaly detection per seat.
- Empty seat detection with configurable timeouts.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

import cv2
import numpy as np

from classroom_monitor.models import Detection
from classroom_monitor.residency import ResidencyMap

logger = logging.getLogger("SeatManager")

# COCO shoulder keypoints must be at least this confident to place the anchor
SHOULDER_KEYPOINT_CONF = 0.3
# Two boxes whose shoulder midpoints are closer than this fraction of the
# shoulder width are the same person
DUPLICATE_SHOULDER_RATIO = 0.35


class SeatState:
    UNKNOWN = "UNKNOWN"
    EMPTY = "EMPTY"
    OCCUPIED = "OCCUPIED"
    OCCLUDED = "OCCLUDED"
    MULTIPLE_PERSON = "MULTIPLE_PERSON"


@dataclass
class SeatDefinition:
    """Definition of a single physical seat ROI in camera space."""

    seat_id: str
    room_id: str
    seat_code: str
    polygon: np.ndarray  # Shape (N, 2), float32 coordinates in pixel space
    seat_label: str = ""
    camera_id: Optional[str] = None
    enabled: bool = True
    desk_polygon: Optional[np.ndarray] = None
    desk_y: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SeatDefinition:
        poly_raw = (
            data.get("polygon")
            or data.get("polygon_json")
            or data.get("polygon_points")
            or []
        )
        if isinstance(poly_raw, str):
            try:
                poly_raw = json.loads(poly_raw)
            except Exception:
                poly_raw = []

        if isinstance(poly_raw, list) and len(poly_raw) > 0 and isinstance(poly_raw[0], dict):
            poly_raw = [
                [float(pt.get("x", 0.0)), float(pt.get("y", 0.0))]
                for pt in poly_raw
            ]

        poly_arr = np.array(poly_raw, dtype=np.float32)

        desk_poly_raw = data.get("desk_polygon")
        desk_poly_arr = None
        if desk_poly_raw:
            try:
                if isinstance(desk_poly_raw, str):
                    desk_poly_raw = json.loads(desk_poly_raw)
                desk_poly_arr = np.array(desk_poly_raw, dtype=np.float32)
            except Exception:
                desk_poly_arr = None

        desk_y_val = data.get("desk_y")
        if desk_y_val is not None:
            try:
                desk_y_val = float(desk_y_val)
            except Exception:
                desk_y_val = None

        return cls(
            seat_id=str(data.get("id") or data.get("seat_id") or data.get("seat_code")),
            room_id=str(data.get("room_id", "")),
            seat_code=str(data.get("seat_code", "")),
            polygon=poly_arr,
            seat_label=str(data.get("seat_label", data.get("seat_code", ""))),
            camera_id=data.get("camera_id"),
            enabled=bool(data.get("enabled", True)),
            desk_polygon=desk_poly_arr,
            desk_y=desk_y_val,
            metadata=data.get("metadata", {}),
        )

    def get_effective_desk_y(self) -> Optional[float]:
        """Get the effective desk Y boundary with automatic fallback to polygon geometry."""
        if self.desk_y is not None:
            return self.desk_y
        if self.desk_polygon is not None and len(self.desk_polygon) > 0:
            return float(np.mean(self.desk_polygon[:, 1]))
        if len(self.polygon) >= 3:
            # Under-desk region begins in the lower third of the seat polygon
            min_y = float(np.min(self.polygon[:, 1]))
            max_y = float(np.max(self.polygon[:, 1]))
            return min_y + (max_y - min_y) * 0.68
        return None

    def contains_point(
        self,
        pt: Tuple[float, float],
        frame_w: Optional[int] = None,
        frame_h: Optional[int] = None,
    ) -> bool:
        """Check if 2D point is inside this seat polygon with automatic normalized scaling support."""
        poly = self.polygon_for_frame(pt=pt, frame_w=frame_w, frame_h=frame_h)
        if len(poly) < 3:
            return False
        res = cv2.pointPolygonTest(poly, (float(pt[0]), float(pt[1])), False)
        return res >= 0

    def polygon_for_frame(
        self,
        *,
        pt: Optional[Tuple[float, float]] = None,
        frame_w: Optional[int] = None,
        frame_h: Optional[int] = None,
    ) -> np.ndarray:
        """Return the polygon in the same coordinate space as a frame point."""
        if len(self.polygon) < 3:
            return self.polygon.astype(np.float32)
        poly = self.polygon.astype(np.float32)
        # If polygon is normalized (0.0 to 1.0) and point is in pixel coordinate space (> 1.0)
        point_is_pixels = pt is not None and (pt[0] > 1.05 or pt[1] > 1.05)
        if poly.size > 0 and np.max(poly) <= 1.05 and (
            point_is_pixels or frame_w is not None or frame_h is not None
        ):
            w = frame_w if frame_w else 1280
            h = frame_h if frame_h else 720
            poly = poly * np.array([w, h], dtype=np.float32)
        return poly


# Backward compatibility alias
SeatROI = SeatDefinition


@dataclass
class SeatOccupancy:
    """Current live state of a seat in a running session."""

    seat: SeatDefinition
    state: str = SeatState.EMPTY
    assigned_track_id: Optional[int] = None
    assigned_detection: Optional[Detection] = None
    last_seen_timestamp_ms: Optional[float] = None
    consecutive_empty_frames: int = 0
    consecutive_occupied_frames: int = 0
    candidate_detections: List[Detection] = field(default_factory=list)
    # Person-count baseline: how many people normally sit in this ROI (e.g. a
    # desk mate inside a two-person ROI).  Only people beyond it count as an
    # additional person near the seat.
    person_count: int = 0
    established_person_count: Optional[int] = None
    count_history: Deque[Tuple[float, int]] = field(default_factory=deque)
    last_primary_bbox: Optional[Tuple[int, int, int, int]] = None

    @property
    def expected_person_count(self) -> int:
        capacity = int(self.seat.metadata.get("capacity", 1) or 1) if self.seat.metadata else 1
        # Until a baseline has settled, the current count is treated as normal.
        learned = self.established_person_count if self.established_person_count is not None else self.person_count
        return max(1, capacity, learned)


class SeatManager:
    """Manages Seat ROI definitions and computes Person -> Seat mapping for a room/camera."""

    def __init__(
        self,
        room_id: str = "",
        camera_id: Optional[str] = None,
        empty_timeout_ms: float = 5000.0,
        occlusion_grace_period_ms: float = 4000.0,
        count_settle_ms: float = 1000.0,
        count_window_ms: float = 20000.0,
    ):
        self.room_id = room_id
        self.camera_id = camera_id
        self.empty_timeout_ms = empty_timeout_ms
        self.occlusion_grace_period_ms = occlusion_grace_period_ms
        # Person-count baseline: the count seen most often in the recent window
        # (a desk mate inside the ROI).  A majority vote tolerates the frequent
        # one-frame detection dropouts that reset a "must persist" rule; a
        # visitor only becomes "normal" after being present most of the window.
        self.count_settle_ms = count_settle_ms
        self.count_window_ms = count_window_ms

        self.seats: Dict[str, SeatDefinition] = {}
        self.occupancies: Dict[str, SeatOccupancy] = {}
        # People walking between the rows (teacher, proctor) are not seat occupants
        self.residency = ResidencyMap()

    def load_seats(self, seat_defs: List[SeatDefinition] | List[Dict[str, Any]]) -> None:
        """Load or update seat definitions."""
        self.seats.clear()
        self.occupancies.clear()
        for s in seat_defs:
            seat_obj = s if isinstance(s, SeatDefinition) else SeatDefinition.from_dict(s)
            if seat_obj.enabled:
                self.seats[seat_obj.seat_code] = seat_obj
                self.occupancies[seat_obj.seat_code] = SeatOccupancy(seat=seat_obj)
        logger.info("SeatManager loaded %d active seats for room %s", len(self.seats), self.room_id)

    def load_from_json_file(self, file_path: str | Path) -> None:
        """Load seat definitions from local JSON configuration file."""
        p = Path(file_path)
        if not p.exists():
            logger.warning("Seat config file not found: %s", p)
            return
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        items = data if isinstance(data, list) else data.get("seats", [])
        self.load_seats(items)

    def get_seat_for_detection(
        self,
        detection: Detection,
        frame_w: Optional[int] = None,
        frame_h: Optional[int] = None,
    ) -> Optional[str]:
        """Find the best matching seat for a detection, including overlapping ROIs."""
        match = self._best_seat_for_point(
            self._anchor(detection), frame_w=frame_w, frame_h=frame_h
        )
        if match is not None:
            return match
        return self._best_seat_for_point(
            detection.center, frame_w=frame_w, frame_h=frame_h
        )

    @staticmethod
    def _point_match_score(
        seat: SeatDefinition,
        point: Tuple[float, float],
        *,
        frame_w: Optional[int] = None,
        frame_h: Optional[int] = None,
    ) -> Optional[Tuple[float, float]]:
        """Score a point inside a seat ROI without favoring physically larger ROIs.

        The first component is signed interior depth normalized by the square
        root of polygon area.  The second favors the polygon whose centroid is
        closer on the same normalized scale.  ``None`` means outside the ROI.
        """
        poly = seat.polygon_for_frame(pt=point, frame_w=frame_w, frame_h=frame_h)
        if len(poly) < 3:
            return None
        signed_distance = float(
            cv2.pointPolygonTest(poly, (float(point[0]), float(point[1])), True)
        )
        if signed_distance < 0.0:
            return None

        area_scale = max(float(np.sqrt(abs(cv2.contourArea(poly)))), 1.0)
        moments = cv2.moments(poly)
        if abs(moments["m00"]) > 1e-9:
            center_x = float(moments["m10"] / moments["m00"])
            center_y = float(moments["m01"] / moments["m00"])
        else:
            center_x, center_y = np.mean(poly, axis=0).tolist()
        centroid_distance = float(
            np.hypot(point[0] - center_x, point[1] - center_y)
        )
        return signed_distance / area_scale, -(centroid_distance / area_scale)

    def _best_seat_for_point(
        self,
        point: Tuple[float, float],
        *,
        frame_w: Optional[int] = None,
        frame_h: Optional[int] = None,
    ) -> Optional[str]:
        """Choose the strongest containing ROI independently of config order."""
        candidates: List[Tuple[float, float, str]] = []
        for seat_code, seat_def in self.seats.items():
            score = self._point_match_score(
                seat_def, point, frame_w=frame_w, frame_h=frame_h
            )
            if score is not None:
                candidates.append((score[0], score[1], seat_code))
        if not candidates:
            return None
        # Negated numeric fields select the highest score; seat_code provides
        # a stable final tie-break instead of YAML/dictionary insertion order.
        return sorted(candidates, key=lambda item: (-item[0], -item[1], item[2]))[0][2]

    def map_detections_to_seats(
        self,
        detections: List[Detection],
        timestamp_ms: float,
        frame_idx: int = 0,
        frame_w: Optional[int] = None,
        frame_h: Optional[int] = None,
    ) -> Tuple[Dict[str, Optional[Detection]], List[Detection]]:
        """Map person detections to stable Seat IDs.

        Returns:
            - mapped_seats: Dict mapping seat_code -> Detection (or None if empty/occluded)
            - unmapped_detections: Detections located outside all seat polygons (proctor, roaming candidate)
        """
        # Reset candidate lists for all seats
        for occ in self.occupancies.values():
            occ.candidate_detections = []

        unmapped: List[Detection] = []

        people = self.merge_duplicate_detections(detections)
        passing = self.residency.passers_by([self._track_point(d) for d in people], timestamp_ms)
        passers_by = {id(d) for d, flag in zip(people, passing) if flag}

        # Step 1: Assign each person (once) to candidate seats
        for det in people:
            x1, y1, x2, y2 = det.bbox
            centroid = ((x1 + x2) / 2.0, (y1 + y2) / 2.0)

            # Test all containing ROIs and choose the strongest spatial match.
            matched_seat_code = self._best_seat_for_point(
                self._anchor(det), frame_w=frame_w, frame_h=frame_h
            )

            # Fallback to centroid if the torso anchor did not match
            if not matched_seat_code:
                matched_seat_code = self._best_seat_for_point(
                    centroid, frame_w=frame_w, frame_h=frame_h
                )

            if matched_seat_code:
                self.occupancies[matched_seat_code].candidate_detections.append(det)
            else:
                unmapped.append(det)

        # Someone walking past (teacher, proctor) is not an extra person at a
        # seat that already has its resident.  A seat whose only person looks
        # like a passer-by keeps them: that is usually its own student with a
        # flickering detection.
        for occ in self.occupancies.values():
            residents = [d for d in occ.candidate_detections if id(d) not in passers_by]
            if residents and len(residents) < len(occ.candidate_detections):
                unmapped.extend(d for d in occ.candidate_detections if id(d) in passers_by)
                occ.candidate_detections = residents

        # Step 2: Update Occupancy states and resolve multiple-person situations
        mapped_results: Dict[str, Optional[Detection]] = {}

        for seat_code, occ in self.occupancies.items():
            candidates = occ.candidate_detections
            occ.person_count = len(candidates)
            self._update_person_baseline(occ, len(candidates), timestamp_ms)

            if len(candidates) == 1:
                # Single candidate in seat -> OCCUPIED
                det = candidates[0]
                occ.state = SeatState.OCCUPIED
                occ.assigned_detection = det
                occ.last_primary_bbox = tuple(det.bbox)
                occ.last_seen_timestamp_ms = timestamp_ms
                occ.consecutive_occupied_frames += 1
                occ.consecutive_empty_frames = 0
                mapped_results[seat_code] = det

            elif len(candidates) > 1:
                # Multiple candidates -> MULTIPLE_PERSON (physical count only;
                # whether that is unusual is decided against the baseline).
                occ.state = SeatState.MULTIPLE_PERSON
                primary_det = self._select_primary(occ, candidates, frame_w=frame_w, frame_h=frame_h)
                occ.assigned_detection = primary_det
                occ.last_primary_bbox = tuple(primary_det.bbox)
                occ.last_seen_timestamp_ms = timestamp_ms
                occ.consecutive_occupied_frames += 1
                occ.consecutive_empty_frames = 0
                mapped_results[seat_code] = primary_det

            else:
                # 0 candidates -> Check if temporarily occluded or completely empty
                occ.assigned_detection = None
                occ.consecutive_empty_frames += 1
                occ.consecutive_occupied_frames = 0

                time_since_last_seen = (
                    timestamp_ms - occ.last_seen_timestamp_ms
                    if occ.last_seen_timestamp_ms is not None
                    else float("inf")
                )

                if time_since_last_seen <= self.occlusion_grace_period_ms:
                    occ.state = SeatState.OCCLUDED
                elif time_since_last_seen > self.empty_timeout_ms:
                    occ.state = SeatState.EMPTY
                else:
                    occ.state = SeatState.UNKNOWN

                mapped_results[seat_code] = None

        return mapped_results, unmapped

    @staticmethod
    def _shoulders(det: Detection) -> Optional[Tuple[float, float, float]]:
        """Shoulder midpoint and shoulder width, or None without both shoulders."""
        kps = det.keypoints
        if kps is None or len(kps) < 7 or kps.shape[-1] < 3:
            return None
        left, right = kps[5], kps[6]
        if left[2] < SHOULDER_KEYPOINT_CONF or right[2] < SHOULDER_KEYPOINT_CONF:
            return None
        x = float(left[0] + right[0]) / 2.0
        y = float(left[1] + right[1]) / 2.0
        x1, y1, x2, y2 = det.bbox
        if not (x1 <= x <= x2 and y1 <= y <= y2):
            return None  # keypoints that do not belong to this box
        return (x, y, float(np.hypot(left[0] - right[0], left[1] - right[1])))

    @classmethod
    def _anchor(cls, det: Detection) -> Tuple[float, float]:
        """Point on the upper torso, just below the shoulders.

        The bottom of a pose box is unreliable in a crowded room: the box often
        stretches down over the legs of the student in the next row, which put
        the anchor in that student's seat.  The torso stays on the person.
        """
        shoulders = cls._shoulders(det)
        if shoulders is None:
            # Without shoulders, fall back to the desk-level point of the box
            x1, y1, x2, y2 = det.bbox
            return ((x1 + x2) / 2.0, float(y2) - (y2 - y1) * 0.15)
        x, y, width = shoulders
        return (x, y + 0.5 * max(width, 1.0))

    @classmethod
    def _track_point(cls, det: Detection) -> Tuple[float, float, float]:
        """Torso anchor plus the person's shoulder width, the residency distance unit."""
        shoulders = cls._shoulders(det)
        x, y = cls._anchor(det)
        if shoulders is not None:
            return (x, y, shoulders[2])
        return (x, y, 0.6 * float(det.bbox[2] - det.bbox[0]))

    @classmethod
    def merge_duplicate_detections(cls, detections: List[Detection]) -> List[Detection]:
        """Drop extra boxes the pose model emits for the same person.

        In crowded rows the model often returns a short upper-body box and a
        tall box for one student; counted separately they look like a second
        person at the seat.  Boxes whose shoulders coincide are one person, and
        the most confident box is kept.
        """
        kept: List[Detection] = []
        for det in sorted(detections, key=lambda d: -float(d.confidence)):
            shoulders = cls._shoulders(det)
            duplicate = False
            if shoulders is not None:
                for other in kept:
                    other_shoulders = cls._shoulders(other)
                    if other_shoulders is None:
                        continue
                    scale = max(shoulders[2], other_shoulders[2], 1.0)
                    if np.hypot(shoulders[0] - other_shoulders[0], shoulders[1] - other_shoulders[1]) < DUPLICATE_SHOULDER_RATIO * scale:
                        duplicate = True
                        break
            if not duplicate:
                kept.append(det)
        # Keep the caller's ordering for the boxes that remain
        kept_ids = {id(d) for d in kept}
        return [d for d in detections if id(d) in kept_ids]

    def _select_primary(
        self,
        occ: SeatOccupancy,
        candidates: List[Detection],
        *,
        frame_w: Optional[int] = None,
        frame_h: Optional[int] = None,
    ) -> Detection:
        """Pick the seat's own occupant, not merely the most confident box.

        Continuity with the previous occupant wins; otherwise the person whose
        anchor sits deepest inside the ROI (a seated student rather than
        someone standing at its edge).
        """
        if occ.last_primary_bbox is not None:
            from classroom_monitor.spatial_matcher import compute_bbox_iou

            best = max(candidates, key=lambda d: compute_bbox_iou(d.bbox, occ.last_primary_bbox))
            if compute_bbox_iou(best.bbox, occ.last_primary_bbox) >= 0.30:
                return best

        def depth(det: Detection) -> Tuple[float, float, float]:
            score = self._point_match_score(occ.seat, self._anchor(det), frame_w=frame_w, frame_h=frame_h)
            if score is None:
                score = self._point_match_score(occ.seat, det.center, frame_w=frame_w, frame_h=frame_h)
            depth_score, centrality = score if score is not None else (-1.0, -1e9)
            return depth_score, centrality, det.confidence

        return max(candidates, key=depth)

    def _update_person_baseline(self, occ: SeatOccupancy, count: int, timestamp_ms: float) -> None:
        history = occ.count_history
        history.append((timestamp_ms, count))
        while history and (timestamp_ms - history[0][0]) > self.count_window_ms:
            history.popleft()
        if (timestamp_ms - history[0][0]) < self.count_settle_ms:
            return  # not enough history yet; the current count counts as normal
        votes = Counter(c for _, c in history)
        best = max(votes.values())
        leaders = [c for c, v in votes.items() if v == best]
        if occ.established_person_count in leaders:
            return  # keep the current baseline on a tie
        occ.established_person_count = min(leaders)

    def to_seat_graph(self, camera_view: Optional[str] = None) -> Any:
        """Construct a connected SeatGraph representing all registered seats and spatial context."""
        from classroom_monitor.scene_context import CAMERA_FACING_SUBJECTS, DeskGeometry, SeatContext, SeatGraph
        graph = SeatGraph(room_id=self.room_id, camera_view=camera_view or CAMERA_FACING_SUBJECTS)
        for s_code, s_def in self.seats.items():
            desk_geo = None
            if s_def.desk_y is not None or s_def.desk_polygon is not None:
                desk_geo = DeskGeometry(
                    desk_boundary_y=s_def.desk_y,
                    writing_zone_polygon=s_def.desk_polygon,
                )
            ctx = SeatContext(
                seat_id=s_def.seat_code,
                room_id=s_def.room_id,
                camera_id=s_def.camera_id,
                seat_code=s_def.seat_code,
                desk_geometry=desk_geo,
                metadata=s_def.metadata,
            )
            graph.add_seat_context(ctx)

        # Auto-infer neighbors based on polygon centroids if not explicitly set
        graph.auto_infer_neighbors_from_polygons(list(self.seats.values()))
        return graph

    def get_seat_status_summary(self) -> Dict[str, Any]:
        """Summary of current room seating occupancy."""
        summary = {
            "total_seats": len(self.seats),
            "occupied": 0,
            "empty": 0,
            "occluded": 0,
            "multiple_person": 0,
            "unknown": 0,
        }
        for occ in self.occupancies.values():
            st = occ.state.lower()
            if st in summary:
                summary[st] += 1
        return summary
