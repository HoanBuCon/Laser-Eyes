"""VIGIL One on the shared server: paired desktops, their sessions and signals.

VIGIL One keeps its own perception on the candidate's computer and pushes what
it records here, so proctors review One and Classroom in the same queue,
reports and video wall.  Everything a client sends is idempotent: a session by
``client_session_id``, an event by ``event_id``, a bookmark by ``request_id``.
The client may resend after a network drop without creating duplicates.

Vocabulary mapping (One -> server) is fixed in ``EVENT_SIGNALS`` and
``SEVERITY_MAP``; signals stay observations for review, never verdicts.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import secrets
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from proctor_support.frame_store import CapturedFrame
from storage.db_models import (
    AuditLog,
    ClientDevice,
    DetectionEvent,
    EvidenceFile,
    Exam,
    ExamRoom,
    ExamSession,
    PairingCode,
    ProctorBookmark,
)
from storage.proctor_service import create_bookmark
from storage.session_lifecycle import _summarise

SOURCE_PRODUCT = "VIGIL_ONE"
ONE_ROOM_CODE = "VIGIL-ONE"
PAIRING_TTL_SECONDS = 600
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_EVENTS_PER_BATCH = 100
# Unambiguous characters for a code read off a screen and typed by hand
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

# One event type -> (server signal, category).  Technical conditions are kept
# apart from behaviour so they never count as review signals in KPIs.
EVENT_SIGNALS: dict[str, tuple[str, str]] = {
    "look_away": ("ONE_LOOK_AWAY", "SUSPICIOUS_BEHAVIOR"),
    "head_turn": ("ONE_HEAD_TURN", "SUSPICIOUS_BEHAVIOR"),
    "talking": ("ONE_TALKING", "SUSPICIOUS_BEHAVIOR"),
    "no_face": ("ONE_NO_FACE", "SUSPICIOUS_BEHAVIOR"),
    "multiple_faces": ("ONE_MULTIPLE_FACES", "SUSPICIOUS_BEHAVIOR"),
    "suspicious_object": ("ONE_SUSPICIOUS_OBJECT", "SUSPICIOUS_BEHAVIOR"),
    "low_light": ("ONE_LOW_LIGHT", "SYSTEM_CONDITION"),
    "camera_interrupted": ("ONE_CAMERA_INTERRUPTED", "SYSTEM_CONDITION"),
    # Reported by the candidate's computer about its own set-up (online exams)
    "virtual_camera": ("ONE_VIRTUAL_CAMERA", "INTEGRITY"),
    "multiple_displays": ("ONE_MULTIPLE_DISPLAYS", "INTEGRITY"),
}
SEVERITY_MAP = {"info": "LOW", "low": "LOW", "medium": "MEDIUM", "high": "HIGH"}
# Review priority (0-100) by severity: an ordering for the queue, not a probability
PRIORITY_BY_SEVERITY = {"info": 10, "low": 25, "medium": 50, "high": 80}


class OneError(RuntimeError):
    """A request that cannot be accepted (bad input, wrong device, closed session)."""


class OneNotFound(OneError):
    pass


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now() -> datetime.datetime:
    return datetime.datetime.utcnow()


def one_evidence_root() -> Path:
    """Where AI-signal snapshots uploaded by VIGIL One are kept."""
    return Path(os.getenv("VIGIL_ONE_EVIDENCE_ROOT", "data/one_evidence")).resolve()


def _audit(db: Session, actor: str, action: str, resource_type: str, resource_id: str, meta: dict[str, Any]) -> None:
    db.add(
        AuditLog(
            actor_id=actor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            metadata_json=json.dumps(meta, ensure_ascii=False),
        )
    )


def _parse_time(value: Any) -> Optional[datetime.datetime]:
    """ISO-8601 from the client (local time with offset, or naive) -> naive UTC."""
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return parsed


# ---------------------------------------------------------------- pairing and devices

def create_pairing_code(db: Session, created_by: str) -> tuple[str, datetime.datetime]:
    """A one-time 6-character code, valid for 10 minutes. Only its hash is stored."""
    code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))
    expires = _now() + datetime.timedelta(seconds=PAIRING_TTL_SECONDS)
    db.add(PairingCode(code_hash=_hash(code), created_by=created_by, expires_at=expires))
    _audit(db, created_by, "CREATE_PAIRING_CODE", "PAIRING_CODE", "-", {"expires_at": expires.isoformat()})
    db.commit()
    return code, expires


def pair_device(db: Session, code: str, name: str, hostname: str = "", app_version: str = "") -> tuple[ClientDevice, str]:
    """Exchange a pairing code for a device token (returned once, stored hashed)."""
    normalized = (code or "").strip().upper().replace(" ", "").replace("-", "")
    row = db.query(PairingCode).filter(PairingCode.code_hash == _hash(normalized)).first()
    if row is None or row.used_at is not None or row.expires_at < _now():
        raise OneError("Mã ghép nối không đúng hoặc đã hết hạn.")
    token = secrets.token_urlsafe(32)
    device = ClientDevice(
        name=(name or hostname or "VIGIL One").strip()[:120],
        hostname=(hostname or "")[:255],
        app_version=(app_version or "")[:50],
        token_hash=_hash(token),
        paired_by=row.created_by,
        last_seen_at=_now(),
    )
    db.add(device)
    db.flush()
    row.used_at = _now()
    row.device_id = device.id
    _audit(db, row.created_by or "system", "PAIR_ONE_DEVICE", "CLIENT_DEVICE", device.id, {"name": device.name, "hostname": device.hostname})
    db.commit()
    db.refresh(device)
    return device, token


def device_for_token(db: Session, token: Optional[str]) -> Optional[ClientDevice]:
    if not token:
        return None
    device = db.query(ClientDevice).filter(ClientDevice.token_hash == _hash(token)).first()
    if device is None or device.revoked_at is not None:
        return None
    if device.expires_at is not None and device.expires_at < _now():
        return None  # candidate tokens last for the exam day only
    now = _now()
    if device.last_seen_at is None or (now - device.last_seen_at).total_seconds() > 30:
        device.last_seen_at = now
        db.commit()
    return device


def revoke_device(db: Session, device_id: str, actor: str) -> ClientDevice:
    device = db.get(ClientDevice, device_id)
    if device is None:
        raise OneNotFound("Thiết bị không tồn tại.")
    if device.revoked_at is None:
        device.revoked_at = _now()
        _audit(db, actor, "REVOKE_ONE_DEVICE", "CLIENT_DEVICE", device.id, {"name": device.name})
        db.commit()
    return device


def serialize_device(device: ClientDevice) -> dict[str, Any]:
    return {
        "id": device.id,
        "name": device.name,
        "hostname": device.hostname,
        "app_version": device.app_version,
        "paired_by": device.paired_by,
        "paired_at": device.paired_at.isoformat() if device.paired_at else None,
        "last_seen_at": device.last_seen_at.isoformat() if device.last_seen_at else None,
        "revoked": device.revoked_at is not None,
    }


# ---------------------------------------------------------------- sessions

def ensure_one_room(db: Session) -> ExamRoom:
    """The virtual room every VIGIL One candidate session belongs to."""
    room = db.query(ExamRoom).filter(ExamRoom.room_code == ONE_ROOM_CODE).first()
    if room is None:
        room = ExamRoom(
            room_code=ONE_ROOM_CODE,
            name="VIGIL One — thí sinh trên máy tính",
            capacity=0,
            description="Phòng ảo: mỗi phiên là một thí sinh dùng VIGIL One trên máy tính có webcam.",
        )
        db.add(room)
        db.commit()
        db.refresh(room)
    return room


def open_exams(db: Session) -> list[dict[str, Any]]:
    rows = db.query(Exam).filter(Exam.status == "ACTIVE").order_by(Exam.created_at.desc()).limit(50).all()
    return [{"id": e.id, "name": e.name, "subject_code": e.subject_code, "exam_date": e.exam_date} for e in rows]


def owned_session(db: Session, device: ClientDevice, session_id: str) -> ExamSession:
    session = db.get(ExamSession, session_id)
    if session is None or session.product != "ONE":
        raise OneNotFound("Phiên không tồn tại.")
    if session.device_id != device.id:
        raise OneError("Phiên này thuộc một thiết bị khác.")
    return session


def open_session(db: Session, device: ClientDevice, payload: dict[str, Any]) -> tuple[ExamSession, bool]:
    """Create the session for one candidate, or return it if the client resends."""
    client_session_id = str(payload.get("client_session_id") or "").strip()[:64]
    if not client_session_id:
        raise OneError("Thiếu client_session_id.")
    existing = db.query(ExamSession).filter(ExamSession.client_session_id == client_session_id).first()
    if existing is not None:
        if existing.device_id != device.id:
            raise OneError("Phiên này thuộc một thiết bị khác.")
        return existing, False
    exam = None
    if payload.get("exam_id"):
        exam = db.get(Exam, payload["exam_id"])
        if exam is None:
            raise OneNotFound("Kỳ thi không tồn tại.")
    candidate = str(payload.get("candidate_ref") or "").strip()[:100] or "UNKNOWN"
    exam_name = str(payload.get("exam_name") or "").strip() or (exam.name if exam else "Phiên VIGIL One")
    session = ExamSession(
        room_id=ensure_one_room(db).id,
        exam_name=f"{exam_name} — {candidate}"[:255],
        subject_code=exam.subject_code if exam else None,
        exam_id=exam.id if exam else None,
        started_at=_parse_time(payload.get("started_at")) or _now(),
        status="RUNNING",
        run_id=f"ONE-{client_session_id}"[:100],
        source_preset=str(payload.get("source_kind") or "CAMERA")[:50],
        product="ONE",
        subject_ref=candidate,
        subject_name=(str(payload.get("candidate_name") or "").strip()[:255] or None),
        device_id=device.id,
        client_session_id=client_session_id,
        simulation=bool(payload.get("simulation")),
        last_heartbeat_at=_now(),
    )
    db.add(session)
    _audit(db, f"device:{device.name}", "OPEN_ONE_SESSION", "EXAM_SESSION", client_session_id,
           {"candidate": candidate, "exam_id": session.exam_id, "simulation": session.simulation})
    db.commit()
    db.refresh(session)
    return session, True


def _require_open(session: ExamSession) -> None:
    if session.status == "CLOSED":
        raise OneError("Ca thi đã đóng: không nhận thêm dữ liệu.")


# ---------------------------------------------------------------- events and evidence

def ingest_events(db: Session, session: ExamSession, events: list[dict[str, Any]]) -> dict[str, int]:
    """Store a batch of One signals as review incidents. Duplicates are skipped."""
    _require_open(session)
    if len(events) > MAX_EVENTS_PER_BATCH:
        raise OneError(f"Tối đa {MAX_EVENTS_PER_BATCH} sự kiện mỗi lần gửi.")
    created = duplicate = 0
    for item in events:
        event_id = str(item.get("event_id") or "").strip()[:36]
        event_type = str(item.get("event_type") or "").strip().lower()
        if not event_id or event_type not in EVENT_SIGNALS:
            raise OneError(f"Sự kiện không hợp lệ: {event_id or '(thiếu event_id)'} / {event_type or '(thiếu loại)'}")
        if db.query(DetectionEvent.id).filter(DetectionEvent.event_id == event_id).first() is not None:
            duplicate += 1
            continue
        signal, category = EVENT_SIGNALS[event_type]
        severity_in = str(item.get("severity") or "medium").lower()
        confidence = max(0.0, min(1.0, float(item.get("confidence") or 0.0)))
        elapsed_ms = item.get("session_elapsed_ms")
        duration = max(0.0, float(item.get("duration_seconds") or 0.0))
        meta = {
            "label": item.get("label"),
            "reason": item.get("reason") or "",
            "first_seen_ms": elapsed_ms,
            "behavior_start_ms": elapsed_ms,
            "last_seen_ms": (elapsed_ms + duration * 1000.0) if isinstance(elapsed_ms, (int, float)) else None,
            "occurrence_count": 1,
            "capability_health": item.get("capability_health") or {},
            "supporting_cues": [item.get("reason")] if item.get("reason") else [],
            "one_event_type": event_type,
            "one_severity": severity_in,
        }
        db.add(
            DetectionEvent(
                session_id=session.id,
                room_id=session.room_id,
                event_id=event_id,
                event_type=category,
                primary_signal=signal,
                behavior=signal,
                observation_quality=confidence,
                severity=SEVERITY_MAP.get(severity_in, "MEDIUM"),
                risk_score=PRIORITY_BY_SEVERITY.get(severity_in, 50),
                confidence_avg=confidence,
                confidence_peak=confidence,
                start_timestamp=_parse_time(item.get("started_at")),
                peak_timestamp=_parse_time(item.get("started_at")),
                end_timestamp=_parse_time(item.get("ended_at")),
                duration_seconds=duration,
                status="FLAGGED_FOR_HUMAN_REVIEW",
                review_status="PENDING",
                model_version="vigil-one-mediapipe",
                config_version=str(item.get("config_version") or "vigil-one-v2")[:50],
                incident_metadata_json=json.dumps(meta, ensure_ascii=False),
                source_product=SOURCE_PRODUCT,
                subject_ref=session.subject_ref,
            )
        )
        created += 1
    if created:
        db.flush()
        _summarise(db, session)
    db.commit()
    return {"created": created, "duplicate": duplicate}


def _check_jpeg(data: bytes, sha256: Optional[str]) -> str:
    if not data:
        raise OneError("Ảnh rỗng.")
    if len(data) > MAX_IMAGE_BYTES:
        raise OneError("Ảnh vượt quá 5 MB.")
    if not data.startswith(b"\xff\xd8"):
        raise OneError("Chỉ nhận ảnh JPEG.")
    digest = hashlib.sha256(data).hexdigest()
    if sha256 and sha256.strip().lower() != digest:
        raise OneError("SHA-256 không khớp với nội dung ảnh.")
    return digest


def attach_evidence(db: Session, device: ClientDevice, event_id: str, data: bytes, sha256: Optional[str]) -> EvidenceFile:
    """The JPEG snapshot of a One signal. Resending the same image is a no-op."""
    event = db.query(DetectionEvent).filter(DetectionEvent.event_id == event_id).first()
    if event is None or event.source_product != SOURCE_PRODUCT:
        raise OneNotFound("Sự kiện không tồn tại.")
    owned_session(db, device, event.session_id)
    digest = _check_jpeg(data, sha256)
    existing = db.query(EvidenceFile).filter(EvidenceFile.event_id == event.id).first()
    if existing is not None and existing.status == "READY":
        if existing.video_sha256 != digest:
            raise OneError("Sự kiện này đã có ảnh bằng chứng khác.")
        return existing
    folder = one_evidence_root() / event.session_id
    folder.mkdir(parents=True, exist_ok=True)
    final_path = folder / f"{event.event_id}.jpg"
    temp_path = folder / f".{event.event_id}.{uuid.uuid4().hex}.tmp"
    temp_path.write_bytes(data)
    os.replace(temp_path, final_path)
    evidence = existing or EvidenceFile(event_id=event.id, file_path=str(final_path))
    evidence.file_path = str(final_path)
    evidence.snapshot_path = str(final_path)
    evidence.video_path = None
    evidence.video_sha256 = digest  # the one integrity digest column the review UI reads
    evidence.file_type = "image/jpeg"
    evidence.file_size_bytes = len(data)
    evidence.status = "READY"
    evidence.error_message = None
    db.add(evidence)
    db.commit()
    db.refresh(evidence)
    return evidence


def create_one_bookmark(
    db: Session,
    session: ExamSession,
    *,
    request_id: str,
    frame_id: int,
    session_elapsed_ms: Optional[float],
    captured_at: Optional[str],
    note: str,
    created_by: str,
    data: bytes,
    sha256: Optional[str],
    width: int = 0,
    height: int = 0,
    source_kind: str = "CAMERA",
) -> tuple[ProctorBookmark, bool]:
    """A frame the person at the One computer marked by hand (not an AI signal)."""
    _require_open(session)
    request_id = (request_id or "").strip()[:64]
    if not request_id:
        raise OneError("Thiếu request_id.")
    existing = db.query(ProctorBookmark).filter(ProctorBookmark.request_id == request_id).first()
    if existing is not None:
        if existing.session_id != session.id:
            raise OneError("request_id đã được dùng cho phiên khác.")
        return existing, False
    _check_jpeg(data, sha256)
    when = _parse_time(captured_at) or _now()
    captured = CapturedFrame(
        capture_id=f"one-{request_id}",
        run_id=session.run_id or f"ONE-{session.id}",
        session_id=session.id,
        source_product=SOURCE_PRODUCT,
        source_kind=(source_kind or "CAMERA")[:30],
        source_ref=f"VIGIL One / {session.subject_ref or ''}",
        frame_id=int(frame_id or 0),
        source_timestamp_ms=float(session_elapsed_ms) if session_elapsed_ms is not None else None,
        captured_at_epoch=when.replace(tzinfo=datetime.timezone.utc).timestamp(),
        width=int(width or 0),
        height=int(height or 0),
        jpeg_bytes=data,
        coordinate_space={"name": "PREPARED_SOURCE_FRAME_PIXELS", "origin": "TOP_LEFT", "width": int(width or 0), "height": int(height or 0)},
    )
    return create_bookmark(
        db,
        captured=captured,
        request_id=request_id,
        subject_ref=session.subject_ref,
        note=note,
        created_by=created_by,
        simulation=bool(session.simulation),
    )


# ---------------------------------------------------------------- live status

_LIVE_LOCK = threading.Lock()
_LIVE_THUMBS: dict[str, tuple[bytes, float]] = {}  # session id -> (latest JPEG, epoch); memory only
STALE_AFTER_SECONDS = 10.0


def heartbeat(db: Session, session: ExamSession, status: dict[str, Any], thumbnail: Optional[bytes]) -> dict[str, Any]:
    """Latest state of a running One session. The thumbnail is never written to disk."""
    clean = {
        key: status.get(key)
        for key in ("fps", "eye", "head", "people", "alerts", "risk_score", "calibrated", "capability_health",
                    "source_kind", "pending_sync", "frame_count")
        if key in status
    }
    session.last_heartbeat_at = _now()
    session.live_status_json = json.dumps(clean, ensure_ascii=False)
    if session.status in ("INTERRUPTED",):
        session.status = "RUNNING"  # the client is back
    db.commit()
    if thumbnail:
        try:
            _check_jpeg(thumbnail, None)
        except OneError:
            thumbnail = None
    if thumbnail:
        with _LIVE_LOCK:
            _LIVE_THUMBS[session.id] = (thumbnail, time.time())
    return clean


def live_thumbnail(session_id: str) -> Optional[bytes]:
    with _LIVE_LOCK:
        item = _LIVE_THUMBS.get(session_id)
    if item is None or time.time() - item[1] > STALE_AFTER_SECONDS * 3:
        return None
    return item[0]


def stop_session(db: Session, session: ExamSession, payload: dict[str, Any]) -> ExamSession:
    """The candidate's session ended on the One computer."""
    if session.status in ("COMPLETED", "CLOSED"):
        return session
    session.status = "COMPLETED"
    session.ended_at = _parse_time(payload.get("ended_at")) or _now()
    session.total_frames = int(payload.get("frame_count") or session.total_frames or 0)
    session.avg_fps = float(payload.get("average_fps") or session.avg_fps or 0.0)
    _summarise(db, session)
    with _LIVE_LOCK:
        _LIVE_THUMBS.pop(session.id, None)
    _audit(db, "system", "STOP_ONE_SESSION", "EXAM_SESSION", session.id, {"frames": session.total_frames})
    db.commit()
    db.refresh(session)
    return session


def serialize_live(session: ExamSession) -> dict[str, Any]:
    try:
        live = json.loads(session.live_status_json or "{}")
    except (TypeError, json.JSONDecodeError):
        live = {}
    age = (_now() - session.last_heartbeat_at).total_seconds() if session.last_heartbeat_at else None
    connected = session.status == "RUNNING" and age is not None and age <= STALE_AFTER_SECONDS
    return {
        "session_id": session.id,
        "exam_id": session.exam_id,
        "candidate_id": session.candidate_id,
        "exam_name": session.exam_name,
        "candidate_ref": session.subject_ref,
        "candidate_name": session.subject_name,
        "status": session.status,
        "connection": "LIVE" if connected else ("ENDED" if session.status not in ("RUNNING", "STOPPING") else "LOST"),
        "simulation": bool(session.simulation),
        "last_heartbeat_at": session.last_heartbeat_at.isoformat() if session.last_heartbeat_at else None,
        "seconds_since_heartbeat": round(age, 1) if age is not None else None,
        "total_events": session.total_events or 0,
        "risk_score": session.risk_score or 0,
        "started_at": session.started_at.isoformat() if session.started_at else None,
        "live": live,
        "thumbnail_url": f"/api/v1/one/sessions/{session.id}/thumbnail",
    }
