"""VIGIL AI Storage Layer.

Provides SQLAlchemy ORM models, database engine initialization (SQLite/PostgreSQL),
Repository pattern data access objects, and hierarchical evidence image storage.
"""

from storage.database import get_db, init_db
from storage.db_models import (
    Base,
    Camera,
    DetectionEvent,
    EvidenceFile,
    ExamRoom,
    ExamSession,
    ExamSite,
)
from storage.evidence_store import EvidenceStore
from storage.repositories import (
    CameraRepository,
    EventRepository,
    RoomRepository,
    SessionRepository,
    SiteRepository,
    StatisticsRepository,
)

__all__ = [
    "Base",
    "init_db",
    "get_db",
    "ExamSite",
    "ExamRoom",
    "Camera",
    "ExamSession",
    "DetectionEvent",
    "EvidenceFile",
    "EvidenceStore",
    "SiteRepository",
    "RoomRepository",
    "CameraRepository",
    "SessionRepository",
    "EventRepository",
    "StatisticsRepository",
]
