"""Online exams with VIGIL One installed on the candidates' own computers.

A chief opens an exam for online joining (a short join code) and imports the
candidate list (code, name, PIN).  A candidate starts VIGIL One, enters the
join code, their code and PIN, and receives a token valid for that exam only;
the rest of the sync API (sessions, signals, evidence, heartbeat) is the one
paired computers already use.

The candidate's computer is not trusted to report on itself, so integrity
signals are raised here, on the server: connection lost, left before
submitting, computer changed, clock out of step.  Like every other signal they
are prompts for a proctor's review, never a verdict.
"""

from __future__ import annotations

import datetime
import json
import secrets
import string
import threading
import time
import uuid
from typing import Any, Optional

from sqlalchemy.orm import Session

from storage.auth_service import hash_password, verify_password
from storage.db_models import Candidate, ClientDevice, DetectionEvent, Exam, ExamSession
from storage import one_service as one
from storage.session_lifecycle import _summarise

JOIN_CODE_LENGTH = 7
TOKEN_HOURS = 12
OFFLINE_AFTER_SECONDS = 30
CLOCK_SKEW_SECONDS = 120
BOOST_SECONDS = 60

DEFAULT_POLICY: dict[str, Any] = {
    "microphone": True,          # voice + lip movement cue
    "allow_materials": False,    # books / notes allowed on the desk
    "video_clips": True,         # short MP4 (5 s before + 4 s after) with each signal
    "record_video": True,        # the whole exam on video, uploaded during the exam, one file per candidate
    "video_width": 640,
    "video_fps": 10,
    "live_preview": True,        # small live picture for the proctors' grid
    "live_width": 320,
    "live_interval": 5,          # seconds between live pictures
    "boost_width": 640,          # while a proctor has the candidate open
    "boost_interval": 2,
    "show_alerts_to_candidate": False,
    "thresholds": {},            # event type -> seconds; empty = the app's defaults
    # Where VIGIL One's perception runs.  "server": the candidate's computer only records and uploads
    # video, the Hub analyses it (no MediaPipe on the candidate's computer); "client": the old way,
    # kept as a fallback for a Hub that cannot keep up.
    "segment_seconds": 10,       # length of the uploaded video pieces (= delay before the Hub sees them)
    "analysis_fps": 10,          # frames per second streamed live and analysed (lips need >= 8)
}

# Integrity signals raised or confirmed by the server (category INTEGRITY)
INTEGRITY_SIGNALS = {
    "ONE_CLIENT_OFFLINE": ("MEDIUM", "Mất kết nối với máy thí sinh"),
    "ONE_LEFT_EARLY": ("HIGH", "Thoát phần mềm trước khi nộp bài"),
    "ONE_DEVICE_CHANGED": ("MEDIUM", "Vào thi lại từ máy khác"),
    "ONE_CLOCK_SKEW": ("LOW", "Đồng hồ máy thí sinh lệch so với máy chủ"),
}


class OnlineExamError(one.OneError):
    pass


def _now() -> datetime.datetime:
    return datetime.datetime.utcnow()


# ---------------------------------------------------------------- exam setup (web, chief)

def policy_for(exam: Exam) -> dict[str, Any]:
    try:
        stored = json.loads(exam.one_policy_json or "{}")
    except (TypeError, json.JSONDecodeError):
        stored = {}
    merged = {**DEFAULT_POLICY, **{k: v for k, v in stored.items() if k in DEFAULT_POLICY}}
    if stored.get("analysis_fps") == 5:
        merged["analysis_fps"] = DEFAULT_POLICY["analysis_fps"]  # the old default (too slow for lips), not a choice
    merged["thresholds"] = {str(k): float(v) for k, v in (merged.get("thresholds") or {}).items()}
    return merged


def update_policy(db: Session, exam: Exam, changes: dict[str, Any]) -> dict[str, Any]:
    current = policy_for(exam)
    for key, value in changes.items():
        if key not in DEFAULT_POLICY:
            raise OnlineExamError(f"Unknown policy setting: {key}")
        current[key] = value
    current["live_width"] = max(160, min(1280, int(current["live_width"])))
    current["live_interval"] = max(1, min(60, int(current["live_interval"])))
    current["video_width"] = max(320, min(1280, int(current["video_width"])))
    current["video_fps"] = max(2, min(30, int(current["video_fps"])))
    current["segment_seconds"] = max(4, min(60, int(current["segment_seconds"])))
    current["analysis_fps"] = max(1, min(int(current["video_fps"]), int(current["analysis_fps"])))
    current["thresholds"] = _clean_thresholds(current.get("thresholds"))
    exam.one_policy_json = json.dumps(current, ensure_ascii=False)
    db.commit()
    return current


def _clean_thresholds(value: Any) -> dict[str, float]:
    """Seconds a behaviour must last before it becomes a signal, per signal type (0.2-60 s)."""
    from exam_monitor.models import EventType

    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise OnlineExamError("thresholds must be an object {signal type: seconds}")
    known = {e.value for e in EventType}
    clean = {}
    for key, seconds in value.items():
        if key not in known:
            raise OnlineExamError(f"Unknown signal type: {key}")
        if seconds in (None, ""):
            continue  # back to the default
        clean[key] = round(max(0.2, min(60.0, float(seconds))), 1)
    return clean


def _new_join_code(db: Session) -> str:
    alphabet = "".join(c for c in string.ascii_uppercase + string.digits if c not in "O0I1L")
    for _ in range(20):
        code = "".join(secrets.choice(alphabet) for _ in range(JOIN_CODE_LENGTH))
        if db.query(Exam.id).filter(Exam.join_code == code).first() is None:
            return code
    raise OnlineExamError("Could not allocate a join code")


def set_join_code(db: Session, exam: Exam, code: str) -> Exam:
    """A join code chosen by the organiser (e.g. the course code): 3-12 letters or digits, unique."""
    clean = (code or "").strip().upper().replace(" ", "").replace("-", "")
    if not (3 <= len(clean) <= 12) or not clean.isalnum() or not clean.isascii():
        raise OnlineExamError("Join code: 3 to 12 letters or digits")
    other = db.query(Exam).filter(Exam.join_code == clean, Exam.id != exam.id).first()
    if other is not None:
        raise OnlineExamError(f"Join code {clean} is already used by the exam “{other.name}”")
    exam.join_code = clean
    db.commit()
    db.refresh(exam)
    return exam


def set_joining(db: Session, exam: Exam, open_: bool, rotate: bool = False) -> Exam:
    if exam.join_code is None or rotate:
        exam.join_code = _new_join_code(db)
    exam.join_open = bool(open_)
    db.commit()
    db.refresh(exam)
    return exam


def _generate_pin() -> str:
    return "".join(secrets.choice(string.digits) for _ in range(6))


def import_candidates(db: Session, exam: Exam, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add or update candidates. PINs are returned only for rows that got a new one."""
    result = []
    seen: set[str] = set()
    for row in rows:
        code = str(row.get("candidate_code") or "").strip()[:50]
        if not code:
            continue
        if code.upper() in seen:
            raise OnlineExamError(f"Candidate code {code} appears twice in the list")
        seen.add(code.upper())
        pin = str(row.get("pin") or "").strip()
        if pin and (len(pin) < 4 or len(pin) > 32):
            raise OnlineExamError(f"PIN of {code} must have 4 to 32 characters")
        candidate = db.query(Candidate).filter(Candidate.exam_id == exam.id, Candidate.candidate_code == code).first()
        issued = None
        if candidate is None:
            issued = pin or _generate_pin()
            candidate = Candidate(exam_id=exam.id, candidate_code=code, pin_hash=hash_password(issued))
            db.add(candidate)
        elif pin:
            candidate.pin_hash = hash_password(pin)
            issued = pin
        if row.get("full_name") is not None:
            candidate.full_name = str(row.get("full_name") or "").strip()[:255] or None
        result.append({"candidate_code": code, "full_name": candidate.full_name, "pin": issued})
    db.commit()
    return result


def remove_candidate(db: Session, exam: Exam, candidate_id: str) -> None:
    candidate = db.get(Candidate, candidate_id)
    if candidate is None or candidate.exam_id != exam.id:
        raise one.OneNotFound("Candidate not found")
    if db.query(ExamSession.id).filter(ExamSession.candidate_id == candidate.id).first() is not None:
        raise OnlineExamError("This candidate already has a session; it cannot be removed")
    db.delete(candidate)
    db.commit()


def _latest_session(db: Session, candidate: Candidate) -> Optional[ExamSession]:
    return (
        db.query(ExamSession)
        .filter(ExamSession.candidate_id == candidate.id)
        .order_by(ExamSession.started_at.desc())
        .first()
    )


def candidate_state(db: Session, candidate: Candidate) -> str:
    """NOT_JOINED / JOINED (signed in, not started) / LIVE / LOST / SUBMITTED / LEFT."""
    session = _latest_session(db, candidate)
    if session is None:
        return "JOINED" if candidate.last_join_at else "NOT_JOINED"
    if session.status in ("COMPLETED", "CLOSED"):
        left = db.query(DetectionEvent.id).filter(
            DetectionEvent.session_id == session.id, DetectionEvent.primary_signal == "ONE_LEFT_EARLY").first()
        return "LEFT" if left else "SUBMITTED"
    return one.serialize_live(session)["connection"]


def roster(db: Session, exam: Exam) -> list[dict[str, Any]]:
    rows = db.query(Candidate).filter(Candidate.exam_id == exam.id).order_by(Candidate.candidate_code).all()
    out = []
    for candidate in rows:
        session = _latest_session(db, candidate)
        out.append({
            "id": candidate.id,
            "candidate_code": candidate.candidate_code,
            "full_name": candidate.full_name,
            "consent_at": candidate.consent_at.isoformat() if candidate.consent_at else None,
            "last_join_at": candidate.last_join_at.isoformat() if candidate.last_join_at else None,
            "state": candidate_state(db, candidate),
            "session": one.serialize_live(session) if session else None,
        })
    return out


def serialize_exam(exam: Exam) -> dict[str, Any]:
    return {
        "id": exam.id,
        "name": exam.name,
        "subject_code": exam.subject_code,
        "exam_date": exam.exam_date,
        "join_code": exam.join_code,
        "join_open": bool(exam.join_open),
        "policy": policy_for(exam),
    }


# ---------------------------------------------------------------- candidate side (VIGIL One)

def join(db: Session, join_code: str, candidate_code: str, pin: str, device_info: dict[str, Any]) -> tuple[ClientDevice, str, Candidate, Exam]:
    """Check code + PIN and issue a token valid for this candidate and exam only."""
    code = (join_code or "").strip().upper().replace(" ", "").replace("-", "")
    exam = db.query(Exam).filter(Exam.join_code == code).first() if code else None
    # One message for every failure: do not reveal which part was wrong
    refused = OnlineExamError("Mã kỳ thi, mã thí sinh hoặc PIN không đúng, hoặc kỳ thi chưa mở.")
    if exam is None or not exam.join_open:
        raise refused
    candidate = (
        db.query(Candidate)
        .filter(Candidate.exam_id == exam.id, Candidate.candidate_code == (candidate_code or "").strip())
        .first()
    )
    if candidate is None or not verify_password(pin or "", candidate.pin_hash):
        raise refused
    hostname = str(device_info.get("hostname") or "")[:255]
    running = _latest_session(db, candidate)
    if running is not None and running.status in ("RUNNING", "STOPPING", "INTERRUPTED") and candidate.last_device and hostname \
            and candidate.last_device != hostname:
        raise_integrity(db, running, "ONE_DEVICE_CHANGED",
                        f"Thí sinh vào lại từ máy '{hostname}' (trước đó: '{candidate.last_device}').")
    token = secrets.token_urlsafe(32)
    device = ClientDevice(
        name=f"{candidate.candidate_code} · {candidate.full_name or ''}".strip(" ·")[:120],
        hostname=hostname,
        app_version=str(device_info.get("app_version") or "")[:50],
        token_hash=one._hash(token),
        paired_by=f"join:{exam.join_code}",
        last_seen_at=_now(),
        kind="CANDIDATE",
        candidate_id=candidate.id,
        expires_at=_now() + datetime.timedelta(hours=TOKEN_HOURS),
    )
    db.add(device)
    candidate.last_join_at = _now()
    candidate.last_device = hostname or candidate.last_device
    one._audit(db, f"candidate:{candidate.candidate_code}", "JOIN_ONLINE_EXAM", "EXAM", exam.id, {"hostname": hostname})
    db.commit()
    db.refresh(device)
    return device, token, candidate, exam


def candidate_of(db: Session, device: ClientDevice) -> Optional[Candidate]:
    return db.get(Candidate, device.candidate_id) if device.candidate_id else None


def record_consent(db: Session, device: ClientDevice) -> Candidate:
    candidate = candidate_of(db, device)
    if candidate is None:
        raise OnlineExamError("Chỉ thí sinh thi online mới cần xác nhận đồng ý.")
    candidate.consent_at = _now()
    one._audit(db, f"candidate:{candidate.candidate_code}", "CANDIDATE_CONSENT", "CANDIDATE", candidate.id, {})
    db.commit()
    return candidate


def apply_candidate_identity(db: Session, device: ClientDevice, payload: dict[str, Any]) -> dict[str, Any]:
    """A candidate token decides exam and identity itself; the client cannot pick another."""
    candidate = candidate_of(db, device)
    if candidate is None:
        return payload
    if candidate.consent_at is None:
        raise OnlineExamError("Thí sinh cần xác nhận đồng ý trước khi bắt đầu.")
    exam = db.get(Exam, candidate.exam_id)
    return {
        **payload,
        "exam_id": exam.id,
        "exam_name": exam.name,
        "candidate_ref": candidate.candidate_code,
        "candidate_name": candidate.full_name or "",
        "candidate_id": candidate.id,
    }


def after_session_opened(db: Session, device: ClientDevice, session: ExamSession, payload: dict[str, Any]) -> None:
    """Integrity checks when a candidate starts: an earlier unfinished session, a wrong clock."""
    candidate = candidate_of(db, device)
    if candidate is None:
        return
    session.candidate_id = candidate.id
    earlier = (
        db.query(ExamSession)
        .filter(ExamSession.candidate_id == candidate.id, ExamSession.id != session.id,
                ExamSession.status.in_(("RUNNING", "STOPPING", "INTERRUPTED")))
        .all()
    )
    for old in earlier:
        old.status = "INTERRUPTED"
        old.ended_at = old.ended_at or _now()
        _summarise(db, old)
    db.commit()
    client_time = one._parse_time(payload.get("client_time"))
    if client_time is not None:
        skew = (client_time - _now()).total_seconds()
        if abs(skew) > CLOCK_SKEW_SECONDS:
            raise_integrity(db, session, "ONE_CLOCK_SKEW", f"Đồng hồ máy thí sinh lệch {skew / 60:+.1f} phút so với máy chủ.")


def raise_integrity(db: Session, session: ExamSession, signal: str, reason: str, *, open_ended: bool = False) -> DetectionEvent:
    severity, label = INTEGRITY_SIGNALS[signal]
    elapsed_ms = max(0.0, (_now() - session.started_at).total_seconds() * 1000.0) if session.started_at else None
    event = DetectionEvent(
        session_id=session.id,
        room_id=session.room_id,
        event_id=str(uuid.uuid4()),
        event_type="INTEGRITY",
        primary_signal=signal,
        behavior=signal,
        severity=severity,
        risk_score=one.PRIORITY_BY_SEVERITY.get(severity.lower(), 50),
        start_timestamp=_now(),
        peak_timestamp=_now(),
        end_timestamp=None if open_ended else _now(),
        status="FLAGGED_FOR_HUMAN_REVIEW",
        review_status="PENDING",
        model_version="vigil-hub-integrity",
        config_version="online-exam-v1",
        incident_metadata_json=json.dumps({
            "label": label, "reason": reason, "first_seen_ms": elapsed_ms, "behavior_start_ms": elapsed_ms,
            "last_seen_ms": elapsed_ms, "occurrence_count": 1, "supporting_cues": [reason], "raised_by": "server",
        }, ensure_ascii=False),
        source_product=one.SOURCE_PRODUCT,
        subject_ref=session.subject_ref,
    )
    db.add(event)
    db.flush()
    _summarise(db, session)
    db.commit()
    return event


# ---------------------------------------------------------------- connection watchdog

def _open_offline(db: Session, session: ExamSession) -> Optional[DetectionEvent]:
    return (
        db.query(DetectionEvent)
        .filter(DetectionEvent.session_id == session.id, DetectionEvent.primary_signal == "ONE_CLIENT_OFFLINE",
                DetectionEvent.end_timestamp.is_(None))
        .first()
    )


def check_connections(db: Session, now: Optional[datetime.datetime] = None) -> int:
    """Open an ONE_CLIENT_OFFLINE signal for each running candidate silent for too long."""
    now = now or _now()
    limit = now - datetime.timedelta(seconds=OFFLINE_AFTER_SECONDS)
    sessions = (
        db.query(ExamSession)
        .filter(ExamSession.product == "ONE", ExamSession.status == "RUNNING",
                ExamSession.last_heartbeat_at.isnot(None), ExamSession.last_heartbeat_at < limit)
        .all()
    )
    opened = 0
    for session in sessions:
        if _open_offline(db, session) is None:
            silent = (now - session.last_heartbeat_at).total_seconds()
            raise_integrity(db, session, "ONE_CLIENT_OFFLINE",
                            f"Không nhận được tín hiệu từ máy thí sinh từ {session.last_heartbeat_at:%H:%M:%S} UTC ({silent:.0f} giây).",
                            open_ended=True)
            opened += 1
    return opened


def connection_back(db: Session, session: ExamSession) -> None:
    """Close the open offline signal (if any) with how long the computer was silent."""
    event = _open_offline(db, session)
    if event is None:
        return
    event.end_timestamp = _now()
    event.duration_seconds = (event.end_timestamp - event.start_timestamp).total_seconds() + OFFLINE_AFTER_SECONDS
    try:
        meta = json.loads(event.incident_metadata_json or "{}")
    except json.JSONDecodeError:
        meta = {}
    meta["reason"] = f"{meta.get('reason', '')} Kết nối trở lại sau khoảng {event.duration_seconds:.0f} giây."
    meta["supporting_cues"] = [meta["reason"]]
    event.incident_metadata_json = json.dumps(meta, ensure_ascii=False)
    db.commit()


def on_stop(db: Session, session: ExamSession, reason: str) -> None:
    """QUIT: the candidate closed VIGIL One without submitting."""
    if (reason or "").upper() == "QUIT" and session.candidate_id:
        raise_integrity(db, session, "ONE_LEFT_EARLY", "Thí sinh đóng VIGIL One trước khi bấm Nộp bài.")


# ---------------------------------------------------------------- live picture rate

_BOOST_LOCK = threading.Lock()
_BOOST_UNTIL: dict[str, float] = {}


def boost(session_id: str) -> None:
    with _BOOST_LOCK:
        _BOOST_UNTIL[session_id] = time.time() + BOOST_SECONDS


def live_settings(db: Session, session: ExamSession) -> dict[str, Any]:
    """What the client should send next: smaller and rarer unless a proctor is watching."""
    exam = db.get(Exam, session.exam_id) if session.exam_id else None
    policy = policy_for(exam) if exam else DEFAULT_POLICY
    with _BOOST_LOCK:
        boosted = _BOOST_UNTIL.get(session.id, 0) > time.time()
    if not policy["live_preview"]:
        return {"enabled": False}
    if boosted:
        return {"enabled": True, "width": policy["boost_width"], "interval": policy["boost_interval"], "boosted": True}
    return {"enabled": True, "width": policy["live_width"], "interval": policy["live_interval"], "boosted": False}
