"""FastAPI Router for VIGIL AI SRS v2 Competition Demo System.

Endpoints for:
- Preset Discovery (/presets)
- Demo Execution Control (/start, /stop, /pause, /resume, /reset)
- Realtime Telemetry (/status)
- Review Queue & Incidents (/events, /events/{event_id})
- Human Review Actions (/events/{event_id}/review)
- MJPEG Live Video Streaming (/stream)
- Evidence Media Delivery (/evidence/snapshot/{filename}, /evidence/video/{filename})
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.dependencies import get_db
from api.realtime import realtime_manager
from classroom_monitor.demo.config import DEMO_PRESETS, get_demo_config
from classroom_monitor.demo.runtime import DemoMode, DemoRuntime, DemoState
from storage.db_models import DetectionEvent, EventReview
from storage.repositories import EventRepository, ReviewRepository

logger = logging.getLogger("DemoRouter")
router = APIRouter(prefix="/demo", tags=["Competition Demo Engine"])


class DemoStartRequest(BaseModel):
    preset: str = Field("india", description="Demo preset name: 'india' or 'student'")
    mode: str = Field("LIVE", description="Execution mode: 'LIVE' or 'REPLAY'")
    debug_overlay: bool = Field(False, description="Render advanced developer overlay")
    show_window: bool = Field(False, description="Open local OpenCV GUI window")
    max_frames: Optional[int] = Field(None, description="Max frames to process (for quick validation)")
    stride: int = Field(1, description="Frame subsampling stride")


class HumanReviewRequest(BaseModel):
    decision: str = Field(..., description="'CONFIRMED', 'REJECTED', or 'INCONCLUSIVE'")
    reason_code: Optional[str] = Field("NONE", description="Standardized reason code")
    notes: Optional[str] = Field("", description="Proctor notes or rationale")
    reviewer_id: Optional[str] = Field("Proctor_01", description="Identifier of human reviewer")


@router.get("/presets")
def list_presets() -> List[Dict[str, Any]]:
    """List all available competition demonstration presets and their metadata."""
    presets = []
    for name, p_cfg in DEMO_PRESETS.items():
        # Check if precomputed replay artifacts exist
        replay_p = Path(f"data/demo_final/{name}")
        has_replay = (replay_p / "result.mp4").exists() and (replay_p / "events.json").exists()

        video_p = p_cfg.get("video_path", "")
        room_c = p_cfg.get("room_code", "ROOM-01")
        cam_id = p_cfg.get("camera_id", "CAM-01")
        imgsz = p_cfg.get("pose_imgsz", 1280)
        seats = p_cfg.get("seats_preset", [])

        presets.append({
            "name": name,
            "title": f"{name.capitalize()} Classroom",
            "video_path": str(video_p),
            "room_code": room_c,
            "camera_id": cam_id,
            "resolution": f"{imgsz}x{imgsz}" if imgsz else "HD",
            "seat_count": len(seats) if seats else 21,
            "has_replay_artifacts": has_replay,
        })
    return presets


@router.post("/start")
def start_demo(payload: DemoStartRequest) -> Dict[str, Any]:
    """Start video analysis in LIVE AI ANALYSIS or RECORDED ANALYSIS REPLAY mode."""
    preset_clean = payload.preset.lower().strip()
    if preset_clean not in DEMO_PRESETS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown preset '{payload.preset}'. Available: {list(DEMO_PRESETS.keys())}",
        )

    mode_clean = payload.mode.upper().strip()
    if mode_clean not in (DemoMode.LIVE.value, DemoMode.REPLAY.value):
        raise HTTPException(
            status_code=400,
            detail=f"Unknown mode '{payload.mode}'. Must be 'LIVE' or 'REPLAY'",
        )

    runtime = DemoRuntime.get_instance()
    res = runtime.start(
        preset=preset_clean,
        mode=mode_clean,
        debug_overlay=payload.debug_overlay,
        show_window=payload.show_window,
        max_frames=payload.max_frames,
        stride=payload.stride,
    )

    # Broadcast status to WebSockets
    realtime_manager.broadcast_threadsafe({
        "type": "DEMO_STARTED",
        "status": res,
    })

    return res


@router.post("/pause")
def pause_demo() -> Dict[str, Any]:
    """Pause current demo processing."""
    runtime = DemoRuntime.get_instance()
    res = runtime.pause()
    realtime_manager.broadcast_threadsafe({"type": "DEMO_STATUS", "status": res})
    return res


@router.post("/resume")
def resume_demo() -> Dict[str, Any]:
    """Resume paused demo processing."""
    runtime = DemoRuntime.get_instance()
    res = runtime.resume()
    realtime_manager.broadcast_threadsafe({"type": "DEMO_STATUS", "status": res})
    return res


@router.post("/stop")
def stop_demo() -> Dict[str, Any]:
    """Stop active demo processing."""
    runtime = DemoRuntime.get_instance()
    res = runtime.stop()
    realtime_manager.broadcast_threadsafe({"type": "DEMO_STATUS", "status": res})
    return res


@router.post("/reset")
def reset_demo() -> Dict[str, Any]:
    """Reset demo runtime state and clear active review queue."""
    runtime = DemoRuntime.get_instance()
    res = runtime.reset()
    realtime_manager.broadcast_threadsafe({"type": "DEMO_STATUS", "status": res})
    return res


@router.get("/status")
def get_demo_status() -> Dict[str, Any]:
    """Retrieve realtime processing telemetry, speed, FPS, and incident metrics."""
    return DemoRuntime.get_instance().get_status()


@router.get("/events")
def get_demo_events() -> List[Dict[str, Any]]:
    """Retrieve all review incidents emitted during the current demo session."""
    return DemoRuntime.get_instance().get_events()


@router.get("/events/{event_id}")
def get_demo_event_details(event_id: str) -> Dict[str, Any]:
    """Retrieve detailed metadata and evidence for a specific incident."""
    ev = DemoRuntime.get_instance().get_event(event_id)
    if not ev:
        raise HTTPException(status_code=404, detail=f"Review incident '{event_id}' not found")
    return ev


@router.post("/events/{event_id}/review")
def review_demo_event(
    event_id: str,
    payload: HumanReviewRequest,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Record human proctor decision (CONFIRMED / REJECTED / INCONCLUSIVE) on an incident."""
    decision_clean = payload.decision.upper().strip()
    if decision_clean not in ("CONFIRMED", "REJECTED", "INCONCLUSIVE"):
        raise HTTPException(status_code=400, detail="Decision must be 'CONFIRMED', 'REJECTED', or 'INCONCLUSIVE'")

    runtime = DemoRuntime.get_instance()
    ev_dict = runtime.get_event(event_id)
    if ev_dict:
        ev_dict["review_status"] = decision_clean
        ev_dict["decision_reason"] = payload.reason_code
        ev_dict["reviewer_notes"] = payload.notes
        ev_dict["reviewer_id"] = payload.reviewer_id

    # Persist decision in SQLite
    db_event = db.query(DetectionEvent).filter(DetectionEvent.event_id == event_id).first()
    if db_event:
        db_event.review_status = decision_clean
        db_event.status = decision_clean
        db_event.reviewer_note = f"[{payload.reason_code}] {payload.notes}".strip()

        # Update or create EventReview
        rev = db.query(EventReview).filter(EventReview.event_id == db_event.id).first()
        if rev:
            rev.decision = decision_clean
            rev.reason_code = payload.reason_code
            rev.note = payload.notes or ""
            rev.reviewer_id = payload.reviewer_id or "Proctor"
        else:
            rev = EventReview(
                event_id=db_event.id,
                reviewer_id=payload.reviewer_id or "Proctor",
                decision=decision_clean,
                reason_code=payload.reason_code,
                note=payload.notes or "",
            )
            db.add(rev)
        db.commit()

    # Broadcast decision to all connected clients
    realtime_manager.broadcast_threadsafe({
        "type": "REVIEW_DECISION",
        "event_id": event_id,
        "decision": decision_clean,
        "reason_code": payload.reason_code,
        "notes": payload.notes,
    })

    return {
        "event_id": event_id,
        "decision": decision_clean,
        "reason_code": payload.reason_code,
        "notes": payload.notes,
        "status": "SUCCESS",
    }


# -----------------------------------------------------------------------------
# Video Streaming Endpoint (MJPEG)
# -----------------------------------------------------------------------------

@router.get("/stream")
async def mjpeg_stream_endpoint():
    """High-performance multipart MJPEG stream from the SRS v2 renderer."""
    runtime = DemoRuntime.get_instance()

    async def frame_generator():
        try:
            while True:
                jpeg = runtime.get_latest_jpeg()
                if jpeg is not None:
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
                    )
                await asyncio.sleep(0.033)  # ~30 FPS polling for new frame
        except (asyncio.CancelledError, GeneratorExit, Exception):
            return

    return StreamingResponse(
        frame_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


# -----------------------------------------------------------------------------
# Evidence Media File Endpoints
# -----------------------------------------------------------------------------

def _find_evidence_file(filename: str, subfolder: str = "evidence") -> Optional[Path]:
    """Locate evidence snapshot or video across candidate run and preset directories."""
    candidate_roots = [
        Path("data/demo_runs"),
        Path("data/demo_final"),
        Path("data"),
    ]
    for root in candidate_roots:
        if root.exists():
            for match in root.rglob(filename):
                if match.is_file():
                    return match
    return None


@router.get("/evidence/snapshot/{filename}")
def get_evidence_snapshot(filename: str):
    """Serve candidate peak frame snapshot image."""
    fpath = _find_evidence_file(filename)
    if not fpath or not fpath.exists():
        raise HTTPException(status_code=404, detail=f"Snapshot '{filename}' not found")
    return FileResponse(str(fpath), media_type="image/jpeg")


@router.get("/evidence/video/{filename}")
def get_evidence_video(filename: str):
    """Serve 10-second candidate evidence MP4 video clip."""
    fpath = _find_evidence_file(filename)
    if not fpath or not fpath.exists():
        raise HTTPException(status_code=404, detail=f"Evidence video '{filename}' not found")
    return FileResponse(str(fpath), media_type="video/mp4")
