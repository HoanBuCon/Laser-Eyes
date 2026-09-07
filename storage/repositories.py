"""Repository Layer implementing CRUD and Domain Aggregations for VIGIL AI."""

from __future__ import annotations

import datetime
import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from classroom_monitor.models import ClassroomEvent
from storage.db_models import (
    Camera,
    DetectionEvent,
    EvidenceFile,
    ExamRoom,
    ExamSession,
    ExamSite,
)

logger = logging.getLogger("Repositories")


class SiteRepository:
    """Operations for Exam Sites / Campus locations."""

    def __init__(self, db: Session):
        self.db = db

    def create(self, name: str, address: Optional[str] = None, contact_info: Optional[str] = None) -> ExamSite:
        site = ExamSite(name=name, address=address, contact_info=contact_info)
        self.db.add(site)
        self.db.commit()
        self.db.refresh(site)
        return site

    def get_by_id(self, site_id: str) -> Optional[ExamSite]:
        return self.db.query(ExamSite).filter(ExamSite.id == site_id).first()

    def list_all(self, active_only: bool = True) -> List[ExamSite]:
        q = self.db.query(ExamSite)
        if active_only:
            q = q.filter(ExamSite.is_active.is_(True))
        return q.all()


class RoomRepository:
    """Operations for Exam Classrooms."""

    def __init__(self, db: Session):
        self.db = db

    def create(self, site_id: str, name: str, capacity: int = 30, description: Optional[str] = None) -> ExamRoom:
        room = ExamRoom(site_id=site_id, name=name, capacity=capacity, description=description)
        self.db.add(room)
        self.db.commit()
        self.db.refresh(room)
        return room

    def get_by_id(self, room_id: str) -> Optional[ExamRoom]:
        return self.db.query(ExamRoom).filter(ExamRoom.id == room_id).first()

    def list_by_site(self, site_id: str) -> List[ExamRoom]:
        return self.db.query(ExamRoom).filter(ExamRoom.site_id == site_id).all()

    def list_all(self) -> List[ExamRoom]:
        return self.db.query(ExamRoom).all()


class CameraRepository:
    """Operations for Room Surveillance Cameras."""

    def __init__(self, db: Session):
        self.db = db

    def create(
        self,
        room_id: str,
        name: str,
        source_uri: str = "0",
        position: str = "front_center",
    ) -> Camera:
        cam = Camera(room_id=room_id, name=name, source_uri=source_uri, position=position)
        self.db.add(cam)
        self.db.commit()
        self.db.refresh(cam)
        return cam

    def get_by_id(self, camera_id: str) -> Optional[Camera]:
        return self.db.query(Camera).filter(Camera.id == camera_id).first()

    def list_by_room(self, room_id: str) -> List[Camera]:
        return self.db.query(Camera).filter(Camera.room_id == room_id).all()


class SessionRepository:
    """Operations for Active and Completed Exam Sessions."""

    def __init__(self, db: Session):
        self.db = db

    def create(
        self,
        room_id: str,
        exam_name: str,
        camera_id: Optional[str] = None,
    ) -> ExamSession:
        session = ExamSession(
            room_id=room_id,
            camera_id=camera_id,
            exam_name=exam_name,
            started_at=datetime.datetime.utcnow(),
            status="running",
        )
        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)
        return session

    def get_by_id(self, session_id: str) -> Optional[ExamSession]:
        return self.db.query(ExamSession).filter(ExamSession.id == session_id).first()

    def list_active(self) -> List[ExamSession]:
        return self.db.query(ExamSession).filter(ExamSession.status == "running").all()

    def end_session(self, session_id: str) -> Optional[ExamSession]:
        session = self.get_by_id(session_id)
        if session:
            session.ended_at = datetime.datetime.utcnow()
            session.status = "completed"
            self.db.commit()
            self.db.refresh(session)
        return session

    def update_metrics(
        self,
        session_id: str,
        total_frames: int,
        avg_fps: float,
    ) -> None:
        session = self.get_by_id(session_id)
        if session:
            session.total_frames = total_frames
            session.avg_fps = avg_fps
            self.db.commit()

    def recalculate_risk_score(self, session_id: str) -> int:
        """Calculate aggregate session risk score (0-100) based on severity weights."""
        session = self.get_by_id(session_id)
        if not session:
            return 0

        events = (
            self.db.query(DetectionEvent)
            .filter(DetectionEvent.session_id == session_id)
            .all()
        )

        score = 0
        for ev in events:
            if ev.status == "dismissed" or ev.status == "suppressed":
                continue
            if ev.severity == "HIGH":
                score += 25
            elif ev.severity == "MEDIUM":
                score += 12
            else:
                score += 5

        risk_score = min(100, score)
        session.risk_score = risk_score
        session.total_events = len(events)
        self.db.commit()
        return risk_score


class EventRepository:
    """Operations for Cheating and Behavioral Events."""

    def __init__(self, db: Session):
        self.db = db

    def record_event(
        self,
        session_id: str,
        event: ClassroomEvent,
        evidence_rel_path: Optional[str] = None,
        file_size: int = 0,
    ) -> DetectionEvent:
        db_event = DetectionEvent(
            session_id=session_id,
            event_id=event.event_id,
            track_id=event.track_id,
            behavior=event.behavior,
            severity=event.severity,
            confidence_avg=event.confidence_avg,
            confidence_peak=event.confidence_peak,
            start_frame=event.start_frame,
            end_frame=event.end_frame,
            duration_seconds=event.duration_seconds,
            bbox_json=json.dumps(event.bbox) if event.bbox else None,
            status=event.status,
            room_context=event.room_context,
            created_at=datetime.datetime.utcfromtimestamp(event.created_at),
        )
        self.db.add(db_event)
        self.db.commit()
        self.db.refresh(db_event)

        if evidence_rel_path:
            ev_file = EvidenceFile(
                event_id=db_event.id,
                file_path=evidence_rel_path,
                file_size_bytes=file_size,
            )
            self.db.add(ev_file)
            self.db.commit()

        return db_event

    def get_by_id(self, event_id: str) -> Optional[DetectionEvent]:
        return (
            self.db.query(DetectionEvent)
            .filter((DetectionEvent.id == event_id) | (DetectionEvent.event_id == event_id))
            .first()
        )

    def list_by_session(self, session_id: str) -> List[DetectionEvent]:
        return (
            self.db.query(DetectionEvent)
            .filter(DetectionEvent.session_id == session_id)
            .order_by(DetectionEvent.created_at.desc())
            .all()
        )

    def update_review(self, event_id: str, status: str, note: str = "") -> Optional[DetectionEvent]:
        ev = self.get_by_id(event_id)
        if ev:
            ev.status = status
            ev.reviewer_note = note
            self.db.commit()
            self.db.refresh(ev)
        return ev


class StatisticsRepository:
    """Analytical Queries and Aggregations for Executive Dashboards."""

    def __init__(self, db: Session):
        self.db = db

    def get_overall_summary(
        self, site_id: Optional[str] = None, room_id: Optional[str] = None
    ) -> Dict[str, Any]:
        query = self.db.query(DetectionEvent).join(ExamSession)
        if room_id:
            query = query.filter(ExamSession.room_id == room_id)
        elif site_id:
            query = query.join(ExamRoom).filter(ExamRoom.site_id == site_id)

        events = query.all()
        total_events = len(events)

        behavior_counts: Dict[str, int] = {}
        severity_counts: Dict[str, int] = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}

        for ev in events:
            behavior_counts[ev.behavior] = behavior_counts.get(ev.behavior, 0) + 1
            severity_counts[ev.severity] = severity_counts.get(ev.severity, 0) + 1

        active_rooms = (
            self.db.query(func.count(func.distinct(ExamSession.room_id)))
            .filter(ExamSession.status == "running")
            .scalar()
            or 0
        )

        return {
            "total_events": total_events,
            "events_by_behavior": behavior_counts,
            "events_by_severity": severity_counts,
            "active_rooms": active_rooms,
            "completed_sessions": self.db.query(ExamSession).filter(ExamSession.status == "completed").count(),
        }

    def get_room_rankings(self, site_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Rank rooms by cumulative risk scores of their most recent sessions."""
        query = self.db.query(ExamRoom)
        if site_id:
            query = query.filter(ExamRoom.site_id == site_id)

        rooms = query.all()
        rankings = []

        for room in rooms:
            last_session = (
                self.db.query(ExamSession)
                .filter(ExamSession.room_id == room.id)
                .order_by(ExamSession.started_at.desc())
                .first()
            )
            risk = last_session.risk_score if last_session else 0
            ev_count = last_session.total_events if last_session else 0
            rankings.append({
                "room_id": room.id,
                "room_name": room.name,
                "site_name": room.site.name if room.site else "N/A",
                "risk_score": risk,
                "total_events": ev_count,
                "status": last_session.status if last_session else "idle",
            })

        return sorted(rankings, key=lambda x: x["risk_score"], reverse=True)
