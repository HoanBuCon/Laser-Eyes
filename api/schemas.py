"""Pydantic Request and Response Schemas for VIGIL AI REST API."""

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
    site_id: str
    name: str = Field(..., example="Phòng A1-302")
    capacity: int = Field(30, ge=1, le=500)
    description: Optional[str] = Field(None, example="Phòng thi lý thuyết tầng 3")


class RoomResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    site_id: str
    name: str
    capacity: int
    description: Optional[str]
    is_active: bool
    created_at: datetime.datetime


# ---- Camera Schemas ----
class CameraCreate(BaseModel):
    room_id: str
    name: str = Field(..., example="Camera góc rộng bảng trước")
    source_uri: str = Field("0", example="rtsp://admin:pass@192.168.1.100:554/stream1")
    position: str = Field("front_center", example="front_center")


class CameraResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    room_id: str
    name: str
    source_uri: str
    position: str
    status: str
    last_seen_at: datetime.datetime


# ---- Exam Session Schemas ----
class SessionCreate(BaseModel):
    room_id: str
    exam_name: str = Field(..., example="Kỳ thi Đánh giá Năng lực - Ca Sáng")
    camera_id: Optional[str] = None


class SessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    room_id: str
    camera_id: Optional[str]
    exam_name: str
    started_at: datetime.datetime
    ended_at: Optional[datetime.datetime]
    total_frames: int
    avg_fps: float
    total_events: int
    risk_score: int
    status: str


# ---- Event Schemas ----
class EventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    event_id: str
    track_id: int
    behavior: str
    severity: str
    confidence_avg: float
    confidence_peak: float
    start_frame: int
    end_frame: int
    duration_seconds: float
    status: str
    room_context: Optional[str]
    reviewer_note: Optional[str]
    created_at: datetime.datetime
    evidence_url: Optional[str] = None


class EventReviewUpdate(BaseModel):
    status: str = Field(..., example="confirmed", description="confirmed, dismissed, or reviewed")
    reviewer_note: str = Field("", example="Xác nhận thí sinh dùng tài liệu tại phút thứ 12")


# ---- Statistics Schemas ----
class StatisticsSummaryResponse(BaseModel):
    total_events: int
    events_by_behavior: Dict[str, int]
    events_by_severity: Dict[str, int]
    active_rooms: int
    completed_sessions: int


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
