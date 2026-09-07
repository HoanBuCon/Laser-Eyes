"""FastAPI Dependency Injection Providers."""

from __future__ import annotations

from typing import Generator
from fastapi import Depends
from sqlalchemy.orm import Session

from storage.database import get_db
from storage.evidence_store import EvidenceStore
from storage.repositories import (
    CameraRepository,
    EventRepository,
    RoomRepository,
    SessionRepository,
    SiteRepository,
    StatisticsRepository,
)

# Global Evidence Store instance
evidence_store = EvidenceStore()


def get_evidence_store() -> EvidenceStore:
    return evidence_store


def get_site_repo(db: Session = Depends(get_db)) -> SiteRepository:
    return SiteRepository(db)


def get_room_repo(db: Session = Depends(get_db)) -> RoomRepository:
    return RoomRepository(db)


def get_camera_repo(db: Session = Depends(get_db)) -> CameraRepository:
    return CameraRepository(db)


def get_session_repo(db: Session = Depends(get_db)) -> SessionRepository:
    return SessionRepository(db)


def get_event_repo(db: Session = Depends(get_db)) -> EventRepository:
    return EventRepository(db)


def get_stats_repo(db: Session = Depends(get_db)) -> StatisticsRepository:
    return StatisticsRepository(db)
