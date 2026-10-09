"""Scene Context, Seat Graph, Desk Geometry, and Capability Gating Architecture.

Implements Scope 05 (FR-CTX-001 to FR-CTX-005) and Scope 14 (Capability Gating) of SRS v2.0:
- Per-Seat Context (neighbors, desk geometry, reference directions, calibration version).
- SeatGraph spatial adjacency representation.
- DeskGeometry with Writing Zone vs Under-Desk boundary testing.
- Capability Gating (ENABLED, DEGRADED, DISABLED, UNVALIDATED) for observation modalities.
- Backward compatibility with legacy Seat ROI configurations.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import cv2
import numpy as np

try:
    import yaml
except ImportError:
    yaml = None

logger = logging.getLogger("SceneContext")


class CapabilityStatus(str, Enum):
    ENABLED = "ENABLED"
    DEGRADED = "DEGRADED"
    DISABLED = "DISABLED"
    UNVALIDATED = "UNVALIDATED"


@dataclass
class DeskGeometry:
    """Seat-specific desk geometry and zone boundaries in camera space."""

    desk_boundary_y: Optional[float] = None
    writing_zone_polygon: Optional[np.ndarray] = None  # Shape (N, 2)
    under_desk_polygon: Optional[np.ndarray] = None    # Shape (N, 2)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DeskGeometry:
        desk_y = data.get("desk_boundary_y") or data.get("desk_y")
        if desk_y is not None:
            try:
                desk_y = float(desk_y)
            except (ValueError, TypeError):
                desk_y = None

        w_poly = data.get("writing_zone_polygon") or data.get("writing_zone")
        if isinstance(w_poly, str):
            try:
                w_poly = json.loads(w_poly)
            except Exception:
                w_poly = None
        if isinstance(w_poly, list) and len(w_poly) > 0:
            w_poly_arr = np.array(w_poly, dtype=np.float32)
        else:
            w_poly_arr = None

        u_poly = data.get("under_desk_polygon") or data.get("under_desk_zone")
        if isinstance(u_poly, str):
            try:
                u_poly = json.loads(u_poly)
            except Exception:
                u_poly = None
        if isinstance(u_poly, list) and len(u_poly) > 0:
            u_poly_arr = np.array(u_poly, dtype=np.float32)
        else:
            u_poly_arr = None

        return cls(
            desk_boundary_y=desk_y,
            writing_zone_polygon=w_poly_arr,
            under_desk_polygon=u_poly_arr,
            metadata=data.get("metadata", {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "desk_boundary_y": self.desk_boundary_y,
            "writing_zone_polygon": self.writing_zone_polygon.tolist() if self.writing_zone_polygon is not None else None,
            "under_desk_polygon": self.under_desk_polygon.tolist() if self.under_desk_polygon is not None else None,
            "metadata": self.metadata,
        }

    def contains_wrist_in_writing_zone(self, pt: Tuple[float, float]) -> bool:
        """Check if wrist point clearly resides inside the writing zone."""
        if self.writing_zone_polygon is not None and len(self.writing_zone_polygon) >= 3:
            res = cv2.pointPolygonTest(self.writing_zone_polygon, (float(pt[0]), float(pt[1])), False)
            return res >= 0
        if self.desk_boundary_y is not None:
            # Wrist is above the desk boundary (on table surface)
            return float(pt[1]) <= self.desk_boundary_y
        return False

    def is_wrist_below_desk(self, pt: Tuple[float, float]) -> bool:
        """Check if wrist point resides clearly below the desk boundary."""
        if self.under_desk_polygon is not None and len(self.under_desk_polygon) >= 3:
            res = cv2.pointPolygonTest(self.under_desk_polygon, (float(pt[0]), float(pt[1])), False)
            return res >= 0
        if self.desk_boundary_y is not None:
            return float(pt[1]) > self.desk_boundary_y
        return False


@dataclass
class SeatNeighbors:
    """Spatial relationship links to neighboring seats."""

    left_neighbor_id: Optional[str] = None
    right_neighbor_id: Optional[str] = None
    front_neighbor_id: Optional[str] = None
    back_neighbor_id: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SeatNeighbors:
        return cls(
            left_neighbor_id=data.get("left_neighbor_id") or data.get("left"),
            right_neighbor_id=data.get("right_neighbor_id") or data.get("right"),
            front_neighbor_id=data.get("front_neighbor_id") or data.get("front"),
            back_neighbor_id=data.get("back_neighbor_id") or data.get("back"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "left_neighbor_id": self.left_neighbor_id,
            "right_neighbor_id": self.right_neighbor_id,
            "front_neighbor_id": self.front_neighbor_id,
            "back_neighbor_id": self.back_neighbor_id,
        }

    def has_neighbor_in_direction(self, direction: str) -> bool:
        dir_norm = direction.upper().strip()
        if "LEFT" in dir_norm:
            return self.left_neighbor_id is not None
        if "RIGHT" in dir_norm:
            return self.right_neighbor_id is not None
        if "FRONT" in dir_norm:
            return self.front_neighbor_id is not None
        if "BACK" in dir_norm:
            return self.back_neighbor_id is not None
        return False

    def get_neighbor_id(self, direction: str) -> Optional[str]:
        dir_norm = direction.upper().strip()
        if "LEFT" in dir_norm:
            return self.left_neighbor_id
        if "RIGHT" in dir_norm:
            return self.right_neighbor_id
        if "FRONT" in dir_norm:
            return self.front_neighbor_id
        if "BACK" in dir_norm:
            return self.back_neighbor_id
        return None


@dataclass
class SeatReferenceDirections:
    """Camera-space perspective baseline and neighbor direction vectors."""

    baseline_yaw: float = 0.0      # Perspective neutral head yaw for this seat
    baseline_pitch: float = 0.0    # Neutral reading/writing pitch
    left_direction_yaw: float = -45.0
    right_direction_yaw: float = 45.0
    front_direction_yaw: float = 0.0

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SeatReferenceDirections:
        return cls(
            baseline_yaw=float(data.get("baseline_yaw", 0.0)),
            baseline_pitch=float(data.get("baseline_pitch", 0.0)),
            left_direction_yaw=float(data.get("left_direction_yaw", -45.0)),
            right_direction_yaw=float(data.get("right_direction_yaw", 45.0)),
            front_direction_yaw=float(data.get("front_direction_yaw", 0.0)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "baseline_yaw": self.baseline_yaw,
            "baseline_pitch": self.baseline_pitch,
            "left_direction_yaw": self.left_direction_yaw,
            "right_direction_yaw": self.right_direction_yaw,
            "front_direction_yaw": self.front_direction_yaw,
        }


@dataclass
class SeatCapabilities:
    """Per-seat capability state matrix based on camera resolution and calibration depth."""

    head_orientation: CapabilityStatus = CapabilityStatus.ENABLED
    body_lean: CapabilityStatus = CapabilityStatus.ENABLED
    desk_hand_interaction: CapabilityStatus = CapabilityStatus.DISABLED
    seat_occupancy: CapabilityStatus = CapabilityStatus.ENABLED
    pairwise_relation: CapabilityStatus = CapabilityStatus.ENABLED

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SeatCapabilities:
        def _parse(val: Any, default: CapabilityStatus) -> CapabilityStatus:
            if isinstance(val, str):
                try:
                    return CapabilityStatus(val.upper())
                except ValueError:
                    pass
            return default

        return cls(
            head_orientation=_parse(data.get("head_orientation"), CapabilityStatus.ENABLED),
            body_lean=_parse(data.get("body_lean"), CapabilityStatus.ENABLED),
            desk_hand_interaction=_parse(data.get("desk_hand_interaction"), CapabilityStatus.DISABLED),
            seat_occupancy=_parse(data.get("seat_occupancy"), CapabilityStatus.ENABLED),
            pairwise_relation=_parse(data.get("pairwise_relation"), CapabilityStatus.ENABLED),
        )

    def to_dict(self) -> Dict[str, str]:
        return {
            "head_orientation": self.head_orientation.value,
            "body_lean": self.body_lean.value,
            "desk_hand_interaction": self.desk_hand_interaction.value,
            "seat_occupancy": self.seat_occupancy.value,
            "pairwise_relation": self.pairwise_relation.value,
        }


@dataclass
class SeatContext:
    """Comprehensive contextual container for an individual Seat."""

    seat_id: str
    room_id: str = ""
    camera_id: Optional[str] = None
    seat_code: str = ""
    neighbors: SeatNeighbors = field(default_factory=SeatNeighbors)
    reference_directions: SeatReferenceDirections = field(default_factory=SeatReferenceDirections)
    desk_geometry: Optional[DeskGeometry] = None
    capabilities: SeatCapabilities = field(default_factory=SeatCapabilities)
    calibration_version: str = "v2.0"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Automatic capability gating based on calibrated features
        if self.desk_geometry is None or (
            self.desk_geometry.desk_boundary_y is None
            and self.desk_geometry.writing_zone_polygon is None
        ):
            self.capabilities.desk_hand_interaction = CapabilityStatus.DISABLED
        elif self.desk_geometry.writing_zone_polygon is not None and len(self.desk_geometry.writing_zone_polygon) >= 3:
            self.capabilities.desk_hand_interaction = CapabilityStatus.ENABLED
        elif self.desk_geometry.desk_boundary_y is not None:
            self.capabilities.desk_hand_interaction = CapabilityStatus.DEGRADED
        else:
            self.capabilities.desk_hand_interaction = CapabilityStatus.DISABLED

        if not (self.neighbors.left_neighbor_id or self.neighbors.right_neighbor_id or self.neighbors.front_neighbor_id):
            self.capabilities.pairwise_relation = CapabilityStatus.DEGRADED

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SeatContext:
        # 1. Desk Geometry: Nested dictionary takes precedence, fallback to flat keys
        desk_geo_data = data.get("desk_geometry")
        if desk_geo_data is not None and isinstance(desk_geo_data, dict):
            desk_geo = DeskGeometry.from_dict(desk_geo_data)
        else:
            desk_boundary_y = data.get("desk_boundary_y")
            if desk_boundary_y is None:
                desk_boundary_y = data.get("desk_y")
            writing_poly = data.get("writing_zone_polygon") or data.get("writing_zone") or data.get("desk_polygon")
            under_poly = data.get("under_desk_polygon") or data.get("under_desk_zone")
            if desk_boundary_y is not None or writing_poly is not None or under_poly is not None:
                desk_geo = DeskGeometry.from_dict({
                    "desk_boundary_y": desk_boundary_y,
                    "writing_zone_polygon": writing_poly,
                    "under_desk_polygon": under_poly,
                })
            else:
                desk_geo = None

        # 2. Reference Directions: Nested dictionary takes precedence, fallback to flat keys
        ref_dirs_data = data.get("reference_directions")
        if ref_dirs_data is not None and isinstance(ref_dirs_data, dict):
            ref_dirs = SeatReferenceDirections.from_dict(ref_dirs_data)
        else:
            ref_dirs = SeatReferenceDirections.from_dict({
                "baseline_yaw": data.get("baseline_yaw", 0.0),
                "baseline_pitch": data.get("baseline_pitch", 0.0),
                "left_direction_yaw": data.get("left_direction_yaw", -45.0),
                "right_direction_yaw": data.get("right_direction_yaw", 45.0),
                "front_direction_yaw": data.get("front_direction_yaw", 0.0),
            })

        # 3. Neighbors & Capabilities
        neighbors_data = data.get("neighbors") or {}
        caps_data = data.get("capabilities") or {}

        return cls(
            seat_id=str(data.get("seat_id") or data.get("id") or data.get("seat_code", "")),
            room_id=str(data.get("room_id", "")),
            camera_id=data.get("camera_id"),
            seat_code=str(data.get("seat_code") or data.get("seat_id", "")),
            neighbors=SeatNeighbors.from_dict(neighbors_data),
            reference_directions=ref_dirs,
            desk_geometry=desk_geo,
            capabilities=SeatCapabilities.from_dict(caps_data),
            calibration_version=str(data.get("calibration_version", "v2.0")),
            metadata=data.get("metadata", {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "seat_id": self.seat_id,
            "room_id": self.room_id,
            "camera_id": self.camera_id,
            "seat_code": self.seat_code,
            "neighbors": self.neighbors.to_dict(),
            "reference_directions": self.reference_directions.to_dict(),
            "desk_geometry": self.desk_geometry.to_dict() if self.desk_geometry else None,
            "capabilities": self.capabilities.to_dict(),
            "calibration_version": self.calibration_version,
            "metadata": self.metadata,
        }


CAMERA_FACING_SUBJECTS = "facing_subjects"   # camera in front of the room, looking at faces
CAMERA_BEHIND_SUBJECTS = "behind_subjects"   # camera behind the candidates
CAMERA_VIEWS = (CAMERA_FACING_SUBJECTS, CAMERA_BEHIND_SUBJECTS)

# Inferred left/right neighbours: ROIs at most this many seat widths apart, and no
# more than this vertical offset per unit of horizontal offset (same row)
NEIGHBOR_MAX_GAP_SEAT_WIDTHS = 0.15
NEIGHBOR_MAX_ROW_SLOPE = 0.5
# ...and its tilt within this of the room's typical row tilt
NEIGHBOR_MAX_TILT_DEVIATION = 0.25


class SeatGraph:
    """Manages the full topological graph and spatial context of all seats in a room.

    Neighbour directions are SUBJECT-centric: ``left_neighbor_id`` is the seat on
    the candidate's own left, matching the head-turn and torso-lean episodes.
    With the camera facing the candidates (``camera_view="facing_subjects"``)
    the candidate's left is the RIGHT side of the image.
    """

    def __init__(self, room_id: str = "", camera_view: str = CAMERA_FACING_SUBJECTS):
        self.room_id = room_id
        if camera_view not in CAMERA_VIEWS:
            raise ValueError(f"camera_view must be one of {CAMERA_VIEWS}, got '{camera_view}'")
        self.camera_view = camera_view
        self.seats_context: Dict[str, SeatContext] = {}

    @property
    def mirrors_image(self) -> bool:
        """True when the candidate's left/right are the image's right/left."""
        return self.camera_view == CAMERA_FACING_SUBJECTS

    def add_seat_context(self, context: SeatContext) -> None:
        self.seats_context[context.seat_id] = context
        if context.seat_code and context.seat_code != context.seat_id:
            self.seats_context[context.seat_code] = context

    def get_context(self, seat_id_or_code: str) -> Optional[SeatContext]:
        return self.seats_context.get(seat_id_or_code)

    def auto_infer_neighbors_from_polygons(
        self,
        seats_definitions: List[Any],
        x_dist_threshold: float = 350.0,
        y_dist_threshold: float = 250.0,
    ) -> None:
        """Heuristically infer left/right/front/back neighbors from seat polygon centroids."""
        centroids: List[Tuple[str, float, float]] = []
        widths: Dict[str, float] = {}
        polygons: Dict[str, np.ndarray] = {}
        for s in seats_definitions:
            s_id = getattr(s, "seat_code", None) or getattr(s, "seat_id", None) or str(s.get("seat_code", ""))
            poly = getattr(s, "polygon", None)
            if poly is None and isinstance(s, dict):
                poly = s.get("polygon") or s.get("polygon_json")
            if poly is not None and len(poly) > 0:
                poly_arr = np.array(poly, dtype=np.float32)
                cx = float(np.mean(poly_arr[:, 0]))
                cy = float(np.mean(poly_arr[:, 1]))
                centroids.append((s_id, cx, cy))
                widths[s_id] = float(np.ptp(poly_arr[:, 0]))
                polygons[s_id] = poly_arr

        def side_by_side(s_id: str, other_id: str, dx: float, dy: float, room_tilt: Optional[float]) -> bool:
            """Left/right neighbours sit next to each other in the same row.

            Desk mates' ROIs touch; a seat across an aisle leaves a gap.  The
            gap is measured in seat widths, so far rows (small ROIs) and near
            rows (large ROIs) are judged alike.  Seats in front of or behind
            each other also touch, but the line between them is steeper than
            the rows or tilted the other way: rows of a room share one tilt in
            the image (perspective), estimated from the clearest links.
            """
            width = max(1.0, (widths.get(s_id, 0.0) + widths.get(other_id, 0.0)) / 2.0)
            if not (0.0 < dx <= x_dist_threshold and abs(dy) <= NEIGHBOR_MAX_ROW_SLOPE * dx):
                return False
            if polygon_gap(polygons[s_id], polygons[other_id]) > NEIGHBOR_MAX_GAP_SEAT_WIDTHS * width:
                return False
            return room_tilt is None or abs(dy / dx - room_tilt) <= NEIGHBOR_MAX_TILT_DEVIATION

        def polygon_gap(a: np.ndarray, b: np.ndarray) -> float:
            """Shortest distance between two polygons; 0 when they touch or overlap."""
            if len(a) < 3 or len(b) < 3:
                return float("inf")
            gap_a = min(-cv2.pointPolygonTest(b, (float(x), float(y)), True) for x, y in a)
            gap_b = min(-cv2.pointPolygonTest(a, (float(x), float(y)), True) for x, y in b)
            return max(0.0, min(gap_a, gap_b))

        def nearest(s_id: str, cx: float, cy: float, sign: float, room_tilt: Optional[float]) -> Optional[str]:
            """Closest seat beside this one on one side of the image (sign -1 = left)."""
            candidates = []
            for other_id, other_cx, other_cy in centroids:
                dx = sign * (other_cx - cx)
                # tilt measured left -> right, whichever side the other seat is on
                dy_lr = (other_cy - cy) if sign > 0 else (cy - other_cy)
                if other_id != s_id and side_by_side(s_id, other_id, dx, dy_lr, room_tilt):
                    candidates.append((float(np.hypot(other_cx - cx, other_cy - cy)), other_id))
            return min(candidates)[1] if candidates else None

        def mutual_links(room_tilt: Optional[float]) -> Tuple[Dict[str, Optional[str]], Dict[str, Optional[str]]]:
            left = {s_id: nearest(s_id, cx, cy, -1.0, room_tilt) for s_id, cx, cy in centroids}
            right = {s_id: nearest(s_id, cx, cy, 1.0, room_tilt) for s_id, cx, cy in centroids}
            return left, right

        # Pass 1: the room's row tilt from the clearest links; pass 2: links that follow it
        image_left, image_right = mutual_links(None)
        position = {s_id: (cx, cy) for s_id, cx, cy in centroids}
        first = [(s_id, right) for s_id, right in image_right.items() if right and image_left.get(right) == s_id]
        if len(first) >= 3:
            room_tilt = float(np.median([
                (position[r][1] - position[l][1]) / max(position[r][0] - position[l][0], 1.0) for l, r in first
            ]))
            image_left, image_right = mutual_links(room_tilt)

        for s_id, cx, cy in centroids:
            ctx = self.get_context(s_id)
            if not ctx:
                ctx = SeatContext(seat_id=s_id, room_id=self.room_id, seat_code=s_id)
                self.add_seat_context(ctx)

            # Neighbours must agree both ways: A has B on its right and B has A
            # on its left.  A one-sided match is usually a seat in another row.
            image_left_id = image_left[s_id] if image_left[s_id] and image_right.get(image_left[s_id]) == s_id else None
            image_right_id = image_right[s_id] if image_right[s_id] and image_left.get(image_right[s_id]) == s_id else None

            # Image sides -> the candidate's own sides
            if self.mirrors_image:
                ctx.neighbors.left_neighbor_id = image_right_id
                ctx.neighbors.right_neighbor_id = image_left_id
            else:
                ctx.neighbors.left_neighbor_id = image_left_id
                ctx.neighbors.right_neighbor_id = image_right_id

            # Find front neighbor (cy_other > cy in overhead perspective)
            front_candidates = [
                (other_id, other_cy - cy)
                for other_id, other_cx, other_cy in centroids
                if other_id != s_id and (other_cy - cy) > 50.0 and (other_cy - cy) < y_dist_threshold and abs(cx - other_cx) < 120.0
            ]
            if front_candidates:
                front_candidates.sort(key=lambda item: item[1])
                ctx.neighbors.front_neighbor_id = front_candidates[0][0]

            # Update capability
            if ctx.neighbors.left_neighbor_id or ctx.neighbors.right_neighbor_id:
                ctx.capabilities.pairwise_relation = CapabilityStatus.ENABLED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "room_id": self.room_id,
            "camera_view": self.camera_view,
            "seats": {sid: ctx.to_dict() for sid, ctx in self.seats_context.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SeatGraph:
        room_id = str(data.get("room_id") or data.get("room_code", ""))
        graph = cls(room_id=room_id, camera_view=str(data.get("camera_view") or CAMERA_FACING_SUBJECTS))
        seats_data = data.get("seats", [])
        if isinstance(seats_data, dict):
            for sid, s_dict in seats_data.items():
                if isinstance(s_dict, dict):
                    ctx = SeatContext.from_dict(s_dict)
                    graph.add_seat_context(ctx)
        elif isinstance(seats_data, list):
            for s_item in seats_data:
                if isinstance(s_item, dict):
                    ctx = SeatContext.from_dict(s_item)
                    graph.add_seat_context(ctx)
        return graph

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> SeatGraph:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Scene configuration file not found: {p}")
        text = p.read_text(encoding="utf-8")
        if p.suffix.lower() in [".yaml", ".yml"]:
            if yaml is None:
                raise ImportError("PyYAML is required to parse YAML scene configs")
            data = yaml.safe_load(text) or {}
        else:
            data = json.loads(text)
        return cls.from_dict(data)


@dataclass
class SceneProfile:
    """Complete Scene Profile containing room metadata, resolution, and SeatGraph."""

    scene_id: str
    room_code: str
    room_name: str
    camera_id: str
    video_file: str
    video_resolution: Dict[str, Any]
    seat_graph: SeatGraph
    calibration_version: str = "v2.0"
    raw_config: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SceneProfile:
        seat_graph = SeatGraph.from_dict(data)
        seats_list = data.get("seats", [])
        if isinstance(seats_list, list) and len(seats_list) > 0:
            has_neighbors = any(
                isinstance(s, dict) and s.get("neighbors") and any(s["neighbors"].values())
                for s in seats_list
            )
            if not has_neighbors:
                seat_graph.auto_infer_neighbors_from_polygons(seats_list)

        return cls(
            scene_id=str(data.get("scene_id", "scene")),
            room_code=str(data.get("room_code", "")),
            room_name=str(data.get("room_name", "")),
            camera_id=str(data.get("camera_id", "")),
            video_file=str(data.get("video_file", "")),
            video_resolution=data.get("video_resolution", {"width": 1280, "height": 720, "fps": 30.0}),
            seat_graph=seat_graph,
            calibration_version=str(data.get("calibration_version", "v2.0")),
            raw_config=data,
        )

    @classmethod
    def from_file(cls, path: Union[str, Path]) -> SceneProfile:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Scene config file not found: {p}")
        text = p.read_text(encoding="utf-8")
        if p.suffix.lower() in [".yaml", ".yml"]:
            if yaml is None:
                raise ImportError("PyYAML is required to parse YAML scene configs")
            data = yaml.safe_load(text) or {}
        else:
            data = json.loads(text)

        profile = cls.from_dict(data)
        if not data.get("scene_id"):
            profile.scene_id = p.stem
        return profile

