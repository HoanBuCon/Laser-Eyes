"""Camera Streams API Endpoints matching SRS v1.0."""

from __future__ import annotations

from typing import List
from fastapi import APIRouter, Depends, HTTPException, status

from api.dependencies import get_camera_repo
from api.schemas import CameraCreate, CameraResponse, CameraUpdate
from storage.repositories import CameraRepository

router = APIRouter(tags=["Cameras"])


@router.post("/cameras", response_model=CameraResponse, status_code=status.HTTP_201_CREATED)
def register_camera(payload: CameraCreate, repo: CameraRepository = Depends(get_camera_repo)):
    """Register a new camera feed attached to an exam room."""
    return repo.create(
        room_id=payload.room_id,
        name=payload.name,
        source_uri=payload.source_uri,
        rtsp_url_protected=payload.rtsp_url_protected,
        position=payload.position,
        resolution=payload.resolution,
        capture_fps=payload.capture_fps,
        inference_fps=payload.inference_fps,
        worker_id=payload.worker_id,
        enabled=payload.enabled,
    )


@router.get("/cameras", response_model=List[CameraResponse])
def list_cameras(repo: CameraRepository = Depends(get_camera_repo)):
    """List all registered cameras."""
    return repo.list_all()


@router.get("/cameras/{camera_id}", response_model=CameraResponse)
def get_camera(camera_id: str, repo: CameraRepository = Depends(get_camera_repo)):
    """Get single camera metadata."""
    cam = repo.get_by_id(camera_id)
    if not cam:
        raise HTTPException(status_code=404, detail="Camera not found")
    return cam


@router.get("/rooms/{room_id}/cameras", response_model=List[CameraResponse])
def list_room_cameras(room_id: str, repo: CameraRepository = Depends(get_camera_repo)):
    """List all camera feeds assigned to a classroom."""
    return repo.list_by_room(room_id)
