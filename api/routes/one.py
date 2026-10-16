"""VIGIL One endpoints: pairing, live candidate sessions and data sync.

Two audiences:

* ``/one/client/*`` — called by the VIGIL One desktop with its device token
  (``Authorization: Bearer <token>``).  These never use the browser sign-in,
  so they stay reachable in ``VIGIL_AUTH_MODE=required``.
* the rest — the web: pairing codes and devices (chief/admin, always signed
  in) and the live list / thumbnails of One candidates.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import (APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, Request, Response, UploadFile,
                     WebSocket, WebSocketDisconnect)
from fastapi.responses import FileResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.auth import actor_name, require_roles
from api.realtime import realtime_manager
from storage.database import get_db
from storage.db_models import ClientDevice, ExamSession
from storage import one_service as one
from storage import online_exam_service as online
from storage import one_analysis, one_live, one_video
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


@router.get("/analysis/status")
def analysis_status() -> dict[str, Any]:
    """Workers analysing VIGIL One video on this Hub (for the operations page)."""
    service = one_analysis.get_service()
    return service.status() if service is not None else {"running": False, "workers": 0}


@router.get("/sessions/{session_id}/thumbnail")
def get_thumbnail(session_id: str) -> Response:
    data = one.live_thumbnail(session_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Chưa có hình trực tiếp.")
    return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------- full-session video (web)

def _one_session(db: Session, session_id: str) -> ExamSession:
    session = db.get(ExamSession, session_id)
    if session is None or session.product != "ONE":
        raise HTTPException(status_code=404, detail="Phiên VIGIL One không tồn tại.")
    return session


def _queue_merge(request: Request, background: BackgroundTasks, session_id: str) -> None:
    provider = request.app.dependency_overrides.get(get_db, get_db)
    background.add_task(one_video.merge_in_background, session_id, provider)


@router.get("/sessions/{session_id}/video")
def get_session_video(session_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    return one_video.info(db, _one_session(db, session_id))


@router.get("/sessions/{session_id}/video/file")
def get_session_video_file(session_id: str, db: Session = Depends(get_db)):
    session = _one_session(db, session_id)
    path = Path(session.video_path or "")
    if session.video_status not in ("READY", "PARTIAL") or not path.is_file():
        raise HTTPException(status_code=404, detail="Video cả phiên chưa sẵn sàng.")
    name = f"VIGIL-One_{session.subject_ref or 'candidate'}_{session.id[:8]}.mp4"
    return FileResponse(str(path), media_type="video/mp4", filename=name, content_disposition_type="inline",
                        headers={"X-Vigil-Sha256": session.video_sha256 or ""})


@router.get("/sessions/{session_id}/video/segments/{seq}")
def get_session_video_segment(session_id: str, seq: int, db: Session = Depends(get_db)):
    _one_session(db, session_id)
    segment = next((s for s in one_video.segments(db, session_id) if s.seq == seq), None)
    if segment is None or not Path(segment.file_path).is_file():
        raise HTTPException(status_code=404, detail="Đoạn video không tồn tại.")
    return FileResponse(segment.file_path, media_type="video/mp4", headers={"X-Vigil-Sha256": segment.sha256})


@router.post("/sessions/{session_id}/video/merge", status_code=202)
def merge_session_video(session_id: str, request: Request, background: BackgroundTasks, db: Session = Depends(get_db),
                        _user=Depends(require_roles("CHIEF", "ADMIN", always=True))) -> dict[str, Any]:
    """Join what has arrived so far (e.g. a candidate whose app never sent the end of the session)."""
    session = _one_session(db, session_id)
    session.video_status = "MERGING"
    db.commit()
    _queue_merge(request, background, session_id)
    return one_video.info(db, session)


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
    # "server": this client only records and uploads video; the Hub analyses it (storage.one_analysis)
    analysis: str = Field("", max_length=10)


def _from_this_computer(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in ("127.0.0.1", "::1", "localhost", "testclient")


@router.post("/client/sessions")
def open_session(payload: SessionOpenRequest, request: Request, device: ClientDevice = Depends(device_auth),
                 db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        data = online.apply_candidate_identity(db, device, payload.model_dump())
        # The Hub decides: a candidate is analysed here; a simulation only from the Hub computer itself
        session, created = one.open_session(db, device, data,
                                            candidate_device=online.candidate_of(db, device) is not None,
                                            allow_simulation=_from_this_computer(request))
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


@router.post("/client/events/{event_id}/clip")
async def post_clip(
    event_id: str,
    video: UploadFile = File(...),
    sha256: str = Form(""),
    device: ClientDevice = Depends(device_auth),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    data = await video.read(one.MAX_CLIP_BYTES + 1)
    if len(data) > one.MAX_CLIP_BYTES:
        raise HTTPException(status_code=413, detail="Clip vượt quá giới hạn.")
    try:
        evidence = one.attach_clip(db, device, event_id, data, sha256 or None)
    except one.OneNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except one.OneError as exc:
        raise HTTPException(status_code=422 if "Clip" in str(exc) or "SHA" in str(exc) or "MP4" in str(exc) else 403, detail=str(exc)) from exc
    return {"event_id": event_id, "status": evidence.status, "sha256": evidence.video_sha256, "bytes": evidence.file_size_bytes}


@router.post("/client/sessions/{session_id}/video")
async def post_video_segment(
    session_id: str,
    request: Request,
    background: BackgroundTasks,
    video: UploadFile = File(...),
    seq: int = Form(...),
    start_ms: float = Form(...),
    duration_s: float = Form(...),
    sha256: str = Form(""),
    speech: str = Form(""),
    device: ClientDevice = Depends(device_auth),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """One segment of the full-session recording (accepted after the end too: the queue may still be catching up).

    In a SERVER-analysis session it is also the input of the Hub's analysis; ``speech`` (JSON
    [[start_ms, end_ms], ...]) says when the candidate's microphone heard a voice."""
    session = _owned(db, device, session_id)
    data = await video.read(one_video.MAX_SEGMENT_BYTES + 1)
    if len(data) > one_video.MAX_SEGMENT_BYTES:
        raise HTTPException(status_code=413, detail="Đoạn video vượt quá giới hạn.")
    try:
        speech_items = json.loads(speech) if speech else None
        if speech_items is not None and not isinstance(speech_items, list):
            raise ValueError
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="speech phải là JSON [[start_ms, end_ms], ...].") from exc
    try:
        segment, created = one_video.add_segment(db, session, seq=seq, start_ms=start_ms, duration_s=duration_s,
                                                 data=data, sha256=sha256 or None, speech=speech_items)
    except one_video.VideoError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if created and session.analysis_mode == "SERVER":
        one_analysis.notify(session.id)
    if created and session.status in ("COMPLETED", "CLOSED"):
        _queue_merge(request, background, session.id)
    return {"seq": segment.seq, "sha256": segment.sha256, "bytes": segment.file_size_bytes, "created": created}


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
    # The client adapts its live picture to this (smaller and rarer unless a proctor is watching);
    # in SERVER mode it also learns how far the Hub's analysis is (calibration, delay)
    return {"ok": True, "session_status": session.status, "live": online.live_settings(db, session),
            "analysis": (live.get("live") or {}).get("analysis")}


class StopRequest(BaseModel):
    ended_at: Optional[str] = None
    frame_count: int = 0
    average_fps: float = 0.0
    reason: str = Field("", max_length=20)  # SUBMITTED (candidate pressed Submit) or QUIT


@router.post("/client/sessions/{session_id}/stop")
def stop(session_id: str, payload: StopRequest, request: Request, background: BackgroundTasks,
         device: ClientDevice = Depends(device_auth), db: Session = Depends(get_db)) -> dict[str, Any]:
    session = _owned(db, device, session_id)
    already_ended = session.status in ("COMPLETED", "CLOSED")
    session = one.stop_session(db, session, payload.model_dump())
    if session.analysis_mode == "SERVER":
        one_analysis.notify(session.id)  # last clips once the remaining video is analysed
    if not already_ended:
        online.on_stop(db, session, payload.reason)
        if one_video.needs_merge(db, session):
            session.video_status = "MERGING"
            db.commit()
            _queue_merge(request, background, session.id)
    result = one.serialize_live(session)
    realtime_manager.broadcast_threadsafe({"type": "ONE_SESSION_STOPPED", "session": result})
    return {"session": result}



# ---------------------------------------------------------------- online exams: candidate side

class JoinRequest(BaseModel):
    join_code: str = Field(..., min_length=3, max_length=16)
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


@router.post("/client/check")
async def client_check(image: UploadFile = File(...), device: ClientDevice = Depends(device_auth)) -> dict[str, Any]:
    """Pre-exam device check done by the Hub, so the candidate's computer needs no face model."""
    data = await _read_image(image)
    try:
        return one_analysis.check_frame(data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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


# ---------------------------------------------------------------- live video (app -> Hub -> web)

LIVE_STATUS_SECONDS = 1.0
MJPEG_IDLE_SECONDS = 60.0   # no new picture for this long: end the stream (the page reconnects)


def _live_session(provider, token: Optional[str], session_id: str) -> tuple[Optional[str], Optional[str], Any]:
    """(error, None, None) or (None, session id, start time) for a candidate app opening its live stream."""
    gen = provider()
    db = next(gen)
    try:
        device = one.device_for_token(db, token)
        if device is None:
            return "Thiết bị chưa được ghép nối hoặc đã bị thu hồi.", None, None
        try:
            session = one.owned_session(db, device, session_id)
        except one.OneError as exc:
            return str(exc), None, None
        if session.analysis_mode != "SERVER":
            return "Phiên này không phân tích trên Hub.", None, None
        if session.status not in ("RUNNING", "STOPPING", "INTERRUPTED"):
            return "Phiên đã kết thúc.", None, None
        return None, session.id, session.started_at
    finally:
        gen.close()


def _live_reply(provider, session_id: str) -> dict[str, Any]:
    gen = provider()
    db = next(gen)
    try:
        session = db.get(ExamSession, session_id)
        if session is None:
            return {"ended": True}
        live = one.serialize_live(session)
        return {"session_status": session.status, "ended": session.status not in ("RUNNING", "STOPPING", "INTERRUPTED"),
                "analysis": (live.get("live") or {}).get("analysis"), "stream": one_live.stats(session_id)}
    finally:
        gen.close()


@router.websocket("/client/sessions/{session_id}/live")
async def live_stream(websocket: WebSocket, session_id: str):
    """The candidate app's live frames (binary: header length, JSON {t, voice}, JPEG).

    The Hub analyses them straight away and answers about once a second with what it sees
    (calibration, faces), which the app turns into hints for the candidate."""
    auth = websocket.headers.get("authorization") or ""
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else websocket.query_params.get("token")
    provider = websocket.app.dependency_overrides.get(get_db, get_db)
    error, sid, started = await run_in_threadpool(_live_session, provider, token, session_id)
    if error:
        await websocket.close(code=1008, reason=error[:120])
        return
    await websocket.accept()
    last_reply = 0.0
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            data = message.get("bytes")
            if data:
                try:
                    header, jpeg = one_live.parse_message(data)
                    voice = header.get("voice")
                    elapsed = (datetime.datetime.utcnow() - started).total_seconds() * 1000.0 if started else None
                    one_live.put_frame(sid, jpeg, float(header.get("t") or 0.0), None if voice is None else bool(voice),
                                       elapsed_ms=elapsed)
                except (ValueError, TypeError) as exc:
                    await websocket.send_text(json.dumps({"error": str(exc)}))
                    continue
                one_analysis.notify_live(sid)
            now = time.monotonic()
            if now - last_reply >= LIVE_STATUS_SECONDS:
                last_reply = now
                reply = await run_in_threadpool(_live_reply, provider, sid)
                await websocket.send_text(json.dumps(reply, ensure_ascii=False))
                if reply.get("ended"):
                    await websocket.close(code=1000)
                    break
    except WebSocketDisconnect:
        pass
    finally:
        one_live.disconnected(sid)


@router.get("/sessions/{session_id}/live.mjpg")
async def live_mjpeg(session_id: str, request: Request, fps: float = 8.0, overlay: bool = True) -> StreamingResponse:
    """The candidate's live picture with the analysis drawn on it, for an <img> on the web."""
    interval = 1.0 / min(15.0, max(1.0, fps))

    async def frames():
        seq, idle = 0, 0.0
        while not await request.is_disconnected():
            item = one_live.view(session_id, seq, annotated=overlay)
            if item is None:
                idle += interval
                if idle > MJPEG_IDLE_SECONDS:
                    return
                await asyncio.sleep(interval)
                continue
            idle = 0.0
            seq, jpeg = item
            yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(jpeg)).encode()
                   + b"\r\n\r\n" + jpeg + b"\r\n")
            await asyncio.sleep(interval)

    return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.get("/sessions/{session_id}/monitor")
def monitor_state(session_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Everything the candidate monitor page shows besides the picture."""
    session = _one_session(db, session_id)
    live = one.serialize_live(session)
    live["stream"] = one_live.stats(session.id)
    live["subject_name"] = session.subject_name
    return live


def _service_or_503() -> "one_analysis.OneAnalysisService":
    service = one_analysis.get_service()
    if service is None:
        raise HTTPException(status_code=503, detail="Phân tích VIGIL One trên Hub đang tắt (VIGIL_ONE_ANALYSIS=0).")
    return service


@router.post("/sessions/{session_id}/recalibrate")
def recalibrate(session_id: str, db: Session = Depends(get_db),
                user=Depends(require_roles("PROCTOR", "CHIEF", "ADMIN"))) -> dict[str, Any]:
    """Start the candidate's gaze calibration again; their app asks them to look at the screen."""
    session = _one_session(db, session_id)
    try:
        snapshot = _service_or_503().recalibrate(db, session.id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    one._audit(db, actor_name(user), "RECALIBRATE_ONE", "EXAM_SESSION", session.id, {})
    db.commit()
    return {"calibration": snapshot.get("state", "COLLECTING")}


class MaterialsRequest(BaseModel):
    allowed: bool


@router.post("/sessions/{session_id}/materials")
def set_materials(session_id: str, payload: MaterialsRequest, db: Session = Depends(get_db),
                  user=Depends(require_roles("PROCTOR", "CHIEF", "ADMIN"))) -> dict[str, Any]:
    """Books / notes allowed for this candidate (open-book) or not."""
    session = _one_session(db, session_id)
    try:
        _service_or_503().set_allowed_materials(db, session.id, payload.allowed)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    one._audit(db, actor_name(user), "ONE_MATERIALS", "EXAM_SESSION", session.id, {"allowed": payload.allowed})
    db.commit()
    return {"allowed": payload.allowed}


class ProctorMarkRequest(BaseModel):
    note: str = Field("", max_length=2000)


@router.post("/sessions/{session_id}/mark")
def proctor_mark(session_id: str, payload: ProctorMarkRequest, db: Session = Depends(get_db),
                 user=Depends(require_roles("PROCTOR", "CHIEF", "ADMIN"))) -> dict[str, Any]:
    """A proctor watching live marks the current frame (a manual bookmark, not an AI signal)."""
    session = _one_session(db, session_id)
    frame = one_live.latest(session.id)
    data = frame.jpeg if frame is not None else one.live_thumbnail(session.id)
    if not data:
        raise HTTPException(status_code=409, detail="Chưa có hình trực tiếp của thí sinh này.")
    try:
        bookmark, _ = one.create_one_bookmark(
            db, session, request_id=f"web-{uuid.uuid4().hex}", frame_id=frame.seq if frame else 0,
            session_elapsed_ms=frame.t_ms if frame else None, captured_at=None, note=payload.note,
            created_by=actor_name(user, "Giám thị"), data=data, sha256=None, source_kind="LIVE_STREAM")
    except one.OneError as exc:
        raise _bad(exc) from exc
    result = serialize_bookmark(bookmark)
    realtime_manager.broadcast_threadsafe({"type": "PROCTOR_BOOKMARK", "bookmark": result})
    return {"bookmark": result}


@router.post("/sessions/{session_id}/boost")
def boost_live(session_id: str) -> dict[str, Any]:
    """A proctor opened this candidate: ask for a bigger, more frequent live picture for a minute."""
    online.boost(session_id)
    return {"boosted_for_seconds": online.BOOST_SECONDS}
