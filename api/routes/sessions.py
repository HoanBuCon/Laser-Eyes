"""Exam Sessions API Endpoints matching SRS v1.0."""

from __future__ import annotations

from typing import List
from fastapi import APIRouter, Depends, HTTPException, status

from api.dependencies import get_session_repo
from api.schemas import SessionCreate, SessionResponse
from storage.repositories import AuditLogRepository, SessionRepository
from storage.database import get_db

router = APIRouter(tags=["Exam Sessions"])


@router.post("/sessions", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
def create_session(payload: SessionCreate, repo: SessionRepository = Depends(get_session_repo)):
    """Initialize a new exam session."""
    return repo.create(
        room_id=payload.room_id,
        exam_name=payload.exam_name,
        subject_code=payload.subject_code,
        camera_id=payload.camera_id,
        start_time=payload.start_time,
        end_time=payload.end_time,
        status="READY",
    )


@router.post("/sessions/{session_id}/start", response_model=SessionResponse)
def start_session(session_id: str, repo: SessionRepository = Depends(get_session_repo), db = Depends(get_db)):
    """Start exam session and begin real-time proctoring."""
    session = repo.start_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Exam session not found")
    AuditLogRepository(db).log_action(
        actor_id="admin",
        action="START_SESSION",
        resource_type="SESSION",
        resource_id=session_id,
        metadata={"exam_name": session.exam_name, "room_id": session.room_id},
    )
    return session


@router.post("/sessions/{session_id}/stop", response_model=SessionResponse)
def stop_session(session_id: str, repo: SessionRepository = Depends(get_session_repo), db = Depends(get_db)):
    """Conclude an active exam session and finalize analytics."""
    session = repo.stop_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Exam session not found")
    AuditLogRepository(db).log_action(
        actor_id="admin",
        action="STOP_SESSION",
        resource_type="SESSION",
        resource_id=session_id,
        metadata={"exam_name": session.exam_name, "room_id": session.room_id},
    )
    return session


@router.get("/sessions/{session_id}", response_model=SessionResponse)
def get_session(session_id: str, repo: SessionRepository = Depends(get_session_repo)):
    """Retrieve details and metrics for an exam session."""
    session = repo.get_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Exam session not found")
    return session


@router.get("/rooms/{room_id}/sessions", response_model=List[SessionResponse])
def list_room_sessions(room_id: str, repo: SessionRepository = Depends(get_session_repo)):
    """List all exam sessions associated with a room."""
    return repo.list_by_room(room_id)
