"""Detection Events and Evidence API Endpoints.

Implements SRS v1.0 specifications:
- Event listing with filtering by room, session, seat, severity, review_status
- Event detail with evidence paths and SHA-256 hash
- Human-in-the-Loop review submission (CONFIRMED, REJECTED, INCONCLUSIVE)
- Evidence snapshot & MP4 video download endpoints
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from api.dependencies import get_evidence_store
from api.schemas import EventResponse, EventReviewUpdate, ReviewResponse
from storage.database import get_db
from storage.evidence_store import EvidenceStore
from storage.repositories import EventRepository
from storage.review_service import ReviewCommand, ReviewTargetMissing, submit_event_review

router = APIRouter(tags=["Detection Events"])


@router.get("/events", response_model=List[EventResponse])
def list_events(
    room_id: Optional[str] = Query(None),
    session_id: Optional[str] = Query(None),
    seat_id: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    review_status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Retrieve filtered list of detected proctoring events."""
    repo = EventRepository(db)
    events = repo.list_all_filtered(
        room_id=room_id,
        session_id=session_id,
        seat_id=seat_id,
        severity=severity,
        review_status=review_status,
        limit=limit,
        offset=offset,
    )
    resp = []
    for ev in events:
        item = EventResponse.model_validate(ev)
        if ev.evidence:
            item.evidence_url = f"/api/v1/events/{ev.id}/evidence"
        resp.append(item)
    return resp


@router.get("/events/{event_id}", response_model=EventResponse)
def get_event(event_id: str, db: Session = Depends(get_db)):
    """Get single event metadata with evidence details."""
    repo = EventRepository(db)
    ev = repo.get_by_id(event_id) or repo.get_by_event_id(event_id)
    if not ev:
        raise HTTPException(status_code=404, detail="Event not found")
    item = EventResponse.model_validate(ev)
    if ev.evidence:
        item.evidence_url = f"/api/v1/events/{ev.id}/evidence"
    return item


@router.post("/events/{event_id}/review", response_model=ReviewResponse)
def review_event(
    event_id: str,
    payload: EventReviewUpdate,
    db: Session = Depends(get_db),
):
    """Submit human proctor review decision (CONFIRMED / REJECTED / INCONCLUSIVE)."""
    try:
        return submit_event_review(
            db,
            ReviewCommand(
                event_id=event_id,
                reviewer_id=payload.reviewer_id,
                decision=payload.decision,
                reason_code=payload.reason_code,
                note=payload.note,
            ),
        )
    except ReviewTargetMissing as exc:
        raise HTTPException(status_code=404, detail="Event not found") from exc


@router.get("/events/{event_id}/evidence")
def get_evidence_image(
    event_id: str,
    db: Session = Depends(get_db),
):
    """Download or display the peak-confidence JPEG snapshot for a violation event."""
    repo = EventRepository(db)
    ev = repo.get_by_id(event_id) or repo.get_by_event_id(event_id)
    if not ev or not ev.evidence:
        raise HTTPException(status_code=404, detail="Evidence not found for event")

    snap_path = ev.evidence.snapshot_path or ev.evidence.file_path
    if not snap_path or not Path(snap_path).exists():
        raise HTTPException(status_code=404, detail="Evidence snapshot missing on disk")

    return FileResponse(str(Path(snap_path).resolve()), media_type="image/jpeg")


@router.get("/events/{event_id}/video")
def get_evidence_video(
    event_id: str,
    db: Session = Depends(get_db),
):
    """Stream or download the ~10s MP4 evidence clip for an event."""
    repo = EventRepository(db)
    ev = repo.get_by_id(event_id) or repo.get_by_event_id(event_id)
    if not ev or not ev.evidence or not ev.evidence.video_path:
        raise HTTPException(status_code=404, detail="Video clip not available for event")

    vid_path = Path(ev.evidence.video_path)
    if not vid_path.exists():
        raise HTTPException(status_code=404, detail="Video clip file missing on disk")

    return FileResponse(str(vid_path.resolve()), media_type="video/mp4")
