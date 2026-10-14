"""VIGIL One endpoints: pairing, live candidate sessions and data sync.

Two audiences:

* ``/one/client/*`` — called by the VIGIL One desktop with its device token
  (``Authorization: Bearer <token>``).  These never use the browser sign-in,
  so they stay reachable in ``VIGIL_AUTH_MODE=required``.
* the rest — the web: pairing codes and devices (chief/admin, always signed
  in) and the live list / thumbnails of One candidates.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.auth import actor_name, require_roles
from api.realtime import realtime_manager
from storage.database import get_db
from storage.db_models import ClientDevice, ExamSession
from storage import one_service as one
from storage import online_exam_service as online
from storage.db_models import Exam
from storage.proctor_service import serialize_bookmark

router = APIRouter(prefix="/one", tags=["VIGIL One"])


# ---------------------------------------------------------------- auth helpers

def device_auth(authorization: Optional[str] = Header(None), db: Session = Depends(get_db)) -> ClientDevice:
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    device = one.device_for_token(db, token)
    if device is None:
        raise HTTPException(status_code=401, detail="Thiết bị chưa được ghép nối hoặc đã bị thu hồi.")
    return device


def _owned(db: Session, device: ClientDevice, session_id: str) -> ExamSession:
    try:
        return one.owned_session(db, device, session_id)
    except one.OneNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except one.OneError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _bad(exc: Exception) -> HTTPException:
    if isinstance(exc, one.OneNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


async def _read_image(upload: UploadFile) -> bytes:
    data = await upload.read(one.MAX_IMAGE_BYTES + 1)
    if len(data) > one.MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Ảnh vượt quá 5 MB.")
    return data


# ---------------------------------------------------------------- web: pairing and devices

@router.post("/pairing-codes", status_code=201)
def create_pairing_code(
    db: Session = Depends(get_db),
    user=Depends(require_roles("CHIEF", "ADMIN", always=True)),
) -> dict[str, Any]:
    code, expires = one.create_pairing_code(db, actor_name(user))
    return {"code": code, "expires_at": expires.isoformat() + "Z", "valid_seconds": one.PAIRING_TTL_SECONDS}


@router.get("/devices")
def list_devices(
    db: Session = Depends(get_db),
    _user=Depends(require_roles("CHIEF", "ADMIN", always=True)),
) -> list[dict[str, Any]]:
    rows = db.query(ClientDevice).order_by(ClientDevice.paired_at.desc()).all()
    return [one.serialize_device(d) for d in rows]


@router.delete("/devices/{device_id}")
def revoke_device(
    device_id: str,
    db: Session = Depends(get_db),
    user=Depends(require_roles("CHIEF", "ADMIN", always=True)),
) -> dict[str, Any]:
    try:
        return one.serialize_device(one.revoke_device(db, device_id, actor_name(user)))
    except one.OneError as exc:
        raise _bad(exc) from exc


# ---------------------------------------------------------------- web: live candidates

@router.get("/sessions")
def list_one_sessions(
    active_only: bool = True,
    exam_id: Optional[str] = None,
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    query = db.query(ExamSession).filter(ExamSession.product == "ONE")
    if active_only:
        query = query.filter(ExamSession.status.in_(("RUNNING", "STOPPING", "INTERRUPTED")))
    if exam_id:
        query = query.filter(ExamSession.exam_id == exam_id)
    rows = query.order_by(ExamSession.started_at.desc()).limit(200).all()
    return [one.serialize_live(s) for s in rows]


@router.get("/sessions/{session_id}/thumbnail")
def get_thumbnail(session_id: str) -> Response:
    data = one.live_thumbnail(session_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Chưa có hình trực tiếp.")
    return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------- client (device token)

class PairRequest(BaseModel):
    code: str = Field(..., min_length=4, max_length=16)
    name: str = Field("", max_length=120)
    hostname: str = Field("", max_length=255)
    app_version: str = Field("", max_length=50)


@router.post("/client/pair")
def pair(payload: PairRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        device, token = one.pair_device(db, payload.code, payload.name, payload.hostname, payload.app_version)
    except one.OneError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return {"device": one.serialize_device(device), "token": token}


@router.get("/client/me")
def whoami(device: ClientDevice = Depends(device_auth)) -> dict[str, Any]:
    return {"device": one.serialize_device(device)}


@router.get("/client/exams")
def client_exams(device: ClientDevice = Depends(device_auth), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return one.open_exams(db)


class SessionOpenRequest(BaseModel):
    client_session_id: str = Field(..., min_length=4, max_length=64)
    candidate_ref: str = Field("", max_length=100)
    candidate_name: str = Field("", max_length=255)
    exam_id: Optional[str] = None
    exam_name: str = Field("", max_length=255)
    source_kind: str = Field("CAMERA", max_length=30)
    simulation: bool = False
    started_at: Optional[str] = None
    client_time: Optional[str] = None


@router.post("/client/sessions")
def open_session(payload: SessionOpenRequest, device: ClientDevice = Depends(device_auth), db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        data = online.apply_candidate_identity(db, device, payload.model_dump())
        session, created = one.open_session(db, device, data)
        if created:
            online.after_session_opened(db, device, session, data)
    except one.OneError as exc:
        raise _bad(exc) from exc
    result = one.serialize_live(session)
    if created:
        realtime_manager.broadcast_threadsafe({"type": "ONE_SESSION_OPENED", "session": result})
    return {"created": created, "session": result}


class EventsRequest(BaseModel):
    events: list[dict[str, Any]]


@router.post("/client/sessions/{session_id}/events")
def post_events(session_id: str, payload: EventsRequest, device: ClientDevice = Depends(device_auth), db: Session = Depends(get_db)) -> dict[str, Any]:
    session = _owned(db, device, session_id)
    try:
        counts = one.ingest_events(db, session, payload.events)
    except one.OneError as exc:
        raise _bad(exc) from exc
    if counts["created"]:
        realtime_manager.broadcast_threadsafe({"type": "ONE_EVENTS", "session_id": session.id, **counts})
    return counts


@router.post("/client/events/{event_id}/evidence")
async def post_evidence(
    event_id: str,
    image: UploadFile = File(...),
    sha256: str = Form(""),
    device: ClientDevice = Depends(device_auth),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    data = await _read_image(image)
    try:
        evidence = one.attach_evidence(db, device, event_id, data, sha256 or None)
    except one.OneNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except one.OneError as exc:
        raise HTTPException(status_code=422 if "Ảnh" in str(exc) or "SHA" in str(exc) or "JPEG" in str(exc) else 403, detail=str(exc)) from exc
    return {"event_id": event_id, "status": evidence.status, "sha256": evidence.video_sha256, "bytes": evidence.file_size_bytes}


@router.post("/client/sessions/{session_id}/bookmarks")
async def post_bookmark(
    session_id: str,
    image: UploadFile = File(...),
    request_id: str = Form(...),
    frame_id: int = Form(0),
    session_elapsed_ms: Optional[float] = Form(None),
    captured_at: Optional[str] = Form(None),
    note: str = Form(""),
    created_by: str = Form(""),
    sha256: str = Form(""),
    width: int = Form(0),
    height: int = Form(0),
    source_kind: str = Form("CAMERA"),
    device: ClientDevice = Depends(device_auth),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    session = _owned(db, device, session_id)
    data = await _read_image(image)
    try:
        bookmark, created = one.create_one_bookmark(
            db, session,
            request_id=request_id, frame_id=frame_id, session_elapsed_ms=session_elapsed_ms,
            captured_at=captured_at, note=note[:2000],
            created_by=(created_by.strip()[:100] or f"VIGIL One · {device.name}"),
            data=data, sha256=sha256 or None, width=width, height=height, source_kind=source_kind,
        )
    except one.OneError as exc:
        raise _bad(exc) from exc
    result = serialize_bookmark(bookmark)
    if created:
        realtime_manager.broadcast_threadsafe({"type": "PROCTOR_BOOKMARK", "bookmark": result})
    return {"created": created, "bookmark": result}


@router.post("/client/sessions/{session_id}/heartbeat")
async def post_heartbeat(
    session_id: str,
    status: str = Form("{}"),
    thumbnail: Optional[UploadFile] = File(None),
    device: ClientDevice = Depends(device_auth),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    session = _owned(db, device, session_id)
    try:
        parsed = json.loads(status or "{}")
        if not isinstance(parsed, dict):
            raise ValueError
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="status phải là JSON object.") from exc
    thumb = await _read_image(thumbnail) if thumbnail is not None else None
    one.heartbeat(db, session, parsed, thumb)
    online.connection_back(db, session)
    live = one.serialize_live(session)
    realtime_manager.broadcast_threadsafe({"type": "ONE_HEARTBEAT", "session": live})
    # The client adapts its live picture to this (smaller and rarer unless a proctor is watching)
    return {"ok": True, "session_status": session.status, "live": online.live_settings(db, session)}


class StopRequest(BaseModel):
    ended_at: Optional[str] = None
    frame_count: int = 0
    average_fps: float = 0.0
    reason: str = Field("", max_length=20)  # SUBMITTED (candidate pressed Submit) or QUIT


@router.post("/client/sessions/{session_id}/stop")
def stop(session_id: str, payload: StopRequest, device: ClientDevice = Depends(device_auth), db: Session = Depends(get_db)) -> dict[str, Any]:
    session = _owned(db, device, session_id)
    already_ended = session.status in ("COMPLETED", "CLOSED")
    session = one.stop_session(db, session, payload.model_dump())
    if not already_ended:
        online.on_stop(db, session, payload.reason)
    result = one.serialize_live(session)
    realtime_manager.broadcast_threadsafe({"type": "ONE_SESSION_STOPPED", "session": result})
    return {"session": result}



# ---------------------------------------------------------------- online exams: candidate side

class JoinRequest(BaseModel):
    join_code: str = Field(..., min_length=4, max_length=16)
    candidate_code: str = Field(..., min_length=1, max_length=50)
    pin: str = Field(..., min_length=1, max_length=64)
    hostname: str = Field("", max_length=255)
    app_version: str = Field("", max_length=50)


@router.post("/client/join")
def join_exam(payload: JoinRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        device, token, candidate, exam = online.join(
            db, payload.join_code, payload.candidate_code, payload.pin,
            {"hostname": payload.hostname, "app_version": payload.app_version})
    except one.OneError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return {
        "token": token,
        "expires_at": device.expires_at.isoformat() + "Z",
        "exam": {"id": exam.id, "name": exam.name, "subject_code": exam.subject_code, "exam_date": exam.exam_date},
        "candidate": {"code": candidate.candidate_code, "full_name": candidate.full_name,
                      "consent_given": candidate.consent_at is not None},
        "policy": online.policy_for(exam),
    }


@router.post("/client/consent")
def give_consent(device: ClientDevice = Depends(device_auth), db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        candidate = online.record_consent(db, device)
    except one.OneError as exc:
        raise _bad(exc) from exc
    return {"consent_at": candidate.consent_at.isoformat() + "Z"}


@router.get("/client/policy")
def client_policy(device: ClientDevice = Depends(device_auth), db: Session = Depends(get_db)) -> dict[str, Any]:
    candidate = online.candidate_of(db, device)
    exam = db.get(Exam, candidate.exam_id) if candidate else None
    return online.policy_for(exam) if exam else dict(online.DEFAULT_POLICY)


# ---------------------------------------------------------------- online exams: web (chief / proctors)

def _exam(db: Session, exam_id: str) -> Exam:
    exam = db.get(Exam, exam_id)
    if exam is None:
        raise HTTPException(status_code=404, detail="Exam not found")
    return exam


@router.get("/exams")
def online_exams(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    rows = db.query(Exam).filter(Exam.status == "ACTIVE").order_by(Exam.created_at.desc()).all()
    return [online.serialize_exam(e) for e in rows]


class JoiningRequest(BaseModel):
    open: bool
    rotate: bool = False


@router.post("/exams/{exam_id}/joining")
def set_joining(exam_id: str, payload: JoiningRequest, db: Session = Depends(get_db),
                user=Depends(require_roles("CHIEF", "ADMIN", always=True))) -> dict[str, Any]:
    exam = online.set_joining(db, _exam(db, exam_id), payload.open, payload.rotate)
    one._audit(db, actor_name(user), "SET_ONLINE_JOINING", "EXAM", exam.id, {"open": exam.join_open, "rotate": payload.rotate})
    db.commit()
    return online.serialize_exam(exam)


@router.put("/exams/{exam_id}/policy")
def put_policy(exam_id: str, payload: dict[str, Any], db: Session = Depends(get_db),
               _user=Depends(require_roles("CHIEF", "ADMIN", always=True))) -> dict[str, Any]:
    try:
        return online.update_policy(db, _exam(db, exam_id), payload)
    except one.OneError as exc:
        raise _bad(exc) from exc


@router.get("/exams/{exam_id}/roster")
def get_roster(exam_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    exam = _exam(db, exam_id)
    rows = online.roster(db, exam)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    return {"exam": online.serialize_exam(exam), "candidates": rows, "counts": counts}


class CandidatesRequest(BaseModel):
    candidates: list[dict[str, Any]]


@router.post("/exams/{exam_id}/candidates")
def add_candidates(exam_id: str, payload: CandidatesRequest, db: Session = Depends(get_db),
                   user=Depends(require_roles("CHIEF", "ADMIN", always=True))) -> dict[str, Any]:
    exam = _exam(db, exam_id)
    try:
        result = online.import_candidates(db, exam, payload.candidates)
    except one.OneError as exc:
        raise _bad(exc) from exc
    one._audit(db, actor_name(user), "IMPORT_CANDIDATES", "EXAM", exam.id, {"count": len(result)})
    db.commit()
    return {"imported": result}


@router.delete("/exams/{exam_id}/candidates/{candidate_id}")
def delete_candidate(exam_id: str, candidate_id: str, db: Session = Depends(get_db),
                     _user=Depends(require_roles("CHIEF", "ADMIN", always=True))) -> dict[str, Any]:
    try:
        online.remove_candidate(db, _exam(db, exam_id), candidate_id)
    except one.OneError as exc:
        raise _bad(exc) from exc
    return {"deleted": candidate_id}


@router.post("/sessions/{session_id}/boost")
def boost_live(session_id: str) -> dict[str, Any]:
    """A proctor opened this candidate: ask for a bigger, more frequent live picture for a minute."""
    online.boost(session_id)
    return {"boosted_for_seconds": online.BOOST_SECONDS}
