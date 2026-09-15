"""SQLAlchemy Relational Database Models for VIGIL AI Enterprise Proctoring."""

from __future__ import annotations

import datetime
import uuid
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from storage.database import Base


def generate_uuid() -> str:
    """Generate string UUID primary key."""
    return str(uuid.uuid4())


class ExamSite(Base):
    """Exam Site / Campus Location holding multiple exam rooms."""

    __tablename__ = "exam_sites"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    name = Column(String(255), nullable=False)
    address = Column(Text, nullable=True)
    contact_info = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # Relationships
    rooms = relationship("ExamRoom", back_populates="site", cascade="all, delete-orphan")


class ExamRoom(Base):
    """Individual Exam Classroom equipped with surveillance cameras."""

    __tablename__ = "exam_rooms"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    site_id = Column(String(36), ForeignKey("exam_sites.id"), nullable=False)
    name = Column(String(255), nullable=False)
    capacity = Column(Integer, default=30)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # Relationships
    site = relationship("ExamSite", back_populates="rooms")
    cameras = relationship("Camera", back_populates="room", cascade="all, delete-orphan")
    sessions = relationship("ExamSession", back_populates="room", cascade="all, delete-orphan")


class Camera(Base):
    """Video capture hardware or network stream attached to a room."""

    __tablename__ = "cameras"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    room_id = Column(String(36), ForeignKey("exam_rooms.id"), nullable=False)
    name = Column(String(255), nullable=False)
    source_uri = Column(String(500), default="0")  # RTSP URL or Device Index
    position = Column(String(50), default="front_center")
    status = Column(String(20), default="online")  # "online", "offline", "error"
    last_seen_at = Column(DateTime, default=datetime.datetime.utcnow)

    # Relationships
    room = relationship("ExamRoom", back_populates="cameras")


class ExamSession(Base):
    """A proctored exam session taking place in a room."""

    __tablename__ = "exam_sessions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    room_id = Column(String(36), ForeignKey("exam_rooms.id"), nullable=False)
    camera_id = Column(String(36), ForeignKey("cameras.id"), nullable=True)
    exam_name = Column(String(255), nullable=False)
    started_at = Column(DateTime, default=datetime.datetime.utcnow)
    ended_at = Column(DateTime, nullable=True)
    total_frames = Column(Integer, default=0)
    avg_fps = Column(Float, default=0.0)
    total_events = Column(Integer, default=0)
    risk_score = Column(Integer, default=0)  # 0 to 100
    status = Column(String(20), default="running")  # "running", "completed", "cancelled"

    # Relationships
    room = relationship("ExamRoom", back_populates="sessions")
    events = relationship(
        "DetectionEvent", back_populates="session", cascade="all, delete-orphan"
    )


class DetectionEvent(Base):
    """A detected violation episode (e.g. phone using, peeking)."""

    __tablename__ = "detection_events"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    session_id = Column(String(36), ForeignKey("exam_sessions.id"), nullable=False)
    event_id = Column(String(36), nullable=False, index=True)  # Human-readable EVT-XXXX
    track_id = Column(Integer, default=0)
    behavior = Column(String(50), nullable=False)
    severity = Column(String(10), default="MEDIUM")  # "LOW", "MEDIUM", "HIGH"
    confidence_avg = Column(Float, default=0.0)
    confidence_peak = Column(Float, default=0.0)
    start_frame = Column(Integer, default=0)
    end_frame = Column(Integer, default=0)
    duration_seconds = Column(Float, default=0.0)
    bbox_json = Column(Text, nullable=True)
    status = Column(String(20), default="suspicious")  # "suspicious", "confirmed", "dismissed"
    room_context = Column(Text, nullable=True)
    reviewer_note = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # Relationships
    session = relationship("ExamSession", back_populates="events")
    evidence = relationship(
        "EvidenceFile", back_populates="event", uselist=False, cascade="all, delete-orphan"
    )


class EvidenceFile(Base):
    """Archived evidence image (JPEG) corresponding to an event."""

    __tablename__ = "evidence_files"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    event_id = Column(String(36), ForeignKey("detection_events.id"), nullable=False)
    file_path = Column(String(500), nullable=False)
    file_type = Column(String(50), default="image/jpeg")
    file_size_bytes = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # Relationships
    event = relationship("DetectionEvent", back_populates="evidence")
