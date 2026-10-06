"""Seat ROI Management Endpoints for VIGIL AI REST API (SRS v1.0).

Canonical Endpoints:
- GET    /api/v1/rooms/{room_id}/seats  (List seats for room)
- POST   /api/v1/rooms/{room_id}/seats  (Create seat for room)
- POST   /api/v1/rooms/{room_id}/seats/bulk (Bulk sync/upsert seats)
- GET    /api/v1/seats/{seat_id}        (Get single seat by ID)
- PUT    /api/v1/seats/{seat_id}        (Update seat polygon/status)
- DELETE /api/v1/seats/{seat_id}        (Delete seat)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from api.schemas import SeatBulkUpsertRequest, SeatCreate, SeatResponse, SeatUpdate
from storage.database import get_db
from storage.db_models import Camera, ExamRoom
from storage.repositories import SeatRepository

router = APIRouter(tags=["Seats"])


@router.get("/rooms/{room_id}/seats", response_model=List[SeatResponse])
def list_seats_for_room(room_id: str, enabled_only: bool = False, db: Session = Depends(get_db)):
    """Retrieve all configured seat ROIs for a room."""
    repo = SeatRepository(db)
    return repo.list_by_room(room_id, enabled_only=enabled_only)


@router.post("/rooms/{room_id}/seats", response_model=SeatResponse, status_code=status.HTTP_201_CREATED)
def create_seat_for_room(room_id: str, seat_data: SeatCreate, db: Session = Depends(get_db)):
    """Create a single seat ROI polygon for a room."""
    repo = SeatRepository(db)
    existing = repo.get_by_code(room_id, seat_data.seat_code)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Seat code '{seat_data.seat_code}' already exists in room '{room_id}'",
        )
    return repo.create(
        room_id=room_id,
        seat_code=seat_data.seat_code,
        polygon_json=seat_data.polygon_json,
        camera_id=seat_data.camera_id,
        seat_label=seat_data.seat_label,
        context_json=seat_data.context_json,
        enabled=seat_data.enabled,
    )


@router.post("/rooms/{room_id}/seats/bulk", response_model=List[SeatResponse])
def bulk_upsert_seats(room_id: str, request: SeatBulkUpsertRequest, db: Session = Depends(get_db)):
    """Bulk create or update seat ROI polygons for a room camera view."""
    repo = SeatRepository(db)
    seats_dicts = [s.model_dump() for s in request.seats]
    return repo.bulk_upsert_for_camera(
        room_id=room_id,
        camera_id=request.camera_id or "",
        seats_data=seats_dicts,
    )


@router.post("/rooms/{room_id}/seats/import-template", response_model=List[SeatResponse])
def import_seat_template(
    room_id: str,
    camera_id: Optional[str] = None,
    replace: bool = False,
    db: Session = Depends(get_db),
):
    """Import the bundled starting layout for this room into the database.

    The layout is only a starting point that people then adjust on the
    calibration page; runs always read the seats stored here.
    """
    from classroom_monitor.demo.config import DEMO_PRESETS
    from classroom_monitor.demo.seating import load_scene_template

    room = db.query(ExamRoom).filter(ExamRoom.id == room_id).first()
    if room is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")
    preset = next(
        (p for p in DEMO_PRESETS.values() if p.get("room_code") == room.room_code and p.get("seat_template_path")),
        None,
    )
    template_path = Path(preset["seat_template_path"]) if preset else None
    if template_path is None or not template_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No starting layout is bundled for room {room.room_code}",
        )
    repo = SeatRepository(db)
    if repo.list_by_room(room_id) and not replace:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This room already has Seat ROIs; confirm replacing them to import the starting layout",
        )
    if not camera_id:
        # Attach the seats to the room camera that shows the preset's video.
        video_name = Path(preset["video_path"]).name.lower()
        cameras = db.query(Camera).filter(Camera.room_id == room_id).all()
        matching = [c for c in cameras if Path(str(c.source_uri or "").replace("\\", "/")).name.lower() == video_name]
        chosen = matching[0] if matching else (cameras[0] if cameras else None)
        if chosen is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Room {room.room_code} has no camera to attach the seats to",
            )
        camera_id = chosen.id
    template = load_scene_template(template_path)
    if replace:
        for old in repo.list_by_room(room_id):
            db.delete(old)
        db.flush()
    return repo.bulk_upsert_for_camera(
        room_id=room_id,
        camera_id=camera_id or "",
        seats_data=template["seats"],
        replace_missing=False,
    )


@router.get("/seats/{seat_id}", response_model=SeatResponse)
def get_seat(seat_id: str, db: Session = Depends(get_db)):
    """Get single seat ROI definition by ID."""
    repo = SeatRepository(db)
    seat = repo.get_by_id(seat_id)
    if not seat:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Seat not found")
    return seat


@router.put("/seats/{seat_id}", response_model=SeatResponse)
def update_seat(seat_id: str, payload: SeatUpdate, db: Session = Depends(get_db)):
    """Update seat polygon coordinates, code, label, context_json, or enabled state."""
    repo = SeatRepository(db)
    updated = repo.update(
        seat_id=seat_id,
        seat_code=payload.seat_code,
        seat_label=payload.seat_label,
        polygon_json=payload.polygon_json,
        context_json=payload.context_json,
        enabled=payload.enabled,
    )
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Seat not found")
    return updated


@router.delete("/seats/{seat_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_seat(seat_id: str, db: Session = Depends(get_db)):
    """Delete a configured seat ROI."""
    repo = SeatRepository(db)
    success = repo.delete(seat_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Seat not found")
    return None
