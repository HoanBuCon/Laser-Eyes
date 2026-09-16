"""Pydantic Request and Response Schemas for VIGIL AI REST API.

Full compliance with SRS v1.0 specifications for:
- Rooms, Cameras, and Seat ROIs
- Exam Sessions and Lifecycle
- Suspicious Events and Evidence Packages
- Human-in-the-Loop Reviews
- Distributed Worker Nodes & Heartbeats
- Audit Logs and System Health
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


# ---- Exam Site Schemas ----
class SiteCreate(BaseModel):
    name: str = Field(..., example="Đại học Bách Khoa TP.HCM")
    address: Optional[str] = Field(None, example="268 Lý Thường Kiệt, Q.10, TP.HCM")
    contact_info: Optional[str] = Field(None, example="admin@proctoring.edu.vn")


class SiteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    address: Optional[str]
    contact_info: Optional[str]
    is_active: bool
    created_at: datetime.datetime


# ---- Exam Room Schemas ----
class RoomCreate(BaseModel):
    name: str = Field(..., example="Phòng A1-302")
    room_code: Optional[str] = Field(None, example="A101")
    site_id: Optional[str] = None
    building: Optional[str] = Field(None, example="Tòa nhà A1")
    floor: Optional[str] = Field(None, example="Tầng 3")
    capacity: int = Field(30, ge=1, le=500)
    description: Optional[str] = Field(None, example="Phòng thi lý thuyết 30 chỗ")
    status: str = Field("active", example="active")


class RoomUpdate(BaseModel):
    name: Optional[str] = None
    room_code: Optional[str] = None
    building: Optional[str] = None
    floor: Optional[str] = None
    capacity: Optional[int] = None
    description: Optional[str] = None
    status: Optional[str] = None


class RoomResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    room_code: Optional[str]
    name: str
    building: Optional[str]
    floor: Optional[str]
    capacity: int
    description: Optional[str]
    status: str
    is_active: bool
    created_at: datetime.datetime


# ---- Camera Schemas ----
class CameraCreate(BaseModel):
    room_id: str
    name: str = Field(..., example="Camera góc rộng bảng trước")
    source_uri: str = Field("0", example="rtsp://admin:pass@192.168.1.100:554/stream1")
    rtsp_url_protected: Optional[str] = None
    position: str = Field("front_center", example="front_center")
    resolution: str = Field("1280x720", example="1280x720")
    capture_fps: float = Field(30.0, ge=1.0, le=120.0)
    inference_fps: float = Field(5.0, ge=1.0, le=30.0)
    worker_id: Optional[str] = None
    enabled: bool = True


class CameraUpdate(BaseModel):
    name: Optional[str] = None
    source_uri: Optional[str] = None
    position: Optional[str] = None
    resolution: Optional[str] = None
    capture_fps: Optional[float] = None
    inference_fps: Optional[float] = None
    worker_id: Optional[str] = None
    enabled: Optional[bool] = None
    status: Optional[str] = None


class CameraResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    room_id: str
    name: str
    source_uri: str
    position: str
    resolution: str
    capture_fps: float
    inference_fps: float
    worker_id: Optional[str]
    enabled: bool
    status: str
    last_seen_at: datetime.datetime


# ---- Seat ROI Schemas ----
class SeatCreate(BaseModel):
    room_id: str
    seat_code: str = Field(..., example="A101_S01")
    seat_label: Optional[str] = Field(None, example="Dãy 1 Bàn 1")
    polygon_json: List[List[float]] = Field(..., example=[[100, 100], [200, 100], [200, 200], [100, 200]])
    camera_id: Optional[str] = None
    enabled: bool = True


class SeatBulkUpsertRequest(BaseModel):
    room_id: str
    camera_id: Optional[str] = None
    seats: List[SeatCreate]


class SeatResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    room_id: str
    camera_id: Optional[str]
    seat_code: str
    seat_label: Optional[str]
    polygon_json: str
    enabled: bool
    created_at: datetime.datetime


# ---- Exam Session Schemas ----
class SessionCreate(BaseModel):
    room_id: str
    exam_name: str = Field(..., example="Kỳ thi Giữa Kỳ - Toán Cao Cấp")
    subject_code: Optional[str] = Field(None, example="MATH101")
    camera_id: Optional[str] = None
    start_time: Optional[datetime.datetime] = None
    end_time: Optional[datetime.datetime] = None


class SessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    room_id: str
    camera_id: Optional[str]
    exam_name: str
    subject_code: Optional[str]
    start_time: Optional[datetime.datetime]
    end_time: Optional[datetime.datetime]
    started_at: datetime.datetime
    ended_at: Optional[datetime.datetime]
    total_frames: int
    avg_fps: float
    total_events: int
    risk_score: int
    status: str


# ---- Event & Evidence Schemas ----
class EvidenceDetail(BaseModel):
    snapshot_path: Optional[str] = None
    video_path: Optional[str] = None
    video_sha256: Optional[str] = None
    status: str = "READY"


class EventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    room_id: Optional[str]
    camera_id: Optional[str]
    seat_id: Optional[str]
    event_id: str
    track_id: int
    event_type: str
    primary_signal: str
    behavior: str
    severity: str
    risk_score: int
    confidence_avg: float
    confidence_peak: float
    start_frame: int
    end_frame: int
    duration_seconds: float
    status: str
    review_status: str
    model_version: str
    config_version: str
    room_context: Optional[str]
    reviewer_note: Optional[str]
    created_at: datetime.datetime
    evidence_url: Optional[str] = None
    evidence: Optional[EvidenceDetail] = None


# ---- Human Review Schemas ----
class EventReviewUpdate(BaseModel):
    reviewer_id: str = Field("proctor_admin", example="proctor_nguyenvana")
    decision: str = Field(..., example="CONFIRMED", description="CONFIRMED, REJECTED, or INCONCLUSIVE")
    reason_code: Optional[str] = Field(
        "TRUE_SUSPICIOUS",
        example="TRUE_SUSPICIOUS",
        description="TRUE_SUSPICIOUS, NORMAL_BEHAVIOR, PROCTOR_OCCLUSION, LOW_IMAGE_QUALITY, SEAT_MAPPING_ERROR, OTHER",
    )
    note: str = Field("", example="Xác nhận thí sinh quay đầu nhìn bài bạn 3 lần liên tục")


class ReviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_id: str
    reviewer_id: str
    decision: str
    reason_code: Optional[str]
    note: str
    reviewed_at: datetime.datetime


# ---- Worker Node Schemas ----
class WorkerRegisterRequest(BaseModel):
    worker_id: str = Field(..., example="worker-gpu-01")
    hostname: str = Field(..., example="node-gpu-4060.local")
    gpu_name: Optional[str] = Field("NVIDIA GeForce RTX 4060", example="NVIDIA GeForce RTX 4060")
    gpu_memory_mb: int = Field(8192, example=8192)
    max_active_streams: int = Field(10, example=10)
    version: str = Field("1.0.0", example="1.0.0")


class WorkerHeartbeatRequest(BaseModel):
    worker_id: str
    active_camera_count: int = Field(0, ge=0)
    cpu_percent: Optional[float] = None
    gpu_percent: Optional[float] = None
    vram_used_mb: Optional[int] = None


class WorkerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    hostname: str
    gpu_name: Optional[str]
    status: str
    last_heartbeat: datetime.datetime
    active_camera_count: int
    max_active_streams: int
    version: str


# ---- Audit Log Schemas ----
class AuditLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    actor_id: str
    action: str
    resource_type: str
    resource_id: Optional[str]
    metadata_json: Optional[str]
    timestamp: datetime.datetime


# ---- Statistics Schemas ----
class StatisticsSummaryResponse(BaseModel):
    total_events: int
    events_by_behavior: Dict[str, int]
    events_by_severity: Dict[str, int]
    active_rooms: int
    completed_sessions: int
    unreviewed_events_count: int = 0


class RoomRankingItem(BaseModel):
    room_id: str
    room_name: str
    site_name: str
    risk_score: int
    total_events: int
    status: str


# ---- Inference Control Schemas ----
class InferenceStartRequest(BaseModel):
    session_id: str
    video_source: Optional[str] = None  # File path, RTSP url, or "0" for webcam
    max_frames: Optional[int] = None


class InferenceStatusResponse(BaseModel):
    session_id: str
    is_running: bool
    frames_processed: int
    current_fps: float
    events_detected: int
    last_error: Optional[str] = None

