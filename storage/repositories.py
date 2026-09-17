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
    DatasetCollection,
    DatasetItem,
    DatasetVersion,
    DetectionEvent,
    EventReview,
    EvidenceFile,
    ExamRoom,
    ExamSession,
    ExamSite,
    ImageAnnotationRevision,
    MediaAsset,
    SeatROI,
    StagedRecordingSession,
    StagedScenarioChecklist,
    TemporalEpisodeAnnotation,
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

    def update(
        self,
        seat_id: str,
        seat_code: Optional[str] = None,
        seat_label: Optional[str] = None,
        polygon_json: Optional[str | List[List[float]]] = None,
        enabled: Optional[bool] = None,
    ) -> Optional[SeatROI]:
        seat = self.get_by_id(seat_id)
        if not seat:
            return None
        if seat_code is not None:
            seat.seat_code = seat_code
        if seat_label is not None:
            seat.seat_label = seat_label
        if polygon_json is not None:
            seat.polygon_json = polygon_json if isinstance(polygon_json, str) else json.dumps(polygon_json)
        if enabled is not None:
            seat.enabled = enabled
        seat.updated_at = datetime.datetime.utcnow()
        self.db.commit()
        self.db.refresh(seat)
        return seat

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


# ==============================================================================
# WORKBENCH REPOSITORIES (Sprint 2 - Data Operations & Quality Validation)
# ==============================================================================

class MediaAssetRepository:
    """Operations for raw media assets (images, videos) indexed non-destructively."""

    def __init__(self, db: Session):
        self.db = db

    def create(
        self,
        file_path: str,
        relative_path: str,
        file_name: str,
        asset_type: str = "IMAGE",
        original_split: Optional[str] = None,
        width: int = 0,
        height: int = 0,
        fps: float = 0.0,
        total_frames: int = 0,
        duration_seconds: float = 0.0,
        sha256_hash: Optional[str] = None,
        annotations_count: int = 0,
        metadata_json: Optional[str] = None,
    ) -> MediaAsset:
        asset = MediaAsset(
            asset_type=asset_type,
            file_path=file_path,
            relative_path=relative_path,
            file_name=file_name,
            original_split=original_split,
            width=width,
            height=height,
            fps=fps,
            total_frames=total_frames,
            duration_seconds=duration_seconds,
            sha256_hash=sha256_hash,
            annotations_count=annotations_count,
            metadata_json=metadata_json,
        )
        self.db.add(asset)
        self.db.commit()
        self.db.refresh(asset)
        return asset

    def get_by_id(self, asset_id: str) -> Optional[MediaAsset]:
        return self.db.query(MediaAsset).filter(MediaAsset.id == asset_id).first()

    def get_by_relative_path(self, relative_path: str) -> Optional[MediaAsset]:
        return self.db.query(MediaAsset).filter(MediaAsset.relative_path == relative_path).first()

    def list_all(
        self,
        asset_type: Optional[str] = None,
        original_split: Optional[str] = None,
        audit_status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[MediaAsset]:
        q = self.db.query(MediaAsset)
        if asset_type:
            q = q.filter(MediaAsset.asset_type == asset_type)
        if original_split:
            q = q.filter(MediaAsset.original_split == original_split)
        if audit_status:
            q = q.filter(MediaAsset.audit_status == audit_status)
        return q.order_by(MediaAsset.created_at.asc()).offset(offset).limit(limit).all()

    def count(
        self,
        asset_type: Optional[str] = None,
        original_split: Optional[str] = None,
        audit_status: Optional[str] = None,
    ) -> int:
        q = self.db.query(MediaAsset)
        if asset_type:
            q = q.filter(MediaAsset.asset_type == asset_type)
        if original_split:
            q = q.filter(MediaAsset.original_split == original_split)
        if audit_status:
            q = q.filter(MediaAsset.audit_status == audit_status)
        return q.count()

    def update_audit_status(self, asset_id: str, audit_status: str) -> Optional[MediaAsset]:
        asset = self.get_by_id(asset_id)
        if asset:
            asset.audit_status = audit_status
            asset.updated_at = datetime.datetime.utcnow()
            self.db.commit()
            self.db.refresh(asset)
        return asset

    def get_summary(self) -> Dict[str, Any]:
        """Aggregate audit metrics, dataset status, and progress statistics."""
        total_images = self.count(asset_type="IMAGE")
        total_videos = self.count(asset_type="VIDEO")
        audited_images = self.count(asset_type="IMAGE", audit_status="AUDITED")
        flagged_images = self.count(asset_type="IMAGE", audit_status="FLAGGED")
        unaudited_images = self.count(asset_type="IMAGE", audit_status="UNAUDITED")

        # Split breakdown
        splits = {}
        for split_name in ["train", "val", "test", "demo", "staged"]:
            cnt = self.count(original_split=split_name)
            if cnt > 0:
                splits[split_name] = cnt

        # Total revisions
        total_revisions = self.db.query(ImageAnnotationRevision).count()
        ambiguous_count = self.db.query(ImageAnnotationRevision).filter(ImageAnnotationRevision.is_ambiguous.is_(True)).count()
        total_episodes = self.db.query(TemporalEpisodeAnnotation).count()
        human_episodes = self.db.query(TemporalEpisodeAnnotation).filter(TemporalEpisodeAnnotation.is_ai_proposal.is_(False)).count()
        ai_proposals = self.db.query(TemporalEpisodeAnnotation).filter(TemporalEpisodeAnnotation.is_ai_proposal.is_(True)).count()
        total_versions = self.db.query(DatasetVersion).count()

        audit_percentage = round((audited_images / total_images * 100.0), 1) if total_images > 0 else 0.0

        return {
            "total_images": total_images,
            "total_videos": total_videos,
            "audited_images": audited_images,
            "flagged_images": flagged_images,
            "unaudited_images": unaudited_images,
            "audit_percentage": audit_percentage,
            "splits": splits,
            "total_revisions": total_revisions,
            "ambiguous_annotations_count": ambiguous_count,
            "total_episodes": total_episodes,
            "human_episodes_count": human_episodes,
            "ai_proposals_count": ai_proposals,
            "dataset_versions_count": total_versions,
        }


class ImageRevisionRepository:
    """Operations for non-destructive human review/corrections on bounding box datasets."""

    def __init__(self, db: Session):
        self.db = db

    def save_revision(
        self,
        asset_id: str,
        bbox_index: int,
        original_class: str,
        reviewed_class: str,
        bbox_json: str | List[float],
        is_ambiguous: bool = False,
        is_rejected: bool = False,
        posture_tags_json: Optional[str | List[str]] = None,
        audit_notes: Optional[str] = None,
        reviewer_id: str = "annotator",
    ) -> ImageAnnotationRevision:
        bbox_str = bbox_json if isinstance(bbox_json, str) else json.dumps(bbox_json)
        tags_str = posture_tags_json if isinstance(posture_tags_json, str) or posture_tags_json is None else json.dumps(posture_tags_json)

        # Check existing revision for this bbox index
        existing = (
            self.db.query(ImageAnnotationRevision)
            .filter(
                ImageAnnotationRevision.asset_id == asset_id,
                ImageAnnotationRevision.bbox_index == bbox_index,
            )
            .first()
        )
        if existing:
            existing.reviewed_class = reviewed_class
            existing.bbox_json = bbox_str
            existing.is_ambiguous = is_ambiguous
            existing.is_rejected = is_rejected
            existing.posture_tags_json = tags_str
            existing.audit_notes = audit_notes
            existing.reviewer_id = reviewer_id
            existing.reviewed_at = datetime.datetime.utcnow()
            revision = existing
        else:
            revision = ImageAnnotationRevision(
                asset_id=asset_id,
                bbox_index=bbox_index,
                original_class=original_class,
                reviewed_class=reviewed_class,
                bbox_json=bbox_str,
                is_ambiguous=is_ambiguous,
                is_rejected=is_rejected,
                posture_tags_json=tags_str,
                audit_notes=audit_notes,
                reviewer_id=reviewer_id,
                reviewed_at=datetime.datetime.utcnow(),
            )
            self.db.add(revision)

        # Mark parent asset as AUDITED
        asset = self.db.query(MediaAsset).filter(MediaAsset.id == asset_id).first()
        if asset:
            asset.audit_status = "FLAGGED" if is_ambiguous or is_rejected else "AUDITED"
            asset.updated_at = datetime.datetime.utcnow()

        self.db.commit()
        self.db.refresh(revision)
        return revision

    def list_by_asset(self, asset_id: str) -> List[ImageAnnotationRevision]:
        return (
            self.db.query(ImageAnnotationRevision)
            .filter(ImageAnnotationRevision.asset_id == asset_id)
            .order_by(ImageAnnotationRevision.bbox_index.asc())
            .all()
        )

    def list_all(self, limit: int = 100, offset: int = 0) -> List[ImageAnnotationRevision]:
        return (
            self.db.query(ImageAnnotationRevision)
            .order_by(ImageAnnotationRevision.reviewed_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )


class TemporalEpisodeRepository:
    """Operations for millisecond ground-truth temporal episodes and AI comparisons."""

    def __init__(self, db: Session):
        self.db = db

    def create(
        self,
        asset_id: str,
        episode_type: str,
        start_ms: float,
        peak_ms: float,
        end_ms: float,
        seat_id: Optional[str] = None,
        seat_code: Optional[str] = None,
        target_neighbor_id: Optional[str] = None,
        confidence: float = 1.0,
        is_ai_proposal: bool = False,
        ai_match_iou: float = 0.0,
        reviewer_id: str = "annotator",
        review_status: str = "ACCEPTED",
        notes: Optional[str] = None,
    ) -> TemporalEpisodeAnnotation:
        duration_ms = max(0.0, end_ms - start_ms)
        episode = TemporalEpisodeAnnotation(
            asset_id=asset_id,
            seat_id=seat_id,
            seat_code=seat_code,
            episode_type=episode_type,
            start_ms=start_ms,
            peak_ms=peak_ms,
            end_ms=end_ms,
            duration_ms=duration_ms,
            target_neighbor_id=target_neighbor_id,
            confidence=confidence,
            is_ai_proposal=is_ai_proposal,
            ai_match_iou=ai_match_iou,
            reviewer_id=reviewer_id,
            review_status=review_status,
            notes=notes,
        )
        self.db.add(episode)
        self.db.commit()
        self.db.refresh(episode)
        return episode

    def get_by_id(self, episode_id: str) -> Optional[TemporalEpisodeAnnotation]:
        return (
            self.db.query(TemporalEpisodeAnnotation)
            .filter(TemporalEpisodeAnnotation.id == episode_id)
            .first()
        )

    def list_by_asset(
        self,
        asset_id: str,
        seat_id: Optional[str] = None,
        is_ai_proposal: Optional[bool] = None,
    ) -> List[TemporalEpisodeAnnotation]:
        q = self.db.query(TemporalEpisodeAnnotation).filter(TemporalEpisodeAnnotation.asset_id == asset_id)
        if seat_id:
            q = q.filter(TemporalEpisodeAnnotation.seat_id == seat_id)
        if is_ai_proposal is not None:
            q = q.filter(TemporalEpisodeAnnotation.is_ai_proposal == is_ai_proposal)
        return q.order_by(TemporalEpisodeAnnotation.start_ms.asc()).all()

    def update(self, episode_id: str, **kwargs) -> Optional[TemporalEpisodeAnnotation]:
        ep = self.get_by_id(episode_id)
        if ep:
            for k, v in kwargs.items():
                if hasattr(ep, k) and v is not None:
                    setattr(ep, k, v)
            if ep.end_ms and ep.start_ms:
                ep.duration_ms = max(0.0, ep.end_ms - ep.start_ms)
            ep.updated_at = datetime.datetime.utcnow()
            self.db.commit()
            self.db.refresh(ep)
        return ep

    def delete(self, episode_id: str) -> bool:
        ep = self.get_by_id(episode_id)
        if ep:
            self.db.delete(ep)
            self.db.commit()
            return True
        return False

    def compare_human_vs_ai(self, asset_id: str, iou_threshold: float = 0.30) -> Dict[str, Any]:
        """Compute Temporal IoU matching, precision, recall, and overlap between Human and AI proposals."""
        human_eps = (
            self.db.query(TemporalEpisodeAnnotation)
            .filter(
                TemporalEpisodeAnnotation.asset_id == asset_id,
                TemporalEpisodeAnnotation.is_ai_proposal.is_(False),
                TemporalEpisodeAnnotation.review_status != "REJECTED",
            )
            .all()
        )
        ai_eps = (
            self.db.query(TemporalEpisodeAnnotation)
            .filter(
                TemporalEpisodeAnnotation.asset_id == asset_id,
                TemporalEpisodeAnnotation.is_ai_proposal.is_(True),
            )
            .all()
        )

        matched_pairs = []
        matched_ai_ids = set()
        matched_human_ids = set()

        for h in human_eps:
            best_iou = 0.0
            best_ai = None
            for a in ai_eps:
                if a.id in matched_ai_ids:
                    continue
                # Optional: Match on seat_code if both present
                if h.seat_code and a.seat_code and h.seat_code != a.seat_code:
                    continue

                # Compute Temporal IoU
                inter = max(0.0, min(h.end_ms, a.end_ms) - max(h.start_ms, a.start_ms))
                union = max(h.end_ms, a.end_ms) - min(h.start_ms, a.start_ms)
                iou = inter / union if union > 0 else 0.0

                if iou > best_iou:
                    best_iou = iou
                    best_ai = a

            if best_iou >= iou_threshold and best_ai is not None:
                matched_ai_ids.add(best_ai.id)
                matched_human_ids.add(h.id)
                latency_ms = best_ai.start_ms - h.start_ms
                matched_pairs.append({
                    "human_episode_id": h.id,
                    "ai_episode_id": best_ai.id,
                    "seat_code": h.seat_code or best_ai.seat_code,
                    "human_type": h.episode_type,
                    "ai_type": best_ai.episode_type,
                    "human_range_ms": [h.start_ms, h.end_ms],
                    "ai_range_ms": [best_ai.start_ms, best_ai.end_ms],
                    "temporal_iou": round(best_iou, 3),
                    "start_latency_ms": round(latency_ms, 1),
                    "is_label_match": h.episode_type == best_ai.episode_type,
                })

        tp = len(matched_pairs)
        fn = len(human_eps) - len(matched_human_ids)
        fp = len(ai_eps) - len(matched_ai_ids)

        precision = tp / (tp + fp) if (tp + fp) > 0 else (1.0 if tp == 0 and len(human_eps) == 0 else 0.0)
        recall = tp / (tp + fn) if (tp + fn) > 0 else (1.0 if tp == 0 and len(human_eps) == 0 else 0.0)
        f1_score = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        avg_iou = (sum(p["temporal_iou"] for p in matched_pairs) / tp) if tp > 0 else 0.0

        return {
            "asset_id": asset_id,
            "total_human_episodes": len(human_eps),
            "total_ai_proposals": len(ai_eps),
            "true_positives": tp,
            "false_positives": fp,
            "false_negatives": fn,
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "f1_score": round(f1_score, 3),
            "average_temporal_iou": round(avg_iou, 3),
            "matched_pairs": matched_pairs,
        }


class DatasetRepository:
    """Operations for versioned ML dataset curation, manifest generation, and export."""

    def __init__(self, db: Session):
        self.db = db

    def create_collection(
        self,
        name: str,
        task_type: str = "ACTOR_CLASSIFICATION",
        description: Optional[str] = None,
        created_by: str = "engineer",
    ) -> DatasetCollection:
        collection = DatasetCollection(
            name=name,
            task_type=task_type,
            description=description,
            created_by=created_by,
        )
        self.db.add(collection)
        self.db.commit()
        self.db.refresh(collection)
        return collection

    def get_collection(self, collection_id: str) -> Optional[DatasetCollection]:
        return (
            self.db.query(DatasetCollection)
            .filter(DatasetCollection.id == collection_id)
            .first()
        )

    def list_collections(self) -> List[DatasetCollection]:
        return self.db.query(DatasetCollection).order_by(DatasetCollection.created_at.desc()).all()

    def create_version(
        self,
        collection_id: str,
        version_tag: str,
        split_strategy: str = "GROUP_BY_SESSION",
        train_ratio: float = 0.70,
        val_ratio: float = 0.15,
        test_ratio: float = 0.15,
        export_format: str = "CLASSIFICATION_CROPS",
        export_path: Optional[str] = None,
        manifest_json: Optional[str] = None,
        total_items: int = 0,
        status: str = "READY",
    ) -> DatasetVersion:
        version = DatasetVersion(
            collection_id=collection_id,
            version_tag=version_tag,
            split_strategy=split_strategy,
            train_ratio=train_ratio,
            val_ratio=val_ratio,
            test_ratio=test_ratio,
            export_format=export_format,
            export_path=export_path,
            manifest_json=manifest_json,
            total_items=total_items,
            status=status,
        )
        self.db.add(version)
        self.db.commit()
        self.db.refresh(version)
        return version

    def get_version(self, version_id: str) -> Optional[DatasetVersion]:
        return self.db.query(DatasetVersion).filter(DatasetVersion.id == version_id).first()

    def list_versions(self, collection_id: Optional[str] = None) -> List[DatasetVersion]:
        q = self.db.query(DatasetVersion)
        if collection_id:
            q = q.filter(DatasetVersion.collection_id == collection_id)
        return q.order_by(DatasetVersion.created_at.desc()).all()

    def add_item(
        self,
        version_id: str,
        split: str,
        label: str,
        asset_id: Optional[str] = None,
        relative_path: Optional[str] = None,
        metadata_json: Optional[str] = None,
    ) -> DatasetItem:
        item = DatasetItem(
            version_id=version_id,
            asset_id=asset_id,
            split=split,
            label=label,
            relative_path=relative_path,
            metadata_json=metadata_json,
        )
        self.db.add(item)
        self.db.commit()
        self.db.refresh(item)
        return item

    def list_items(self, version_id: str, split: Optional[str] = None) -> List[DatasetItem]:
        q = self.db.query(DatasetItem).filter(DatasetItem.version_id == version_id)
        if split:
            q = q.filter(DatasetItem.split == split)
        return q.all()


class StagedSessionRepository:
    """Operations for structured staged/mock exam recording sessions."""

    def __init__(self, db: Session):
        self.db = db

    def create_session(
        self,
        session_code: str,
        script_name: str,
        room_id: Optional[str] = None,
        actor_names_json: Optional[str | List[str]] = None,
        target_video_path: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> StagedRecordingSession:
        actors_str = actor_names_json if isinstance(actor_names_json, str) or actor_names_json is None else json.dumps(actor_names_json)
        sess = StagedRecordingSession(
            session_code=session_code,
            script_name=script_name,
            room_id=room_id,
            actor_names_json=actors_str,
            target_video_path=target_video_path,
            notes=notes,
            status="PLANNED",
        )
        self.db.add(sess)
        self.db.commit()
        self.db.refresh(sess)
        return sess

    def get_session(self, session_id: str) -> Optional[StagedRecordingSession]:
        return (
            self.db.query(StagedRecordingSession)
            .filter(StagedRecordingSession.id == session_id)
            .first()
        )

    def list_sessions(self) -> List[StagedRecordingSession]:
        return self.db.query(StagedRecordingSession).order_by(StagedRecordingSession.recorded_at.desc()).all()

    def add_scenario(
        self,
        session_id: str,
        scenario_code: str,
        title: str,
        expected_behavior: str,
        seat_code: Optional[str] = None,
        target_start_ms: float = 0.0,
        target_end_ms: float = 0.0,
        notes: Optional[str] = None,
    ) -> StagedScenarioChecklist:
        scen = StagedScenarioChecklist(
            session_id=session_id,
            scenario_code=scenario_code,
            title=title,
            expected_behavior=expected_behavior,
            seat_code=seat_code,
            target_start_ms=target_start_ms,
            target_end_ms=target_end_ms,
            notes=notes,
            status="PENDING",
        )
        self.db.add(scen)
        self.db.commit()
        self.db.refresh(scen)
        return scen

    def update_scenario(self, checklist_id: str, **kwargs) -> Optional[StagedScenarioChecklist]:
        scen = (
            self.db.query(StagedScenarioChecklist)
            .filter(StagedScenarioChecklist.id == checklist_id)
            .first()
        )
        if scen:
            for k, v in kwargs.items():
                if hasattr(scen, k) and v is not None:
                    setattr(scen, k, v)
            self.db.commit()
            self.db.refresh(scen)
        return scen

    def list_scenarios_by_session(self, session_id: str) -> List[StagedScenarioChecklist]:
        return (
            self.db.query(StagedScenarioChecklist)
            .filter(StagedScenarioChecklist.session_id == session_id)
            .order_by(StagedScenarioChecklist.target_start_ms.asc())
            .all()
        )


