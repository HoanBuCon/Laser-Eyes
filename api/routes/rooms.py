"""Exam Rooms API Endpoints matching SRS v1.0."""

from __future__ import annotations

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.dependencies import get_room_repo
from api.schemas import RoomCreate, RoomResponse, RoomUpdate
from storage.repositories import RoomRepository

router = APIRouter(tags=["Exam Rooms"])


@router.post("/rooms", response_model=RoomResponse, status_code=status.HTTP_201_CREATED)
def create_room(payload: RoomCreate, repo: RoomRepository = Depends(get_room_repo)):
    """Create a new classroom for exam surveillance."""
    return repo.create(
        name=payload.name,
        site_id=payload.site_id,
        room_code=payload.room_code or payload.name,
        building=payload.building,
        floor=payload.floor,
        capacity=payload.capacity,
        description=payload.description,
        status=payload.status,
    )


@router.get("/rooms", response_model=List[RoomResponse])
def list_rooms(
    site_id: Optional[str] = Query(None, description="Filter rooms by site ID"),
    repo: RoomRepository = Depends(get_room_repo),
):
    """List classrooms, optionally filtered by site."""
    if site_id:
        return repo.list_by_site(site_id)
    return repo.list_all()


@router.get("/rooms/{room_id}", response_model=RoomResponse)
def get_room(room_id: str, repo: RoomRepository = Depends(get_room_repo)):
    """Get details for a specific classroom."""
    room = repo.get_by_id(room_id) or repo.get_by_code(room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Exam room not found")
    return room


@router.patch("/rooms/{room_id}", response_model=RoomResponse)
def update_room(room_id: str, payload: RoomUpdate, repo: RoomRepository = Depends(get_room_repo)):
    """Update room configuration."""
    room = repo.update(room_id, **payload.model_dump(exclude_unset=True))
    if not room:
        raise HTTPException(status_code=404, detail="Exam room not found")
    return room
