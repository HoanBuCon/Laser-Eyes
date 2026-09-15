"""Camera Streams API Endpoints."""

from __future__ import annotations

from typing import List
from fastapi import APIRouter, Depends, HTTPException, status

from api.dependencies import get_camera_repo
from api.schemas import CameraCreate, CameraResponse
from storage.repositories import CameraRepository

router = APIRouter(prefix="/cameras", tags=["Cameras"])


@router.post("/", response_model=CameraResponse, status_code=status.HTTP_201_CREATED)
def register_camera(payload: CameraCreate, repo: CameraRepository = Depends(get_camera_repo)):
    """Register a new camera feed attached to an exam room."""
    return repo.create(
        room_id=payload.room_id,
        name=payload.name,
        source_uri=payload.source_uri,
        position=payload.position,
    )


@router.get("/room/{room_id}", response_model=List[CameraResponse])
def list_room_cameras(room_id: str, repo: CameraRepository = Depends(get_camera_repo)):
    """List all camera feeds assigned to a classroom."""
    return repo.list_by_room(room_id)
