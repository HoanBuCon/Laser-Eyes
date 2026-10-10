"""What the calibration page needs to check a room's Seat ROIs.

* coverage: seats with an ROI against the room's capacity,
* the neighbour graph the analysis will infer from the polygons (the same
  ``SeatGraph`` the pipeline uses), so a badly drawn ROI that loses its
  neighbour shows up while drawing,
* overlapping ROIs, a cause of "multi person" false positives.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
from sqlalchemy.orm import Session

from classroom_monitor.demo.seating import _camera_view_from_contexts, _graph_from_definitions, _seat_definition
from classroom_monitor.scene_context import CAMERA_FACING_SUBJECTS
from storage.db_models import Camera, ExamRoom, SeatROI

# Overlap is measured on a reduced raster; precise enough to flag real overlaps
_RASTER_SCALE = 0.25
OVERLAP_WARN_RATIO = 0.2  # share of the smaller ROI


def _polygon(row: SeatROI) -> List[List[float]]:
    raw = json.loads(row.polygon_json) if isinstance(row.polygon_json, str) else row.polygon_json
    if raw and isinstance(raw[0], dict):
        raw = [[float(p.get("x", 0.0)), float(p.get("y", 0.0))] for p in raw]
    return [[float(x), float(y)] for x, y in (raw or [])]


def _overlaps(polygons: Dict[str, List[List[float]]]) -> List[Dict[str, Any]]:
    if len(polygons) < 2:
        return []
    pts = np.array([p for poly in polygons.values() for p in poly], dtype=np.float32)
    if not len(pts):
        return []
    w = int(pts[:, 0].max() * _RASTER_SCALE) + 2
    h = int(pts[:, 1].max() * _RASTER_SCALE) + 2
    masks: Dict[str, np.ndarray] = {}
    for code, poly in polygons.items():
        if len(poly) < 3:
            continue
        mask = np.zeros((h, w), dtype=np.uint8)
        cv2.fillPoly(mask, [np.round(np.array(poly) * _RASTER_SCALE).astype(np.int32)], 1)
        masks[code] = mask
    codes = sorted(masks)
    found = []
    for i, a in enumerate(codes):
        for b in codes[i + 1:]:
            inter = int(np.logical_and(masks[a], masks[b]).sum())
            if not inter:
                continue
            ratio = inter / max(1, min(int(masks[a].sum()), int(masks[b].sum())))
            if ratio >= OVERLAP_WARN_RATIO:
                found.append({"seats": [a, b], "ratio": round(ratio, 3)})
    return found


def room_seat_layout(db: Session, room: ExamRoom, camera_id: Optional[str] = None) -> Dict[str, Any]:
    query = db.query(SeatROI).filter(SeatROI.room_id == room.id, SeatROI.enabled == True)  # noqa: E712
    rows = query.all()
    cameras = sorted({r.camera_id or "" for r in rows})
    if camera_id is None and len(cameras) > 1:
        # The camera with most seats is the one being calibrated
        camera_id = max(cameras, key=lambda c: sum(1 for r in rows if (r.camera_id or "") == c))
    if camera_id is not None:
        rows = [r for r in rows if (r.camera_id or "") == camera_id]
    camera = db.get(Camera, camera_id) if camera_id else None

    polygons = {r.seat_code: _polygon(r) for r in rows}
    contexts = {r.seat_code: (json.loads(r.context_json) if r.context_json else {}) for r in rows}
    config = SimpleNamespace(room_code=room.room_code or room.id, camera_id=camera_id or "")
    edges: List[Dict[str, Any]] = []
    isolated: List[str] = []
    error = None
    if rows:
        try:
            defs = [_seat_definition(r.seat_code, r.seat_label, polygons[r.seat_code], config, contexts[r.seat_code]) for r in rows]
            view = _camera_view_from_contexts(list(contexts.values()), CAMERA_FACING_SUBJECTS)
            graph = _graph_from_definitions(config.room_code, defs, contexts, view)
            seen: set[Tuple[str, str]] = set()
            for code in polygons:
                ctx = graph.get_context(code)
                if ctx is None:
                    continue
                links = {
                    "side": [ctx.neighbors.left_neighbor_id, ctx.neighbors.right_neighbor_id],
                    "front": [ctx.neighbors.front_neighbor_id, ctx.neighbors.back_neighbor_id],
                }
                linked = False
                for kind, others in links.items():
                    for other in others:
                        other_ctx = graph.get_context(other) if other else None
                        other_code = other_ctx.seat_code if other_ctx is not None else other
                        if not other_code or other_code not in polygons:
                            continue
                        linked = linked or kind == "side"
                        key = tuple(sorted((code, other_code)))
                        if (key, kind) in seen:
                            continue
                        seen.add((key, kind))
                        edges.append({"a": key[0], "b": key[1], "kind": kind})
                if not linked:
                    isolated.append(code)
        except Exception as exc:  # a broken ROI must not break the page
            error = str(exc)

    capacity = int(room.capacity or 0)
    return {
        "room_id": room.id,
        "room_code": room.room_code,
        "camera_id": camera_id,
        "camera_name": camera.name if camera else None,
        "cameras": cameras,
        "capacity": capacity,
        "seat_count": len(rows),
        "coverage": round(len(rows) / capacity, 3) if capacity else None,
        "seats": [{"seat_code": code, "polygon": poly} for code, poly in sorted(polygons.items())],
        "edges": edges,
        "seats_without_side_neighbour": sorted(isolated),
        "overlaps": _overlaps(polygons),
        "error": error,
    }
