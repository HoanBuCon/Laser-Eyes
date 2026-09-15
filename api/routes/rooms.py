"""Exam Rooms API Endpoints."""

from __future__ import annotations

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.dependencies import get_room_repo
from api.schemas import RoomCreate, RoomResponse
from storage.repositories import RoomRepository

router = APIRouter(prefix="/rooms", tags=["Exam Rooms"])


@router.post("/", response_model=RoomResponse, status_code=status.HTTP_201_CREATED)
def create_room(payload: RoomCreate, repo: RoomRepository = Depends(get_room_repo)):
    """Create a new classroom for exam surveillance."""
    return repo.create(
        site_id=payload.site_id,
        name=payload.name,
        capacity=payload.capacity,
        description=payload.description,
    )


@router.get("/", response_model=List[RoomResponse])
def list_rooms(
    site_id: Optional[str] = Query(None, description="Filter rooms by site ID"),
    repo: RoomRepository = Depends(get_room_repo),
):
    """List classrooms, optionally filtered by site."""
    if site_id:
        return repo.list_by_site(site_id)
    return repo.list_all()


@router.get("/{room_id}", response_model=RoomResponse)
def get_room(room_id: str, repo: RoomRepository = Depends(get_room_repo)):
    """Get details for a specific classroom."""
    room = repo.get_by_id(room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Exam room not found")
    return room
