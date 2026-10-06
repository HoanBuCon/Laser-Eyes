"""Single source of truth for Classroom seat calibration.

Seat ROIs are drawn by people in the web calibration page (``/calibration``)
and stored in SQLite.  Every run — web LIVE and the developer CLI alike —
builds its ``SeatManager`` polygons and ``SeatGraph`` context from those
stored seats through :func:`build_scene_seating`, so there is exactly one
calibration and it is the one people edit on the website.

Per-seat context lives in the seat's ``context_json`` (all optional):

* ``capacity``       – people who normally sit inside this ROI (default 1)
* ``camera_view``    – ``facing_subjects`` | ``behind_subjects`` (room-wide)
* ``desk_y``         – desk boundary line (enables wrist zones, DEGRADED)
* ``baseline_yaw``   – static head-yaw offset (the runtime also learns one)

Scene YAML files under ``configs/scenes`` are only *templates*: the
calibration page can import one into the database as a starting layout
(:func:`load_scene_template`).  They are never read by a run directly.

``DemoVideoConfig.seats_preset`` (seat dictionaries passed in code) is kept
for tests and controlled experiments; presets never set it.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from classroom_monitor.scene_context import CAMERA_FACING_SUBJECTS, CAMERA_VIEWS, DeskGeometry, SeatGraph
from classroom_monitor.seat_manager import SeatDefinition, SeatManager

logger = logging.getLogger("SceneSeating")

CALIBRATION_PAGE = "/calibration"


class CalibrationMismatchError(RuntimeError):
    """Seat polygons and seat context do not describe the same seats, or no seats exist."""


@dataclass
class SceneSeating:
    seat_defs: List[SeatDefinition]
    seat_graph: SeatGraph
    source: str      # "database" | "preset"
    source_ref: str  # room code or preset name

    @property
    def seat_codes(self) -> List[str]:
        return [s.seat_code for s in self.seat_defs]

    def describe(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "source_ref": self.source_ref,
            "camera_view": self.seat_graph.camera_view,
            "seat_count": len(self.seat_defs),
            "seat_codes": self.seat_codes,
        }


def validate_seating(seat_defs: List[SeatDefinition], seat_graph: SeatGraph, source_ref: str = "") -> None:
    """Every runtime seat must have context from the same calibration."""
    if not seat_defs:
        raise CalibrationMismatchError(
            f"No Seat ROI is calibrated for {source_ref or 'this room'}. "
            f"Draw the seats on the web calibration page ({CALIBRATION_PAGE}) and save them."
        )
    missing = [s.seat_code for s in seat_defs if seat_graph.get_context(s.seat_code) is None]
    if missing:
        raise CalibrationMismatchError(
            f"Seat context missing for {len(missing)} seat(s) from {source_ref}: {', '.join(missing)}"
        )
    bad_polygons = [s.seat_code for s in seat_defs if len(s.polygon) < 3]
    if bad_polygons:
        raise CalibrationMismatchError(
            f"Seats without a valid polygon in {source_ref}: {', '.join(bad_polygons)}. "
            f"Fix them on the web calibration page ({CALIBRATION_PAGE})."
        )


def build_scene_seating(config: Any) -> SceneSeating:
    if getattr(config, "seats_preset", None):
        seating = _from_preset(config)
    else:
        seating = _from_database(config)
    validate_seating(seating.seat_defs, seating.seat_graph, seating.source_ref)
    logger.info(
        "Seat calibration: %s (%s), %d seats, camera %s",
        seating.source, seating.source_ref, len(seating.seat_defs), seating.seat_graph.camera_view,
    )
    return seating


def _seat_definition(code: str, label: Optional[str], polygon: Any, config: Any, context: Dict[str, Any]) -> SeatDefinition:
    if isinstance(polygon, str):
        polygon = json.loads(polygon)
    if polygon and isinstance(polygon[0], dict):
        polygon = [[float(p.get("x", 0.0)), float(p.get("y", 0.0))] for p in polygon]
    desk_y = context.get("desk_y")
    return SeatDefinition(
        seat_id=code,
        room_id=config.room_code,
        seat_code=code,
        seat_label=label or code,
        polygon=np.array(polygon or [], dtype=np.float32).reshape(-1, 2),
        camera_id=config.camera_id,
        desk_y=float(desk_y) if desk_y is not None else None,
        metadata={"capacity": max(1, int(context.get("capacity", 1) or 1))},
    )


def _graph_from_definitions(
    room_code: str,
    seat_defs: List[SeatDefinition],
    context_by_code: Dict[str, Dict[str, Any]],
    camera_view: str,
) -> SeatGraph:
    mgr = SeatManager(room_id=room_code)
    mgr.load_seats(seat_defs)
    graph = mgr.to_seat_graph(camera_view=camera_view)
    for code, extra in context_by_code.items():
        ctx = graph.get_context(code)
        if ctx is None:
            continue
        if extra.get("baseline_yaw") is not None:
            ctx.reference_directions.baseline_yaw = float(extra["baseline_yaw"])
        if extra.get("baseline_pitch") is not None:
            ctx.reference_directions.baseline_pitch = float(extra["baseline_pitch"])
        desk_y = extra.get("desk_y")
        if desk_y is not None:
            if ctx.desk_geometry is None:
                ctx.desk_geometry = DeskGeometry(desk_boundary_y=float(desk_y))
            else:
                ctx.desk_geometry.desk_boundary_y = float(desk_y)
            ctx.__post_init__()  # re-derive desk capability from the geometry
    return graph


def _camera_view_from_contexts(contexts: List[Dict[str, Any]], default: str) -> str:
    views = [c.get("camera_view") for c in contexts if c.get("camera_view") in CAMERA_VIEWS]
    if not views:
        return default
    view, _count = Counter(views).most_common(1)[0]
    return view


def _from_preset(config: Any) -> SceneSeating:
    seat_defs: List[SeatDefinition] = []
    contexts: Dict[str, Dict[str, Any]] = {}
    for s in config.seats_preset:
        code = s["seat_code"]
        context = {k: s.get(k) for k in ("desk_y", "baseline_yaw", "capacity", "camera_view") if s.get(k) is not None}
        seat_defs.append(_seat_definition(code, s.get("seat_label"), s.get("polygon_json") or s.get("polygon"), config, context))
        contexts[code] = context
    view = _camera_view_from_contexts(list(contexts.values()), getattr(config, "camera_view", CAMERA_FACING_SUBJECTS))
    graph = _graph_from_definitions(config.room_code, seat_defs, contexts, view)
    return SceneSeating(seat_defs, graph, "preset", config.name)


def _video_name(path_value: Any) -> str:
    return Path(str(path_value or "").replace("\\", "/")).name.lower()


def _from_database(config: Any) -> SceneSeating:
    from storage.database import SessionLocal, init_db
    from storage.db_models import Camera, ExamRoom, SeatROI

    init_db()
    db = SessionLocal()
    try:
        room = db.query(ExamRoom).filter(ExamRoom.room_code == config.room_code).first()
        rows = (
            db.query(SeatROI).filter(SeatROI.room_id == room.id, SeatROI.enabled == True).all()  # noqa: E712
            if room is not None else []
        )
        # A room may hold seats drawn on more than one camera; use the camera
        # that shows this run's video.
        by_camera: Dict[str, List[Any]] = {}
        for row in rows:
            by_camera.setdefault(row.camera_id or "", []).append(row)
        if len(by_camera) > 1:
            wanted = _video_name(config.video_path)
            matching = [
                cam_id for cam_id in by_camera
                if (cam := db.get(Camera, cam_id)) is not None and _video_name(cam.source_uri) == wanted
            ]
            if len(matching) != 1:
                raise CalibrationMismatchError(
                    f"Room {config.room_code} has Seat ROIs on {len(by_camera)} cameras and none is unambiguously "
                    f"the camera for {wanted}. Keep the seats of one camera on the calibration page ({CALIBRATION_PAGE})."
                )
            rows = by_camera[matching[0]]

        seat_defs: List[SeatDefinition] = []
        contexts: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            context = json.loads(row.context_json) if row.context_json else {}
            seat_defs.append(_seat_definition(row.seat_code, row.seat_label, row.polygon_json, config, context))
            contexts[row.seat_code] = context
    finally:
        db.close()

    view = _camera_view_from_contexts(list(contexts.values()), getattr(config, "camera_view", CAMERA_FACING_SUBJECTS))
    graph = _graph_from_definitions(config.room_code, seat_defs, contexts, view)
    return SceneSeating(seat_defs, graph, "database", config.room_code)


def load_scene_template(path: Path) -> Dict[str, Any]:
    """Read a scene YAML as an importable starting layout for the calibration page."""
    import yaml

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    camera_view = data.get("camera_view") or CAMERA_FACING_SUBJECTS
    seats = []
    for raw in data.get("seats", []) or []:
        code = raw.get("seat_code") or raw.get("seat_id")
        polygon = raw.get("polygon") or []
        if not code or len(polygon) < 3:
            continue
        context: Dict[str, Any] = {"camera_view": camera_view, "capacity": int(raw.get("capacity", 1) or 1)}
        if raw.get("desk_boundary_y") is not None:
            context["desk_y"] = float(raw["desk_boundary_y"])
        if raw.get("baseline_yaw") is not None:
            context["baseline_yaw"] = float(raw["baseline_yaw"])
        seats.append({
            "seat_code": code,
            "seat_label": raw.get("seat_label") or code,
            "polygon_json": polygon,
            "context_json": context,
            "enabled": True,
        })
    return {"room_code": data.get("room_code"), "camera_view": camera_view, "seats": seats}
