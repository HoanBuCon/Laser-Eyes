"""Human-proctor frame capture, bookmark, queue, review, and export APIs."""

from __future__ import annotations

import json
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.dependencies import get_db
from api.realtime import realtime_manager
from classroom_monitor.demo.runtime import DemoRuntime
from storage.db_models import DetectionEvent
from storage.proctor_service import (
    BookmarkConflict,
    BookmarkNotFound,
    bookmark_snapshot_path,
    bookmark_crop_bytes,
    bookmarks_to_csv,
    create_bookmark,
    get_bookmark,
    get_bookmark_by_request_id,
    list_bookmarks,
    review_bookmark,
    serialize_bookmark,
    verify_bookmark_integrity,
)

router = APIRouter(prefix="/proctor", tags=["Human Proctor Support"])


class BookmarkCreateRequest(BaseModel):
    capture_id: str
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()), min_length=8, max_length=64)
    subject_ref: Optional[str] = Field(None, max_length=100)
    note: str = Field("", max_length=4000)
    created_by: str = Field("Proctor_01", min_length=1, max_length=100)
    linked_ai_incident_id: Optional[str] = Field(None, max_length=64)
    roi_snapshot: Optional[dict[str, Any]] = None


class BookmarkReviewRequest(BaseModel):
    reviewer_id: str = Field("Proctor_01", min_length=1, max_length=100)
    decision: str
    reason_code: Optional[str] = Field(None, max_length=50)
    note: str = Field("", max_length=4000)


@router.post("/captures")
def capture_current_frame() -> dict[str, Any]:
    try:
        captured = DemoRuntime.get_instance().capture_latest_frame()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    result = captured.metadata()
    result["snapshot_url"] = f"/api/v1/proctor/captures/{captured.capture_id}/snapshot"
    return result


@router.get("/captures/{capture_id}/snapshot")
def get_capture_snapshot(capture_id: str) -> Response:
    captured = DemoRuntime.get_instance().get_captured_frame(capture_id)
    if captured is None:
        raise HTTPException(status_code=404, detail="Capture token is missing or expired")
    return Response(content=captured.jpeg_bytes, media_type="image/jpeg")


@router.post("/bookmarks")
def save_bookmark(
    payload: BookmarkCreateRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    runtime = DemoRuntime.get_instance()
    existing = get_bookmark_by_request_id(db, payload.request_id)
    if existing is not None:
        result = serialize_bookmark(existing, include_reviews=True)
        result["created"] = False
        return result
    captured = runtime.get_captured_frame(payload.capture_id)
    if captured is None:
        raise HTTPException(status_code=409, detail="Capture token is missing or expired; capture again")
    status = runtime.get_status()
    if captured.run_id != status.get("run_id") or captured.session_id != status.get("session_id"):
        raise HTTPException(status_code=409, detail="Capture belongs to a previous Classroom run")
    try:
        bookmark, created = create_bookmark(
            db,
            captured=captured,
            request_id=payload.request_id,
            subject_ref=payload.subject_ref,
            note=payload.note,
            created_by=payload.created_by,
            linked_ai_incident_id=payload.linked_ai_incident_id,
            roi_snapshot=payload.roi_snapshot,
            simulation=status.get("inference_mode") == "MOCK",
        )
    except BookmarkConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    result = serialize_bookmark(bookmark, include_reviews=True)
    result["created"] = created
    if created:
        realtime_manager.broadcast_threadsafe({"type": "PROCTOR_BOOKMARK", "bookmark": result})
    return result


@router.get("/bookmarks")
def get_bookmarks(
    session_id: Optional[str] = None,
    decision: Optional[str] = None,
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    return [
        serialize_bookmark(item)
        for item in list_bookmarks(db, session_id=session_id, decision=decision, limit=limit)
    ]


@router.get("/bookmarks/{bookmark_id}")
def get_bookmark_detail(bookmark_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return serialize_bookmark(get_bookmark(db, bookmark_id), include_reviews=True)
    except BookmarkNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/bookmarks/{bookmark_id}/snapshot")
def get_bookmark_snapshot(
    bookmark_id: str,
    view: str = Query("overall", pattern="^(overall|crop)$"),
    db: Session = Depends(get_db),
) -> Response:
    try:
        bookmark = get_bookmark(db, bookmark_id)
        if view == "crop":
            return Response(content=bookmark_crop_bytes(bookmark), media_type="image/jpeg")
        path = bookmark_snapshot_path(bookmark)
    except BookmarkNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BookmarkConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return FileResponse(path, media_type="image/jpeg", filename=f"bookmark-{bookmark.id}.jpg")


@router.get("/bookmarks/{bookmark_id}/integrity")
def get_bookmark_integrity(bookmark_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        bookmark = get_bookmark(db, bookmark_id)
    except BookmarkNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"bookmark_id": bookmark.id, **verify_bookmark_integrity(bookmark)}


@router.post("/bookmarks/{bookmark_id}/review")
def submit_bookmark_review(
    bookmark_id: str,
    payload: BookmarkReviewRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        bookmark = review_bookmark(
            db,
            bookmark_id=bookmark_id,
            reviewer_id=payload.reviewer_id,
            decision=payload.decision,
            reason_code=payload.reason_code,
            note=payload.note,
        )
    except BookmarkNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except BookmarkConflict as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    result = serialize_bookmark(bookmark, include_reviews=True)
    realtime_manager.broadcast_threadsafe({"type": "PROCTOR_BOOKMARK_REVIEW", "bookmark": result})
    return result


def _serialize_ai_event(event: DetectionEvent) -> dict[str, Any]:
    return {
        "queue_id": f"AI:{event.event_id}",
        "source_type": "AI_INCIDENT",
        "event_id": event.event_id,
        "session_id": event.session_id,
        "subject_ref": event.seat.seat_code if event.seat else None,
        "primary_signal": event.primary_signal or event.behavior,
        "severity": event.severity,
        "review_priority_score": event.risk_score,
        "review_status": event.review_status,
        "source_timestamp_ms": None,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


@router.get("/queue")
def get_combined_queue(
    session_id: Optional[str] = None,
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    if session_id is None:
        session_id = DemoRuntime.get_instance().get_status().get("session_id")
    ai_query = db.query(DetectionEvent)
    if session_id:
        ai_query = ai_query.filter(DetectionEvent.session_id == session_id)
    ai_items = [_serialize_ai_event(item) for item in ai_query.order_by(DetectionEvent.created_at.desc()).limit(500)]
    manual_items = [
        serialize_bookmark(item)
        for item in list_bookmarks(db, session_id=session_id, limit=500)
    ]
    combined = ai_items + manual_items
    return sorted(combined, key=lambda item: item.get("created_at") or item.get("captured_at") or "", reverse=True)


@router.get("/sessions/{session_id}/export")
def export_session_review(
    session_id: str,
    format: str = Query("json", pattern="^(json|csv)$"),
    db: Session = Depends(get_db),
) -> Response:
    bookmarks = list_bookmarks(db, session_id=session_id, limit=1000)
    if format == "csv":
        return Response(
            content=bookmarks_to_csv(bookmarks),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="proctor-bookmarks-{session_id}.csv"'},
        )
    events = (
        db.query(DetectionEvent)
        .filter(DetectionEvent.session_id == session_id)
        .order_by(DetectionEvent.created_at.asc())
        .all()
    )
    payload = {
        "schema_version": "vigil.proctor-support.v1",
        "session_id": session_id,
        "manual_bookmarks": [serialize_bookmark(item, include_reviews=True) for item in bookmarks],
        "ai_review_incidents": [_serialize_ai_event(item) for item in events],
    }
    return Response(
        content=json.dumps(payload, ensure_ascii=False, indent=2),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="proctor-review-{session_id}.json"'},
    )
