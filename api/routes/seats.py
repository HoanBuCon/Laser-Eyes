"""Seat ROI Management Endpoints for VIGIL AI REST API."""

from __future__ import annotations

import json
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from api.schemas import SeatBulkUpsertRequest, SeatCreate, SeatResponse
from storage.database import get_db
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


@router.delete("/seats/{seat_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_seat(seat_id: str, db: Session = Depends(get_db)):
    """Delete a configured seat ROI."""
    repo = SeatRepository(db)
    success = repo.delete(seat_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Seat not found")
    return None
