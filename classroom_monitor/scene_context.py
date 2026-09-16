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
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

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
        else:
            self.capabilities.desk_hand_interaction = CapabilityStatus.ENABLED

        if not (self.neighbors.left_neighbor_id or self.neighbors.right_neighbor_id or self.neighbors.front_neighbor_id):
            self.capabilities.pairwise_relation = CapabilityStatus.DEGRADED

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SeatContext:
        desk_geo_data = data.get("desk_geometry")
        desk_geo = DeskGeometry.from_dict(desk_geo_data) if desk_geo_data else None

        # Also support legacy flat desk_y / desk_polygon
        if desk_geo is None:
            desk_y_flat = data.get("desk_y")
            desk_poly_flat = data.get("desk_polygon")
            if desk_y_flat is not None or desk_poly_flat is not None:
                desk_geo = DeskGeometry.from_dict({
                    "desk_boundary_y": desk_y_flat,
                    "writing_zone_polygon": desk_poly_flat,
                })

        neighbors_data = data.get("neighbors") or {}
        ref_dirs_data = data.get("reference_directions") or {}
        caps_data = data.get("capabilities") or {}

        return cls(
            seat_id=str(data.get("seat_id") or data.get("id") or data.get("seat_code", "")),
            room_id=str(data.get("room_id", "")),
            camera_id=data.get("camera_id"),
            seat_code=str(data.get("seat_code", "")),
            neighbors=SeatNeighbors.from_dict(neighbors_data),
            reference_directions=SeatReferenceDirections.from_dict(ref_dirs_data),
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


class SeatGraph:
    """Manages the full topological graph and spatial context of all seats in a room."""

    def __init__(self, room_id: str = ""):
        self.room_id = room_id
        self.seats_context: Dict[str, SeatContext] = {}

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

        for s_id, cx, cy in centroids:
            ctx = self.get_context(s_id)
            if not ctx:
                ctx = SeatContext(seat_id=s_id, room_id=self.room_id, seat_code=s_id)
                self.add_seat_context(ctx)

            # Find closest left neighbor (cx_other < cx, same row |cy - cy_other| < y_thresh)
            left_candidates = [
                (other_id, cx - other_cx)
                for other_id, other_cx, other_cy in centroids
                if other_id != s_id and (cx - other_cx) > 0 and (cx - other_cx) < x_dist_threshold and abs(cy - other_cy) < 100.0
            ]
            if left_candidates:
                left_candidates.sort(key=lambda item: item[1])
                ctx.neighbors.left_neighbor_id = left_candidates[0][0]

            # Find closest right neighbor (cx_other > cx)
            right_candidates = [
                (other_id, other_cx - cx)
                for other_id, other_cx, other_cy in centroids
                if other_id != s_id and (other_cx - cx) > 0 and (other_cx - cx) < x_dist_threshold and abs(cy - other_cy) < 100.0
            ]
            if right_candidates:
                right_candidates.sort(key=lambda item: item[1])
                ctx.neighbors.right_neighbor_id = right_candidates[0][0]

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
            "seats": {sid: ctx.to_dict() for sid, ctx in self.seats_context.items()},
        }
