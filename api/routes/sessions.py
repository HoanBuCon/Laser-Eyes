"""Exam Sessions API Endpoints."""

from __future__ import annotations

from typing import List
from fastapi import APIRouter, Depends, HTTPException, status

from api.dependencies import get_session_repo
from api.schemas import SessionCreate, SessionResponse
from storage.repositories import SessionRepository

router = APIRouter(prefix="/sessions", tags=["Exam Sessions"])


@router.post("/", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
def start_session(payload: SessionCreate, repo: SessionRepository = Depends(get_session_repo)):
    """Initialize and start a new proctored exam session."""
    return repo.create(
        room_id=payload.room_id,
        exam_name=payload.exam_name,
        camera_id=payload.camera_id,
    )


@router.get("/active", response_model=List[SessionResponse])
def list_active_sessions(repo: SessionRepository = Depends(get_session_repo)):
    """List all currently active proctoring sessions."""
    return repo.list_active()


@router.get("/{session_id}", response_model=SessionResponse)
def get_session(session_id: str, repo: SessionRepository = Depends(get_session_repo)):
    """Retrieve details and metrics for an exam session."""
    session = repo.get_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Exam session not found")
    return session


@router.post("/{session_id}/end", response_model=SessionResponse)
def end_session(session_id: str, repo: SessionRepository = Depends(get_session_repo)):
    """Conclude an active exam session and finalize analytics."""
    session = repo.end_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Exam session not found")
    return session
