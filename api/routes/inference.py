"""[LEGACY / DEPRECATED] Inference Control and Video Stream API Endpoints.

NOTE: This router runs the legacy VideoProcessor. The canonical SRS v2 competition demo
pipeline is available under `/api/v1/demo/*` powered by `classroom_monitor.demo.runtime.DemoRuntime`.
"""

from __future__ import annotations

import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from api.dependencies import get_db, get_event_repo, get_evidence_store, get_session_repo
from api.schemas import InferenceStartRequest, InferenceStatusResponse
from classroom_monitor.config import ClassroomConfig
from classroom_monitor.models import ClassroomEvent
from classroom_monitor.video_processor import VideoProcessor
from storage.database import SessionLocal
from storage.evidence_store import EvidenceStore
from storage.repositories import EventRepository, SessionRepository

logger = logging.getLogger("InferenceRouter")
def require_legacy_inference_enabled() -> None:
    if os.getenv("VIGIL_ENABLE_LEGACY_INFERENCE", "0") != "1":
        raise HTTPException(
            status_code=410,
            detail="Legacy inference is disabled. Use the canonical /api/v1/demo SRS v2 runtime.",
        )


router = APIRouter(
    prefix="/inference",
    tags=["Inference Engine (Legacy)"],
    deprecated=True,
    dependencies=[Depends(require_legacy_inference_enabled)],
)

# Active background runners tracking dict
_active_runners: Dict[str, Dict[str, Any]] = {}


def _run_background_inference(
    session_id: str,
    source_uri: str,
    max_frames: Optional[int] = None,
) -> None:
    """Worker function executed inside background thread for real-time video processing."""
    db: Session = SessionLocal()
    session_repo = SessionRepository(db)
    event_repo = EventRepository(db)
    evidence_store = EvidenceStore()

    session = session_repo.get_by_id(session_id)
    if not session:
        logger.error("Background runner could not locate session: %s", session_id)
        db.close()
        return

    room_id = session.room_id
    site_id = session.room.site_id if session.room else "default_site"

    config = ClassroomConfig()
    runner_state = {
        "is_running": True,
        "frames_processed": 0,
        "current_fps": 0.0,
        "events_detected": 0,
        "last_error": None,
        "stop_requested": False,
    }
    _active_runners[session_id] = runner_state

    def on_event_callback(event: ClassroomEvent) -> None:
        """Callback invoked when EventEngine detects a suspicious episode."""
        evidence_rel_path = None
        file_size = 0

        if event.evidence_frame is not None:
            evidence_rel_path, file_size = evidence_store.save_evidence(
                event_id=event.event_id,
                frame=event.evidence_frame,
                site_id=site_id,
                room_id=room_id,
                session_id=session_id,
            )

        event_repo.record_event(
            session_id=session_id,
            event=event,
            evidence_rel_path=evidence_rel_path,
            file_size=file_size,
        )
        session_repo.recalculate_risk_score(session_id)
        runner_state["events_detected"] += 1

    try:
        processor = VideoProcessor(config=config, on_event=on_event_callback)
        logger.info("Starting inference pipeline on session %s (source: %s)...", session_id, source_uri)

        res = processor.process_video(
            video_path=source_uri,
            max_frames=max_frames,
            save_evidence=False,  # Saved inside on_event_callback
        )
        session_repo.update_metrics(
            session_id=session_id,
            total_frames=res["total_frames_processed"],
            avg_fps=res["average_fps"],
        )
        session_repo.end_session(session_id)

    except Exception as exc:
        logger.error("Inference runner encountered an error: %s", exc, exc_info=True)
        runner_state["last_error"] = str(exc)
    finally:
        runner_state["is_running"] = False
        db.close()


@router.post("/start", response_model=InferenceStatusResponse)
def start_inference_stream(
    payload: InferenceStartRequest,
    background_tasks: BackgroundTasks,
    session_repo: SessionRepository = Depends(get_session_repo),
):
    """Trigger background AI inference processing for a session."""
    session = session_repo.get_by_id(payload.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Exam session not found")

    if payload.session_id in _active_runners and _active_runners[payload.session_id]["is_running"]:
        return InferenceStatusResponse(
            session_id=payload.session_id,
            is_running=True,
            frames_processed=_active_runners[payload.session_id]["frames_processed"],
            current_fps=_active_runners[payload.session_id]["current_fps"],
            events_detected=_active_runners[payload.session_id]["events_detected"],
        )

    # Determine source (camera or video file)
    source_uri = payload.video_source
    if not source_uri and session.camera:
        source_uri = session.camera.source_uri

    if not source_uri:
        # Fallback to test video or mock
        source_uri = "assets/demo_classroom.mp4"

    thread = threading.Thread(
        target=_run_background_inference,
        args=(payload.session_id, source_uri, payload.max_frames),
        daemon=True,
    )
    thread.start()

    return InferenceStatusResponse(
        session_id=payload.session_id,
        is_running=True,
        frames_processed=0,
        current_fps=0.0,
        events_detected=0,
    )


@router.get("/status/{session_id}", response_model=InferenceStatusResponse)
def get_inference_status(session_id: str):
    """Check live inference telemetry and event counts."""
    if session_id not in _active_runners:
        return InferenceStatusResponse(
            session_id=session_id,
            is_running=False,
            frames_processed=0,
            current_fps=0.0,
            events_detected=0,
        )

    state = _active_runners[session_id]
    return InferenceStatusResponse(
        session_id=session_id,
        is_running=state["is_running"],
        frames_processed=state["frames_processed"],
        current_fps=state["current_fps"],
        events_detected=state["events_detected"],
        last_error=state.get("last_error"),
    )


@router.post("/upload")
async def upload_exam_video(
    file: UploadFile = File(...),
):
    """Upload recorded exam video for batch surveillance processing."""
    upload_dir = Path("data/uploads")
    upload_dir.mkdir(parents=True, exist_ok=True)

    original_name = Path(file.filename or "upload.mp4").name
    extension = Path(original_name).suffix.lower()
    allowed_extensions = {".mp4", ".avi", ".mkv", ".mov"}
    if extension not in allowed_extensions:
        raise HTTPException(status_code=415, detail="Unsupported video extension")
    if file.content_type and not file.content_type.startswith(("video/", "application/octet-stream")):
        raise HTTPException(status_code=415, detail="Unsupported video MIME type")
    max_bytes = 500 * 1024 * 1024
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail="Upload exceeds 500 MiB limit")
    dest_file = upload_dir / f"{int(time.time())}_{secrets.token_hex(8)}{extension}"
    with open(dest_file, "wb") as f:
        f.write(content)

    return {
        "filename": original_name,
        "saved_path": str(dest_file).replace("\\", "/"),
        "size_bytes": len(content),
    }
