"""Repository Layer implementing CRUD and Domain Aggregations for VIGIL AI.

Comprehensive data access layer supporting:
1. SiteRepository & RoomRepository
2. CameraRepository
3. SeatRepository (Seat ROI Polygons)
4. SessionRepository
5. EventRepository & EvidenceRepository (SHA-256 integrity)
6. ReviewRepository (Human-in-the-Loop Decisions)
7. WorkerRepository (Inference Worker Heartbeats & Node Management)
8. AuditLogRepository
"""

from __future__ import annotations

import datetime
import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from classroom_monitor.models import ClassroomEvent
from storage.db_models import (
    AuditLog,
    Camera,
    DetectionEvent,
    EventReview,
    EvidenceFile,
    ExamRoom,
    ExamSession,
    ExamSite,
    SeatROI,
    WorkerNode,
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

    def create(
        self,
        name: str,
        site_id: Optional[str] = None,
        room_code: Optional[str] = None,
        building: Optional[str] = None,
        floor: Optional[str] = None,
        capacity: int = 30,
        description: Optional[str] = None,
        status: str = "active",
    ) -> ExamRoom:
        room = ExamRoom(
            name=name,
            site_id=site_id,
            room_code=room_code or name,
            building=building,
            floor=floor,
            capacity=capacity,
            description=description,
            status=status,
        )
        self.db.add(room)
        self.db.commit()
        self.db.refresh(room)
        return room

    def get_by_id(self, room_id: str) -> Optional[ExamRoom]:
        return self.db.query(ExamRoom).filter(ExamRoom.id == room_id).first()

    def get_by_code(self, room_code: str) -> Optional[ExamRoom]:
        return self.db.query(ExamRoom).filter(ExamRoom.room_code == room_code).first()

    def list_by_site(self, site_id: str) -> List[ExamRoom]:
        return self.db.query(ExamRoom).filter(ExamRoom.site_id == site_id).all()

    def list_all(self) -> List[ExamRoom]:
        return self.db.query(ExamRoom).all()

    def update(self, room_id: str, **kwargs) -> Optional[ExamRoom]:
        room = self.get_by_id(room_id)
        if room:
            for k, v in kwargs.items():
                if hasattr(room, k) and v is not None:
                    setattr(room, k, v)
            room.updated_at = datetime.datetime.utcnow()
            self.db.commit()
            self.db.refresh(room)
        return room


class CameraRepository:
    """Operations for Room Surveillance Cameras."""

    def __init__(self, db: Session):
        self.db = db

    def create(
        self,
        room_id: str,
        name: str,
        source_uri: str = "0",
        rtsp_url_protected: Optional[str] = None,
        position: str = "front_center",
        resolution: str = "1280x720",
        capture_fps: float = 30.0,
        inference_fps: float = 5.0,
        worker_id: Optional[str] = None,
        enabled: bool = True,
    ) -> Camera:
        cam = Camera(
            room_id=room_id,
            name=name,
            source_uri=source_uri,
            rtsp_url_protected=rtsp_url_protected or source_uri,
            position=position,
            resolution=resolution,
            capture_fps=capture_fps,
            inference_fps=inference_fps,
            worker_id=worker_id,
            enabled=enabled,
        )
        self.db.add(cam)
        self.db.commit()
        self.db.refresh(cam)
        return cam

    def get_by_id(self, camera_id: str) -> Optional[Camera]:
        return self.db.query(Camera).filter(Camera.id == camera_id).first()

    def list_by_room(self, room_id: str) -> List[Camera]:
        return self.db.query(Camera).filter(Camera.room_id == room_id).all()

    def list_all(self) -> List[Camera]:
        return self.db.query(Camera).all()

    def update_status(self, camera_id: str, status: str) -> Optional[Camera]:
        cam = self.get_by_id(camera_id)
        if cam:
            cam.status = status
            cam.last_seen_at = datetime.datetime.utcnow()
            self.db.commit()
            self.db.refresh(cam)
        return cam


class SeatRepository:
    """Operations for Seat ROI Polygons mapping physical exam desks."""

    def __init__(self, db: Session):
        self.db = db

    def create(
        self,
        room_id: str,
        seat_code: str,
        polygon_json: str | List[List[float]],
        camera_id: Optional[str] = None,
        seat_label: Optional[str] = None,
        enabled: bool = True,
    ) -> SeatROI:
        poly_str = polygon_json if isinstance(polygon_json, str) else json.dumps(polygon_json)
        seat = SeatROI(
            room_id=room_id,
            camera_id=camera_id,
            seat_code=seat_code,
            seat_label=seat_label or seat_code,
            polygon_json=poly_str,
            enabled=enabled,
        )
        self.db.add(seat)
        self.db.commit()
        self.db.refresh(seat)
        return seat

    def get_by_id(self, seat_id: str) -> Optional[SeatROI]:
        return self.db.query(SeatROI).filter(SeatROI.id == seat_id).first()

    def get_by_code(self, room_id: str, seat_code: str) -> Optional[SeatROI]:
        return (
            self.db.query(SeatROI)
            .filter(SeatROI.room_id == room_id, SeatROI.seat_code == seat_code)
            .first()
        )

    def list_by_room(self, room_id: str, enabled_only: bool = False) -> List[SeatROI]:
        q = self.db.query(SeatROI).filter(SeatROI.room_id == room_id)
        if enabled_only:
            q = q.filter(SeatROI.enabled.is_(True))
        return q.all()

    def list_by_camera(self, camera_id: str) -> List[SeatROI]:
        return self.db.query(SeatROI).filter(SeatROI.camera_id == camera_id).all()

    def bulk_upsert_for_camera(
        self, room_id: str, camera_id: str, seats_data: List[Dict[str, Any]]
    ) -> List[SeatROI]:
        """Atomically sync seat polygons for a given room/camera."""
        results = []
        for s in seats_data:
            seat_code = s["seat_code"]
            existing = self.get_by_code(room_id, seat_code)
            poly_str = s["polygon_json"] if isinstance(s["polygon_json"], str) else json.dumps(s["polygon_json"])
            if existing:
                existing.camera_id = camera_id
                existing.polygon_json = poly_str
                existing.seat_label = s.get("seat_label", existing.seat_label)
                existing.enabled = s.get("enabled", True)
                existing.updated_at = datetime.datetime.utcnow()
                results.append(existing)
            else:
                new_seat = SeatROI(
                    room_id=room_id,
                    camera_id=camera_id,
                    seat_code=seat_code,
                    seat_label=s.get("seat_label", seat_code),
                    polygon_json=poly_str,
                    enabled=s.get("enabled", True),
                )
                self.db.add(new_seat)
                results.append(new_seat)
        self.db.commit()
        return results

    def delete(self, seat_id: str) -> bool:
        seat = self.get_by_id(seat_id)
        if seat:
            self.db.delete(seat)
            self.db.commit()
            return True
        return False


class SessionRepository:
    """Operations for Active and Completed Exam Sessions."""

    def __init__(self, db: Session):
        self.db = db

    def create(
        self,
        room_id: str,
        exam_name: str,
        subject_code: Optional[str] = None,
        camera_id: Optional[str] = None,
        start_time: Optional[datetime.datetime] = None,
        end_time: Optional[datetime.datetime] = None,
        status: str = "running",
    ) -> ExamSession:
        session = ExamSession(
            room_id=room_id,
            camera_id=camera_id,
            exam_name=exam_name,
            subject_code=subject_code,
            start_time=start_time or datetime.datetime.utcnow(),
            end_time=end_time,
            started_at=datetime.datetime.utcnow(),
            status=status,
        )
        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)
        return session

    def get_by_id(self, session_id: str) -> Optional[ExamSession]:
        return self.db.query(ExamSession).filter(ExamSession.id == session_id).first()

    def start_session(self, session_id: str) -> Optional[ExamSession]:
        session = self.get_by_id(session_id)
        if session:
            session.status = "RUNNING"
            session.started_at = datetime.datetime.utcnow()
            self.db.commit()
            self.db.refresh(session)
        return session

    def stop_session(self, session_id: str, status: str = "COMPLETED") -> Optional[ExamSession]:
        session = self.get_by_id(session_id)
        if session:
            session.status = status
            session.ended_at = datetime.datetime.utcnow()
            self.db.commit()
            self.db.refresh(session)
        return session

    def recalculate_risk_score(self, session_id: str) -> int:
        """Aggregate total events and compute a normalized session risk index (0 - 100)."""
        session = self.get_by_id(session_id)
        if not session:
            return 0
        events = (
            self.db.query(DetectionEvent)
            .filter(DetectionEvent.session_id == session_id)
            .all()
        )
        total_risk = 0
        weights = {"CRITICAL": 35, "HIGH": 25, "MEDIUM": 15, "LOW": 5}
        for ev in events:
            total_risk += weights.get(ev.severity.upper(), 10)
        risk = min(100, total_risk)
        session.risk_score = risk
        session.total_events = len(events)
        self.db.commit()
        return risk



class EventRepository:
    """Operations for Detected AI Proctoring Events."""

    def __init__(self, db: Session):
        self.db = db

    def create_from_domain_event(
        self,
        session_id: str,
        event: ClassroomEvent,
        room_id: Optional[str] = None,
        camera_id: Optional[str] = None,
        seat_id: Optional[str] = None,
        risk_score: int = 75,
        primary_signal: Optional[str] = None,
        model_version: str = "yolo11n-pose",
        config_version: str = "school-prototype-v1",
    ) -> DetectionEvent:
        """Persist a Domain ClassroomEvent as an SQL Record with idempotency."""
        existing = (
            self.db.query(DetectionEvent)
            .filter(DetectionEvent.event_id == event.event_id)
            .first()
        )
        if existing:
            return existing

        db_event = DetectionEvent(
            session_id=session_id,
            room_id=room_id,
            camera_id=camera_id,
            seat_id=seat_id,
            event_id=event.event_id,
            track_id=event.track_id,
            event_type="SUSPICIOUS_BEHAVIOR",
            primary_signal=primary_signal or event.behavior,
            behavior=event.behavior,
            severity=event.severity,
            risk_score=risk_score,
            confidence_avg=event.confidence_avg,
            confidence_peak=event.confidence_peak,
            start_frame=event.start_frame,
            end_frame=event.end_frame,
            duration_seconds=event.duration_seconds,
            bbox_json=json.dumps(event.bbox) if event.bbox else None,
            status="PENDING",
            review_status="PENDING",
            model_version=model_version,
            config_version=config_version,
            room_context=event.room_context,
            reviewer_note=event.reviewer_note,
        )
        self.db.add(db_event)
        self.db.commit()
        self.db.refresh(db_event)

        # Attach Evidence file record if available
        if event.evidence_path or event.evidence_video_path:
            evi = EvidenceFile(
                event_id=db_event.id,
                file_path=event.evidence_video_path or event.evidence_path or "",
                snapshot_path=event.evidence_path,
                video_path=event.evidence_video_path,
                file_type="video/mp4" if event.evidence_video_path else "image/jpeg",
                status="READY",
            )
            self.db.add(evi)
            self.db.commit()

        return db_event

    record_event = create_from_domain_event

    def get_by_id(self, event_pk: str) -> Optional[DetectionEvent]:
        return self.db.query(DetectionEvent).filter(DetectionEvent.id == event_pk).first()

    def get_by_event_id(self, event_id: str) -> Optional[DetectionEvent]:
        return self.db.query(DetectionEvent).filter(DetectionEvent.event_id == event_id).first()

    def list_by_session(
        self,
        session_id: str,
        severity: Optional[str] = None,
        review_status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[DetectionEvent]:
        q = self.db.query(DetectionEvent).filter(DetectionEvent.session_id == session_id)
        if severity:
            q = q.filter(DetectionEvent.severity == severity)
        if review_status:
            q = q.filter(DetectionEvent.review_status == review_status)
        return q.order_by(DetectionEvent.created_at.desc()).offset(offset).limit(limit).all()

    def list_all_filtered(
        self,
        room_id: Optional[str] = None,
        session_id: Optional[str] = None,
        seat_id: Optional[str] = None,
        severity: Optional[str] = None,
        review_status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[DetectionEvent]:
        q = self.db.query(DetectionEvent)
        if room_id:
            q = q.filter(DetectionEvent.room_id == room_id)
        if session_id:
            q = q.filter(DetectionEvent.session_id == session_id)
        if seat_id:
            q = q.filter(DetectionEvent.seat_id == seat_id)
        if severity:
            q = q.filter(DetectionEvent.severity == severity)
        if review_status:
            q = q.filter(DetectionEvent.review_status == review_status)
        return q.order_by(DetectionEvent.created_at.desc()).offset(offset).limit(limit).all()

    def update_review(
        self, event_id: str, status: str, reviewer_note: str = ""
    ) -> Optional[DetectionEvent]:
        """Update review decision on an event (backward compatible)."""
        ev = self.get_by_id(event_id) or self.get_by_event_id(event_id)
        if ev:
            ev.review_status = status.upper()
            ev.status = status.upper()
            ev.reviewer_note = reviewer_note
            self.db.commit()
            self.db.refresh(ev)
        return ev


class ReviewRepository:
    """Operations for Human-in-the-Loop Proctor Reviews."""

    def __init__(self, db: Session):
        self.db = db

    def submit_review(
        self,
        event_pk: str,
        reviewer_id: str,
        decision: str,
        reason_code: Optional[str] = None,
        note: str = "",
    ) -> EventReview:
        """Submit proctor review and update parent event review_status."""
        event = self.db.query(DetectionEvent).filter(DetectionEvent.id == event_pk).first()
        if not event:
            raise ValueError(f"Event not found with pk: {event_pk}")

        review = self.db.query(EventReview).filter(EventReview.event_id == event_pk).first()
        if review:
            review.reviewer_id = reviewer_id
            review.decision = decision
            review.reason_code = reason_code
            review.note = note
            review.reviewed_at = datetime.datetime.utcnow()
        else:
            review = EventReview(
                event_id=event_pk,
                reviewer_id=reviewer_id,
                decision=decision,
                reason_code=reason_code,
                note=note,
                reviewed_at=datetime.datetime.utcnow(),
            )
            self.db.add(review)

        event.review_status = decision
        event.status = decision
        event.reviewer_note = note
        self.db.commit()
        self.db.refresh(review)
        return review

    def get_by_event_id(self, event_pk: str) -> Optional[EventReview]:
        return self.db.query(EventReview).filter(EventReview.event_id == event_pk).first()


class WorkerRepository:
    """Operations for Inference Worker Nodes registration and heartbeats."""

    def __init__(self, db: Session):
        self.db = db

    def register(
        self,
        worker_id: str,
        hostname: str,
        gpu_name: Optional[str] = None,
        gpu_memory_mb: int = 0,
        max_active_streams: int = 10,
        version: str = "1.0.0",
    ) -> WorkerNode:
        worker = self.db.query(WorkerNode).filter(WorkerNode.id == worker_id).first()
        if worker:
            worker.hostname = hostname
            worker.gpu_name = gpu_name
            worker.gpu_memory_mb = gpu_memory_mb
            worker.max_active_streams = max_active_streams
            worker.version = version
            worker.status = "ONLINE"
            worker.last_heartbeat = datetime.datetime.utcnow()
        else:
            worker = WorkerNode(
                id=worker_id,
                hostname=hostname,
                gpu_name=gpu_name,
                gpu_memory_mb=gpu_memory_mb,
                max_active_streams=max_active_streams,
                version=version,
                status="ONLINE",
                last_heartbeat=datetime.datetime.utcnow(),
            )
            self.db.add(worker)
        self.db.commit()
        self.db.refresh(worker)
        return worker

    def heartbeat(self, worker_id: str, active_camera_count: int = 0) -> Optional[WorkerNode]:
        worker = self.db.query(WorkerNode).filter(WorkerNode.id == worker_id).first()
        if worker:
            worker.last_heartbeat = datetime.datetime.utcnow()
            worker.active_camera_count = active_camera_count
            worker.status = "ONLINE"
            self.db.commit()
            self.db.refresh(worker)
        return worker

    def list_all(self) -> List[WorkerNode]:
        return self.db.query(WorkerNode).all()

    def get_by_id(self, worker_id: str) -> Optional[WorkerNode]:
        return self.db.query(WorkerNode).filter(WorkerNode.id == worker_id).first()


class AuditLogRepository:
    """Operations for System Audit Trail."""

    def __init__(self, db: Session):
        self.db = db

    def log_action(
        self,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AuditLog:
        meta_str = json.dumps(metadata) if metadata else None
        entry = AuditLog(
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            metadata_json=meta_str,
            timestamp=datetime.datetime.utcnow(),
        )
        self.db.add(entry)
        self.db.commit()
        self.db.refresh(entry)
        return entry

    def list_recent(self, limit: int = 100) -> List[AuditLog]:
        return self.db.query(AuditLog).order_by(AuditLog.timestamp.desc()).limit(limit).all()


class StatisticsRepository:
    """Operations for System Analytics and Proctoring Dashboards."""

    def __init__(self, db: Session):
        self.db = db

    def get_overall_summary(
        self, site_id: Optional[str] = None, room_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Aggregate total events, behavior breakdown, and severity distribution."""
        q = self.db.query(DetectionEvent)
        if room_id:
            q = q.filter(DetectionEvent.room_id == room_id)

        total_events = q.count()

        # Group by behavior
        beh_counts = (
            self.db.query(DetectionEvent.behavior, func.count(DetectionEvent.id))
            .group_by(DetectionEvent.behavior)
            .all()
        )
        events_by_behavior = {b: cnt for b, cnt in beh_counts if b}

        # Group by severity
        sev_counts = (
            self.db.query(DetectionEvent.severity, func.count(DetectionEvent.id))
            .group_by(DetectionEvent.severity)
            .all()
        )
        events_by_severity = {s: cnt for s, cnt in sev_counts if s}

        active_rooms = self.db.query(ExamRoom).filter(ExamRoom.is_active.is_(True)).count()
        completed_sessions = (
            self.db.query(ExamSession).filter(ExamSession.status == "COMPLETED").count()
        )
        unreviewed = (
            self.db.query(DetectionEvent)
            .filter(DetectionEvent.review_status.in_(["PENDING", "pending", "suspicious"]))
            .count()
        )

        return {
            "total_events": total_events,
            "events_by_behavior": events_by_behavior,
            "events_by_severity": events_by_severity,
            "active_rooms": active_rooms,
            "completed_sessions": completed_sessions,
            "unreviewed_events_count": unreviewed,
        }

    def get_room_rankings(self, site_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Rank classrooms by highest risk scores."""
        rooms = self.db.query(ExamRoom).all()
        rankings = []
        for r in rooms:
            evt_count = (
                self.db.query(DetectionEvent).filter(DetectionEvent.room_id == r.id).count()
            )
            site_name = r.site.name if r.site else "Main Campus"
            rankings.append({
                "room_id": r.id,
                "room_name": r.name,
                "site_name": site_name,
                "risk_score": min(100, evt_count * 15),
                "total_events": evt_count,
                "status": r.status or "active",
            })
        rankings.sort(key=lambda x: x["risk_score"], reverse=True)
        return rankings

