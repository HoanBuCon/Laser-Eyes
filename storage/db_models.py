"""SQLAlchemy Relational Database Models for VIGIL AI Enterprise Proctoring.

Comprehensive 9-table relational schema matching SRS v1.0 specifications:
1. exam_sites / rooms
2. cameras
3. seats (Seat ROI Polygons)
4. exam_sessions
5. detection_events (AI Events)
6. evidence_files (Snapshots, MP4 Video, SHA-256 Hash)
7. event_reviews (Human-in-the-Loop Review Decisions)
8. worker_nodes (Inference Worker Heartbeats & Registrations)
9. audit_logs (Immutable Audit Trail)
"""

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
    site_id = Column(String(36), ForeignKey("exam_sites.id"), nullable=True)
    room_code = Column(String(50), nullable=True, index=True)
    name = Column(String(255), nullable=False)
    building = Column(String(100), nullable=True)
    floor = Column(String(50), nullable=True)
    capacity = Column(Integer, default=30)
    description = Column(Text, nullable=True)
    status = Column(String(20), default="active")  # "active", "maintenance", "inactive"
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    # Relationships
    site = relationship("ExamSite", back_populates="rooms")
    cameras = relationship("Camera", back_populates="room", cascade="all, delete-orphan")
    sessions = relationship("ExamSession", back_populates="room", cascade="all, delete-orphan")
    seats = relationship("SeatROI", back_populates="room", cascade="all, delete-orphan")
    events = relationship("DetectionEvent", back_populates="room", cascade="all, delete-orphan")


class Camera(Base):
    """Video capture hardware or network stream attached to a room."""

    __tablename__ = "cameras"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    room_id = Column(String(36), ForeignKey("exam_rooms.id"), nullable=False)
    name = Column(String(255), nullable=False)
    source_uri = Column(String(500), default="0")  # RTSP URL or Device Index
    rtsp_url_protected = Column(String(500), nullable=True)
    position = Column(String(50), default="front_center")
    resolution = Column(String(50), default="1280x720")
    capture_fps = Column(Float, default=30.0)
    inference_fps = Column(Float, default=5.0)
    worker_id = Column(String(36), ForeignKey("worker_nodes.id"), nullable=True)
    enabled = Column(Boolean, default=True)
    status = Column(String(20), default="online")  # "online", "degraded", "offline", "error"
    last_seen_at = Column(DateTime, default=datetime.datetime.utcnow)

    # Relationships
    room = relationship("ExamRoom", back_populates="cameras")
    worker = relationship("WorkerNode", back_populates="assigned_cameras")
    seats = relationship("SeatROI", back_populates="camera", cascade="all, delete-orphan")
    events = relationship("DetectionEvent", back_populates="camera", cascade="all, delete-orphan")


class SeatROI(Base):
    """Seat Region-of-Interest Polygon mapping a physical seat in a room camera view."""

    __tablename__ = "seats"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    room_id = Column(String(36), ForeignKey("exam_rooms.id"), nullable=False)
    camera_id = Column(String(36), ForeignKey("cameras.id"), nullable=True)
    seat_code = Column(String(50), nullable=False, index=True)  # e.g., "A101_S01"
    seat_label = Column(String(100), nullable=True)             # e.g., "Row 1 Desk 1"
    polygon_json = Column(Text, nullable=False)                 # JSON array of points [[x, y], ...]
    context_json = Column(Text, nullable=True)                  # Scene/Seat context & neighbor graph
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    # Relationships
    room = relationship("ExamRoom", back_populates="seats")
    camera = relationship("Camera", back_populates="seats")
    events = relationship("DetectionEvent", back_populates="seat", cascade="all, delete-orphan")
    episodes = relationship("BehaviorEpisodeDB", back_populates="seat", cascade="all, delete-orphan")
    patterns = relationship("BehaviorPatternDB", back_populates="seat", cascade="all, delete-orphan")
    temporal_episodes = relationship("TemporalEpisodeAnnotation", back_populates="seat", cascade="all, delete-orphan")


class BehaviorEpisodeDB(Base):
    """Time-bounded atomic behavior episode recorded per seat."""

    __tablename__ = "behavior_episodes"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    session_id = Column(String(36), ForeignKey("exam_sessions.id"), nullable=True)
    seat_id = Column(String(36), ForeignKey("seats.id"), nullable=False)
    episode_type = Column(String(50), nullable=False)  # e.g., "HEAD_TURN_LEFT", "TORSO_LEAN_RIGHT"
    start_timestamp_ms = Column(Float, nullable=False)
    peak_timestamp_ms = Column(Float, nullable=False)
    end_timestamp_ms = Column(Float, nullable=True)
    duration_ms = Column(Float, default=0.0)
    confidence = Column(Float, default=1.0)
    quality = Column(Float, default=1.0)
    metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # Relationships
    seat = relationship("SeatROI", back_populates="episodes")


class BehaviorPatternDB(Base):
    """Synthesized review-worthy behavioral pattern recorded per seat."""

    __tablename__ = "behavior_patterns"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    session_id = Column(String(36), ForeignKey("exam_sessions.id"), nullable=True)
    seat_id = Column(String(36), ForeignKey("seats.id"), nullable=False)
    pattern_type = Column(String(50), nullable=False)  # e.g., "REPEATED_NEIGHBOR_GLANCE"
    start_timestamp_ms = Column(Float, nullable=False)
    end_timestamp_ms = Column(Float, nullable=False)
    confidence = Column(Float, default=1.0)
    quality = Column(Float, default=1.0)
    primary_direction = Column(String(20), nullable=True)
    target_neighbor_id = Column(String(50), nullable=True)
    component_episode_ids_json = Column(Text, nullable=True)
    metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # Relationships
    seat = relationship("SeatROI", back_populates="patterns")


class ExamSession(Base):
    """A proctored exam session taking place in a room."""

    __tablename__ = "exam_sessions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    room_id = Column(String(36), ForeignKey("exam_rooms.id"), nullable=False)
    camera_id = Column(String(36), ForeignKey("cameras.id"), nullable=True)
    exam_name = Column(String(255), nullable=False)
    subject_code = Column(String(50), nullable=True)
    start_time = Column(DateTime, nullable=True)
    end_time = Column(DateTime, nullable=True)
    started_at = Column(DateTime, default=datetime.datetime.utcnow)
    ended_at = Column(DateTime, nullable=True)
    total_frames = Column(Integer, default=0)
    avg_fps = Column(Float, default=0.0)
    total_events = Column(Integer, default=0)
    risk_score = Column(Integer, default=0)  # 0 to 100
    status = Column(String(20), default="RUNNING")  # "DRAFT", "READY", "RUNNING", "STOPPING", "COMPLETED", "FAILED"

    # Relationships
    room = relationship("ExamRoom", back_populates="sessions")
    events = relationship(
        "DetectionEvent", back_populates="session", cascade="all, delete-orphan"
    )


class DetectionEvent(Base):
    """A detected suspicious behavioral event associated with a Seat and Session."""

    __tablename__ = "detection_events"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    session_id = Column(String(36), ForeignKey("exam_sessions.id"), nullable=False)
    room_id = Column(String(36), ForeignKey("exam_rooms.id"), nullable=True)
    camera_id = Column(String(36), ForeignKey("cameras.id"), nullable=True)
    seat_id = Column(String(36), ForeignKey("seats.id"), nullable=True)
    event_id = Column(String(36), nullable=False, index=True, unique=True)  # idempotency key
    track_id = Column(Integer, default=0)
    event_type = Column(String(50), default="SUSPICIOUS_BEHAVIOR")
    primary_signal = Column(String(50), default="PROLONGED_HEAD_TURN")
    behavior = Column(String(50), nullable=False)  # Legacy alias matching primary_signal
    primary_pattern = Column(String(50), nullable=True)  # SRS v2: e.g. "REPEATED_NEIGHBOR_GLANCE"
    supporting_patterns_json = Column(Text, nullable=True)  # SRS v2: list of supporting pattern cues
    observation_quality = Column(Float, default=1.0)        # SRS v2: quality metric
    severity = Column(String(10), default="MEDIUM")  # "LOW", "MEDIUM", "HIGH", "CRITICAL"
    risk_score = Column(Integer, default=50)  # 0 to 100 normalized score
    confidence_avg = Column(Float, default=0.0)
    confidence_peak = Column(Float, default=0.0)
    start_frame = Column(Integer, default=0)
    end_frame = Column(Integer, default=0)
    start_timestamp = Column(DateTime, nullable=True)
    peak_timestamp = Column(DateTime, nullable=True)
    end_timestamp = Column(DateTime, nullable=True)
    duration_seconds = Column(Float, default=0.0)
    bbox_json = Column(Text, nullable=True)
    status = Column(String(30), default="PENDING")  # "PENDING", "CONFIRMED", "REJECTED", "INCONCLUSIVE", "FLAGGED_FOR_HUMAN_REVIEW"
    review_status = Column(String(20), default="PENDING")  # "PENDING", "CONFIRMED", "REJECTED", "INCONCLUSIVE"
    model_version = Column(String(50), default="yolo11n-pose")
    config_version = Column(String(50), default="school-prototype-v1")
    room_context = Column(Text, nullable=True)
    reviewer_note = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # Relationships
    session = relationship("ExamSession", back_populates="events")
    room = relationship("ExamRoom", back_populates="events")
    camera = relationship("Camera", back_populates="events")
    seat = relationship("SeatROI", back_populates="events")
    evidence = relationship(
        "EvidenceFile", back_populates="event", uselist=False, cascade="all, delete-orphan"
    )
    review = relationship(
        "EventReview", back_populates="event", uselist=False, cascade="all, delete-orphan"
    )


class EvidenceFile(Base):
    """Archived evidence package (JPEG Snapshot + 10s MP4 Video + SHA-256 Hash)."""

    __tablename__ = "evidence_files"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    event_id = Column(String(36), ForeignKey("detection_events.id"), nullable=False, unique=True)
    file_path = Column(String(500), nullable=False)  # Primary file (or snapshot)
    snapshot_path = Column(String(500), nullable=True)
    video_path = Column(String(500), nullable=True)
    video_sha256 = Column(String(64), nullable=True)  # SHA-256 integrity digest
    file_type = Column(String(50), default="video/mp4")
    file_size_bytes = Column(Integer, default=0)
    status = Column(String(20), default="READY")  # "PENDING", "READY", "FAILED"
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # Relationships
    event = relationship("DetectionEvent", back_populates="evidence")


class EventReview(Base):
    """Human-in-the-Loop Proctor Review Record for an AI Event."""

    __tablename__ = "event_reviews"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    event_id = Column(String(36), ForeignKey("detection_events.id"), nullable=False, unique=True)
    reviewer_id = Column(String(100), nullable=False)  # User ID or Username
    decision = Column(String(20), nullable=False)      # "CONFIRMED", "REJECTED", "INCONCLUSIVE"
    reason_code = Column(String(50), nullable=True)    # "TRUE_SUSPICIOUS", "NORMAL_BEHAVIOR", "PROCTOR_OCCLUSION", "LOW_IMAGE_QUALITY", "SEAT_MAPPING_ERROR", "OTHER"
    note = Column(Text, default="")
    reviewed_at = Column(DateTime, default=datetime.datetime.utcnow)

    # Relationships
    event = relationship("DetectionEvent", back_populates="review")


class WorkerNode(Base):
    """Distributed Inference Worker Node handling RTSP decoding and AI perception."""

    __tablename__ = "worker_nodes"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    hostname = Column(String(255), nullable=False)
    gpu_name = Column(String(255), nullable=True)
    gpu_memory_mb = Column(Integer, default=0)
    status = Column(String(20), default="ONLINE")  # "ONLINE", "DEGRADED", "OFFLINE"
    last_heartbeat = Column(DateTime, default=datetime.datetime.utcnow)
    active_camera_count = Column(Integer, default=0)
    max_active_streams = Column(Integer, default=10)
    version = Column(String(50), default="1.0.0")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # Relationships
    assigned_cameras = relationship("Camera", back_populates="worker")


class AuditLog(Base):
    """Immutable Audit Log Trail for Critical Actions in the System."""

    __tablename__ = "audit_logs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    actor_id = Column(String(100), nullable=False)
    action = Column(String(100), nullable=False)  # "LOGIN", "START_SESSION", "STOP_SESSION", "REVIEW_EVENT", "UPDATE_SEAT_ROI"
    resource_type = Column(String(50), nullable=False) # "SESSION", "EVENT", "SEAT", "CAMERA", "ROOM"
    resource_id = Column(String(100), nullable=True)
    metadata_json = Column(Text, nullable=True)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)


# ==============================================================================
# HUMAN DATA OPERATIONS WORKBENCH SCHEMA (Sprint 2 - Observation Quality)
# ==============================================================================

class MediaAsset(Base):
    """Raw media item (image/video/audio) tracked non-destructively for audit & annotation."""

    __tablename__ = "media_assets"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    asset_type = Column(String(20), default="IMAGE")  # "IMAGE", "VIDEO", "AUDIO"
    file_path = Column(String(500), nullable=False)
    relative_path = Column(String(500), nullable=False)
    file_name = Column(String(255), nullable=False)
    original_split = Column(String(20), nullable=True)  # "train", "val", "test", "demo", "staged"
    width = Column(Integer, default=0)
    height = Column(Integer, default=0)
    fps = Column(Float, default=0.0)
    total_frames = Column(Integer, default=0)
    duration_seconds = Column(Float, default=0.0)
    sha256_hash = Column(String(64), nullable=True)
    audit_status = Column(String(30), default="UNAUDITED")  # "UNAUDITED", "AUDITED", "FLAGGED", "REJECTED"
    annotations_count = Column(Integer, default=0)
    metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    # Relationships
    image_revisions = relationship("ImageAnnotationRevision", back_populates="asset", cascade="all, delete-orphan")
    temporal_episodes = relationship("TemporalEpisodeAnnotation", back_populates="asset", cascade="all, delete-orphan")
    dataset_items = relationship("DatasetItem", back_populates="asset", cascade="all, delete-orphan")


class ImageAnnotationRevision(Base):
    """Non-destructive human review or correction of an image bounding box / actor crop."""

    __tablename__ = "image_annotation_revisions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    asset_id = Column(String(36), ForeignKey("media_assets.id"), nullable=False)
    bbox_index = Column(Integer, default=0)
    original_class = Column(String(50), nullable=False)
    reviewed_class = Column(String(50), nullable=False)
    bbox_json = Column(Text, nullable=False)  # Normalized [x_center, y_center, width, height]
    is_ambiguous = Column(Boolean, default=False)
    is_rejected = Column(Boolean, default=False)
    posture_tags_json = Column(Text, nullable=True)  # e.g., ["LOOK_LEFT", "HAND_ON_DESK"]
    audit_notes = Column(Text, nullable=True)
    reviewer_id = Column(String(100), default="annotator")
    reviewed_at = Column(DateTime, default=datetime.datetime.utcnow)

    # Relationships
    asset = relationship("MediaAsset", back_populates="image_revisions")


class TemporalEpisodeAnnotation(Base):
    """Ground-truth millisecond-level temporal episode annotated by human or proposed by AI."""

    __tablename__ = "temporal_episode_annotations"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    asset_id = Column(String(36), ForeignKey("media_assets.id"), nullable=False)
    seat_id = Column(String(36), ForeignKey("seats.id"), nullable=True)
    seat_code = Column(String(50), nullable=True)
    episode_type = Column(String(50), nullable=False)  # e.g. "HEAD_TURN_LEFT", "TORSO_LEAN_RIGHT"
    start_ms = Column(Float, nullable=False)
    peak_ms = Column(Float, nullable=False)
    end_ms = Column(Float, nullable=False)
    duration_ms = Column(Float, default=0.0)
    target_neighbor_id = Column(String(50), nullable=True)
    confidence = Column(Float, default=1.0)
    is_ai_proposal = Column(Boolean, default=False)
    ai_match_iou = Column(Float, default=0.0)
    reviewer_id = Column(String(100), default="annotator")
    review_status = Column(String(30), default="ACCEPTED")  # "ACCEPTED", "MODIFIED", "REJECTED", "AI_PROPOSED", "HUMAN_ONLY"
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    # Relationships
    asset = relationship("MediaAsset", back_populates="temporal_episodes")
    seat = relationship("SeatROI", back_populates="temporal_episodes")


class DatasetCollection(Base):
    """Logical grouping of ML training/validation datasets (e.g. Actor Crops, Episodes)."""

    __tablename__ = "dataset_collections"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    name = Column(String(255), nullable=False)
    task_type = Column(String(50), default="ACTOR_CLASSIFICATION")  # "ACTOR_CLASSIFICATION", "TEMPORAL_EPISODE", "YOLO_BBOX"
    description = Column(Text, nullable=True)
    created_by = Column(String(100), default="engineer")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # Relationships
    versions = relationship("DatasetVersion", back_populates="collection", cascade="all, delete-orphan")


class DatasetVersion(Base):
    """Specific released or draft version of a curated dataset with manifest and group splits."""

    __tablename__ = "dataset_versions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    collection_id = Column(String(36), ForeignKey("dataset_collections.id"), nullable=False)
    version_tag = Column(String(50), nullable=False)  # e.g., "v1.0.0"
    split_strategy = Column(String(50), default="GROUP_BY_SESSION")  # "GROUP_BY_SESSION", "STRATIFIED", "RANDOM"
    train_ratio = Column(Float, default=0.70)
    val_ratio = Column(Float, default=0.15)
    test_ratio = Column(Float, default=0.15)
    export_format = Column(String(50), default="CLASSIFICATION_CROPS")  # "CLASSIFICATION_CROPS", "YOLO_DIR", "EPISODE_JSON"
    export_path = Column(String(500), nullable=True)
    manifest_json = Column(Text, nullable=True)
    total_items = Column(Integer, default=0)
    status = Column(String(30), default="READY")  # "DRAFT", "EXPORTING", "READY", "FAILED"
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # Relationships
    collection = relationship("DatasetCollection", back_populates="versions")
    items = relationship("DatasetItem", back_populates="version", cascade="all, delete-orphan")


class DatasetItem(Base):
    """An individual sample associated with a curated dataset version."""

    __tablename__ = "dataset_items"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    version_id = Column(String(36), ForeignKey("dataset_versions.id"), nullable=False)
    asset_id = Column(String(36), ForeignKey("media_assets.id"), nullable=True)
    split = Column(String(20), nullable=False)  # "train", "val", "test"
    label = Column(String(100), nullable=False)
    relative_path = Column(String(500), nullable=True)
    metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # Relationships
    version = relationship("DatasetVersion", back_populates="items")
    asset = relationship("MediaAsset", back_populates="dataset_items")


class StagedRecordingSession(Base):
    """Structured mock/staged exam recording session following an actor protocol."""

    __tablename__ = "staged_recording_sessions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    session_code = Column(String(50), nullable=False, index=True)  # e.g., "STAGE-2026-09-01"
    room_id = Column(String(36), ForeignKey("exam_rooms.id"), nullable=True)
    script_name = Column(String(255), nullable=False)
    actor_names_json = Column(Text, nullable=True)
    target_video_path = Column(String(500), nullable=True)
    recorded_at = Column(DateTime, default=datetime.datetime.utcnow)
    status = Column(String(30), default="PLANNED")  # "PLANNED", "RECORDED", "ANNOTATED", "VERIFIED"
    notes = Column(Text, nullable=True)

    # Relationships
    scenarios = relationship("StagedScenarioChecklist", back_populates="session", cascade="all, delete-orphan")


class StagedScenarioChecklist(Base):
    """Scenario execution item within a staged recording session."""

    __tablename__ = "staged_scenario_checklists"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    session_id = Column(String(36), ForeignKey("staged_recording_sessions.id"), nullable=False)
    scenario_code = Column(String(50), nullable=False)  # e.g., "SCEN-01-LEFT-PEEK"
    title = Column(String(255), nullable=False)
    expected_behavior = Column(String(100), nullable=False)
    seat_code = Column(String(50), nullable=True)
    target_start_ms = Column(Float, default=0.0)
    target_end_ms = Column(Float, default=0.0)
    actual_start_ms = Column(Float, nullable=True)
    actual_end_ms = Column(Float, nullable=True)
    status = Column(String(30), default="PENDING")  # "PENDING", "PASS", "FAIL", "RE_RECORD"
    notes = Column(Text, nullable=True)

    # Relationships
    session = relationship("StagedRecordingSession", back_populates="scenarios")
