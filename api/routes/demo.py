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
import secrets
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
from classroom_monitor.async_evidence_writer import compute_file_sha256
from classroom_monitor.contracts import HashStatus
from storage.db_models import DetectionEvent
from storage.review_service import ReviewCommand, ReviewTargetMissing, submit_event_review

logger = logging.getLogger("DemoRouter")
router = APIRouter(prefix="/demo", tags=["Competition Demo Engine"])


class DemoStartRequest(BaseModel):
    preset: str = Field("india", description="Demo preset name: 'india' or 'student'")
    mode: str = Field("LIVE", description="Execution mode: 'LIVE' or 'REPLAY'")
    debug_overlay: bool = Field(False, description="Render advanced developer overlay")
    show_window: bool = Field(False, description="Open local OpenCV GUI window")
    max_frames: Optional[int] = Field(None, description="Max frames to process (for quick validation)")
    stride: int = Field(1, description="Frame subsampling stride")
    allow_mock: bool = Field(False, description="Explicitly allow visibly-labelled synthetic pose inference")


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
    try:
        res = runtime.start(
            preset=preset_clean,
            mode=mode_clean,
            debug_overlay=payload.debug_overlay,
            show_window=payload.show_window,
            max_frames=payload.max_frames,
            stride=payload.stride,
            allow_mock=payload.allow_mock,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

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


@router.get("/events/{event_id}/integrity")
def verify_demo_event_integrity(
    event_id: str,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Compare a stored SHA-256 integrity digest with the current evidence file."""
    db_event = db.query(DetectionEvent).filter(DetectionEvent.event_id == event_id).first()
    evidence = db_event.evidence if db_event else None
    if evidence is None or not evidence.video_sha256:
        return {"event_id": event_id, "hash_status": HashStatus.NOT_AVAILABLE.value, "sha256": None}
    path = Path(evidence.video_path or evidence.file_path)
    if not path.is_file():
        return {
            "event_id": event_id,
            "hash_status": HashStatus.MISMATCH.value,
            "sha256": evidence.video_sha256,
            "error": "Evidence file is missing",
        }
    actual = compute_file_sha256(path)
    hash_status = (
        HashStatus.VERIFIED.value
        if secrets.compare_digest(actual.lower(), evidence.video_sha256.lower())
        else HashStatus.MISMATCH.value
    )
    runtime_event = DemoRuntime.get_instance().get_event(event_id)
    if runtime_event is not None:
        runtime_event["hash_status"] = hash_status
    return {
        "event_id": event_id,
        "hash_status": hash_status,
        "sha256": evidence.video_sha256,
    }


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

    try:
        submit_event_review(
            db,
            ReviewCommand(
                event_id=event_id,
                reviewer_id=payload.reviewer_id or "Proctor",
                decision=decision_clean,
                reason_code=payload.reason_code,
                note=payload.notes or "",
            ),
        )
    except ReviewTargetMissing as exc:
        raise HTTPException(
            status_code=409,
            detail="Review incident is not durable; decision was not accepted",
        ) from exc

    runtime = DemoRuntime.get_instance()
    ev_dict = runtime.get_event(event_id)
    if ev_dict:
        ev_dict["review_status"] = decision_clean
        ev_dict["review_decision"] = decision_clean
        ev_dict["decision_reason"] = payload.reason_code
        ev_dict["reviewer_notes"] = payload.notes
        ev_dict["reviewer_id"] = payload.reviewer_id

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
    """Locate one unambiguous, contained evidence file by safe basename."""
    if filename != Path(filename).name or filename in {"", ".", ".."}:
        return None
    candidate_roots = [
        Path("data/demo_runs"),
        Path("data/demo_final"),
    ]
    matches: list[Path] = []
    for root in candidate_roots:
        if root.exists():
            resolved_root = root.resolve()
            for match in root.rglob(filename):
                resolved_match = match.resolve()
                if resolved_match.is_file() and resolved_root in resolved_match.parents:
                    matches.append(resolved_match)
    unique = list(dict.fromkeys(matches))
    return unique[0] if len(unique) == 1 else None


@router.get("/evidence/snapshot/{filename}")
def get_evidence_snapshot(filename: str):
    """Serve candidate peak frame snapshot image."""
    if Path(filename).suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise HTTPException(status_code=415, detail="Unsupported snapshot type")
    fpath = _find_evidence_file(filename)
    if not fpath or not fpath.exists():
        raise HTTPException(status_code=404, detail=f"Snapshot '{filename}' not found")
    return FileResponse(str(fpath), media_type="image/jpeg")


@router.get("/evidence/video/{filename}")
def get_evidence_video(filename: str):
    """Serve 10-second candidate evidence MP4 video clip."""
    if Path(filename).suffix.lower() != ".mp4":
        raise HTTPException(status_code=415, detail="Unsupported evidence video type")
    fpath = _find_evidence_file(filename)
    if not fpath or not fpath.exists():
        raise HTTPException(status_code=404, detail=f"Evidence video '{filename}' not found")
    return FileResponse(str(fpath), media_type="video/mp4")
