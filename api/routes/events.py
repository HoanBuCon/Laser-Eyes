"""Detection Events and Evidence API Endpoints."""

from __future__ import annotations

from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse

from api.dependencies import get_event_repo, get_evidence_store
from api.schemas import EventResponse, EventReviewUpdate
from storage.evidence_store import EvidenceStore
from storage.repositories import EventRepository

router = APIRouter(prefix="/events", tags=["Detection Events"])


@router.get("/session/{session_id}", response_model=List[EventResponse])
def list_session_events(session_id: str, repo: EventRepository = Depends(get_event_repo)):
    """Retrieve all detected violation events for a specific exam session."""
    events = repo.list_by_session(session_id)
    resp = []
    for ev in events:
        item = EventResponse.model_validate(ev)
        if ev.evidence:
            item.evidence_url = f"/api/events/{ev.id}/evidence"
        resp.append(item)
    return resp


@router.get("/{event_id}", response_model=EventResponse)
def get_event(event_id: str, repo: EventRepository = Depends(get_event_repo)):
    """Get single event metadata."""
    ev = repo.get_by_id(event_id)
    if not ev:
        raise HTTPException(status_code=404, detail="Event not found")
    item = EventResponse.model_validate(ev)
    if ev.evidence:
        item.evidence_url = f"/api/events/{ev.id}/evidence"
    return item


@router.put("/{event_id}/review", response_model=EventResponse)
def review_event(
    event_id: str,
    payload: EventReviewUpdate,
    repo: EventRepository = Depends(get_event_repo),
):
    """Update human review status (confirmed / dismissed) with auditor notes."""
    ev = repo.update_review(event_id, payload.status, payload.reviewer_note)
    if not ev:
        raise HTTPException(status_code=404, detail="Event not found")
    item = EventResponse.model_validate(ev)
    if ev.evidence:
        item.evidence_url = f"/api/events/{ev.id}/evidence"
    return item


@router.get("/{event_id}/evidence")
def get_evidence_image(
    event_id: str,
    repo: EventRepository = Depends(get_event_repo),
    store: EvidenceStore = Depends(get_evidence_store),
):
    """Download or display the peak-confidence JPEG snapshot for a violation event."""
    ev = repo.get_by_id(event_id)
    if not ev or not ev.evidence:
        raise HTTPException(status_code=404, detail="Evidence not found for event")

    abs_path = store.get_absolute_path(ev.evidence.file_path)
    if not abs_path or not abs_path.exists():
        raise HTTPException(status_code=404, detail="Evidence file missing on disk")

    return FileResponse(str(abs_path), media_type="image/jpeg")
