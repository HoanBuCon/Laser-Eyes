"""Camera Streams API Endpoints matching SRS v1.0."""

from __future__ import annotations

import os
import secrets
import time
from pathlib import Path
from typing import List, Optional

import cv2
import numpy as np
from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from fastapi.responses import FileResponse

from api.dependencies import get_camera_repo
from api.schemas import CameraCreate, CameraResponse, CameraUpdate
from storage.repositories import CameraRepository

router = APIRouter(tags=["Cameras"])

REF_FRAME_DIR = Path("data/reference_frames")
REF_FRAME_DIR.mkdir(parents=True, exist_ok=True)


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


@router.get("/cameras/{camera_id}/reference-frame")
def get_camera_reference_frame(camera_id: str, repo: CameraRepository = Depends(get_camera_repo)):
    """Capture and return a reference JPEG frame from camera source (RTSP, video file, or fallback demo video)."""
    cam = repo.get_by_id(camera_id)
    source_uri = cam.source_uri if cam else None

    # Fallback to demo video if source_uri is invalid, mock, or unreachable
    candidates = []
    if source_uri and not source_uri.startswith("sim://"):
        candidates.append(source_uri)
    candidates.extend([
        "demo_video/india_classroom.mp4",
        "demo_video/classroom_demo.mp4",
    ])

    frame = None
    for cand in candidates:
        if Path(cand).exists() or cand.startswith("rtsp://") or cand.startswith("http://"):
            cap = cv2.VideoCapture(cand)
            if cap.isOpened():
                # Grab a frame (seek slightly into video to avoid black intro)
                if Path(cand).exists():
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 10)
                ret, img = cap.read()
                cap.release()
                if ret and img is not None and img.size > 0:
                    frame = img
                    break

    if frame is None:
        # Generate clean synthetic reference test frame
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        cv2.putText(frame, f"Reference Frame for {camera_id}", (50, 100), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 255), 2)
        cv2.rectangle(frame, (100, 150), (1180, 650), (60, 60, 60), 2)

    ret, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ret:
        raise HTTPException(status_code=500, detail="Failed to encode reference frame")

    return Response(content=buf.tobytes(), media_type="image/jpeg")


@router.post("/cameras/reference-frame/upload")
async def upload_reference_frame(file: UploadFile = File(...)):
    """Upload a custom reference image or video file for Seat ROI calibration."""
    ext = Path(Path(file.filename or "").name).suffix.lower()
    allowed_extensions = {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".avi", ".mkv", ".mov"}
    if ext not in allowed_extensions:
        raise HTTPException(status_code=415, detail="Unsupported reference media extension")
    if file.content_type and not file.content_type.startswith(("image/", "video/", "application/octet-stream")):
        raise HTTPException(status_code=415, detail="Unsupported reference media MIME type")
    max_bytes = 100 * 1024 * 1024
    contents = await file.read(max_bytes + 1)
    if len(contents) > max_bytes:
        raise HTTPException(status_code=413, detail="Upload exceeds 100 MiB limit")
    save_name = f"ref_{int(time.time())}_{secrets.token_hex(8)}{ext}"
    save_path = REF_FRAME_DIR / save_name

    with open(save_path, "wb") as f:
        f.write(contents)

    # If it's a video, extract frame 0 and save as jpg
    if ext in [".mp4", ".avi", ".mkv", ".mov"]:
        cap = cv2.VideoCapture(str(save_path))
        if not cap.isOpened():
            raise HTTPException(status_code=400, detail="Invalid video file")
        ret, frame = cap.read()
        cap.release()
        if not ret or frame is None:
            raise HTTPException(status_code=400, detail="Could not extract frame from video")
        jpg_name = f"{save_name}.jpg"
        jpg_path = REF_FRAME_DIR / jpg_name
        cv2.imwrite(str(jpg_path), frame)
        h, w = frame.shape[:2]
        return {
            "status": "ok",
            "image_url": f"/api/v1/cameras/reference-frame/view/{jpg_name}",
            "filename": jpg_name,
            "width": w,
            "height": h,
        }
    else:
        # Image file
        img = cv2.imread(str(save_path))
        if img is None:
            raise HTTPException(status_code=400, detail="Invalid image file format")
        h, w = img.shape[:2]
        return {
            "status": "ok",
            "image_url": f"/api/v1/cameras/reference-frame/view/{save_name}",
            "filename": save_name,
            "width": w,
            "height": h,
        }


@router.get("/cameras/reference-frame/view/{filename}")
def view_reference_frame_file(filename: str):
    """Serve uploaded reference frame image."""
    if filename != Path(filename).name:
        raise HTTPException(status_code=400, detail="Invalid reference frame filename")
    root = REF_FRAME_DIR.resolve()
    fpath = (root / filename).resolve()
    if root not in fpath.parents or not fpath.is_file():
        raise HTTPException(status_code=404, detail="Reference frame file not found")
    return FileResponse(str(fpath), media_type="image/jpeg")
