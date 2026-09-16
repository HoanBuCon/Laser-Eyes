"""Inference Worker Node Endpoints for VIGIL AI REST API."""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from api.schemas import WorkerHeartbeatRequest, WorkerRegisterRequest, WorkerResponse
from storage.database import get_db
from storage.repositories import WorkerRepository

router = APIRouter(tags=["Workers"])


@router.post("/workers/register", response_model=WorkerResponse, status_code=status.HTTP_200_OK)
def register_worker(request: WorkerRegisterRequest, db: Session = Depends(get_db)):
    """Register or update an active inference worker node."""
    repo = WorkerRepository(db)
    return repo.register(
        worker_id=request.worker_id,
        hostname=request.hostname,
        gpu_name=request.gpu_name,
        gpu_memory_mb=request.gpu_memory_mb,
        max_active_streams=request.max_active_streams,
        version=request.version,
    )


@router.post("/workers/heartbeat", response_model=WorkerResponse)
def worker_heartbeat(request: WorkerHeartbeatRequest, db: Session = Depends(get_db)):
    """Receive periodic heartbeat ping from an inference worker node."""
    repo = WorkerRepository(db)
    worker = repo.heartbeat(
        worker_id=request.worker_id,
        active_camera_count=request.active_camera_count,
    )
    if not worker:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Worker '{request.worker_id}' not found. Please register first.",
        )
    return worker


@router.get("/workers", response_model=List[WorkerResponse])
def list_workers(db: Session = Depends(get_db)):
    """List all registered inference worker nodes and their status."""
    repo = WorkerRepository(db)
    return repo.list_all()
