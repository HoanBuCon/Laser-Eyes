"""VIGIL One on the shared server: pairing, session sync, signals, evidence,
bookmarks, live status and the multi-product review queue."""

from __future__ import annotations

import hashlib
import io
import json
import uuid

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.main import app
from storage.auth_service import create_user
from storage.database import Base, get_db
from storage.db_models import AuditLog, DetectionEvent, ExamRoom, ExamSession, ProctorBookmark, User
from storage.session_lifecycle import close_interrupted_sessions

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
PASSWORD = "correct-horse-1"


@pytest.fixture(autouse=True)
def _one_evidence_root(tmp_path, monkeypatch):
    monkeypatch.setenv("VIGIL_ONE_EVIDENCE_ROOT", str(tmp_path / "one_evidence"))


@pytest.fixture
def db():
    Base.metadata.create_all(bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(db):
    def override():
        s = TestingSession()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _sign_in(client, db, username="chief", role="CHIEF"):
    if db.query(User).filter(User.username == username).first() is None:
        create_user(db, username, username.title(), role, PASSWORD)
    res = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD})
    assert res.status_code == 200, res.text


def _jpeg(color=(40, 80, 120)) -> bytes:
    frame = np.zeros((48, 64, 3), dtype=np.uint8)
    frame[:] = color
    ok, enc = cv2.imencode(".jpg", frame)
    assert ok
    return enc.tobytes()


def _paired(client, db) -> dict:
    """Pair one device; returns auth headers. Signs the chief out afterwards."""
    _sign_in(client, db)
    code = client.post("/api/v1/one/pairing-codes").json()["code"]
    client.post("/api/v1/auth/logout")
    client.cookies.clear()
    res = client.post("/api/v1/one/client/pair", json={"code": code, "name": "Laptop SV01", "hostname": "pc-01"})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['token']}"}


def _open(client, headers, cid="cs-1", candidate="SV-2026-001", **extra) -> dict:
    res = client.post("/api/v1/one/client/sessions", headers=headers,
                      json={"client_session_id": cid, "candidate_ref": candidate, "exam_name": "Thi thử", **extra})
    assert res.status_code == 200, res.text
    return res.json()


def _event(event_id=None, event_type="look_away", severity="medium") -> dict:
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "event_type": event_type,
        "severity": severity,
        "started_at": "2026-10-14T09:12:03.120+07:00",
        "ended_at": "2026-10-14T09:12:07.480+07:00",
        "duration_seconds": 4.36,
        "session_elapsed_ms": 192340,
        "confidence": 0.81,
        "label": "Mắt nhìn ngoài vùng thi",
        "reason": "Mắt ở ngoài vùng an toàn 4,4 giây.",
    }


# ---------------------------------------------------------------- pairing

def test_pairing_code_needs_a_chief_even_in_open_mode(client, db):
    assert client.post("/api/v1/one/pairing-codes").status_code == 401
    _sign_in(client, db, "proctor1", "PROCTOR")
    assert client.post("/api/v1/one/pairing-codes").status_code == 403


def test_pairing_code_is_single_use_and_token_is_stored_hashed(client, db):
    _sign_in(client, db)
    code = client.post("/api/v1/one/pairing-codes").json()["code"]
    assert len(code) == 6
    first = client.post("/api/v1/one/client/pair", json={"code": code.lower(), "name": "A"})
    assert first.status_code == 200
    token = first.json()["token"]
    assert client.post("/api/v1/one/client/pair", json={"code": code, "name": "B"}).status_code == 401
    from storage.db_models import ClientDevice
    device = db.query(ClientDevice).one()
    assert device.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token not in json.dumps([r.metadata_json for r in db.query(AuditLog)])


def test_client_api_rejects_missing_or_revoked_token(client, db):
    assert client.get("/api/v1/one/client/me").status_code == 401
    headers = _paired(client, db)
    assert client.get("/api/v1/one/client/me", headers=headers).status_code == 200
    _sign_in(client, db)
    device_id = client.get("/api/v1/one/devices").json()[0]["id"]
    assert client.delete(f"/api/v1/one/devices/{device_id}").json()["revoked"] is True
    assert client.get("/api/v1/one/client/me", headers=headers).status_code == 401


def test_client_api_works_when_sign_in_is_required(client, db, monkeypatch):
    headers = _paired(client, db)
    monkeypatch.setenv("VIGIL_AUTH_MODE", "required")
    assert client.get("/api/v1/one/client/me", headers=headers).status_code == 200
    assert client.get("/api/v1/one/sessions").status_code == 401  # web side still needs a user


# ---------------------------------------------------------------- sessions and signals

def test_session_is_idempotent_and_lives_in_the_virtual_room(client, db):
    headers = _paired(client, db)
    a = _open(client, headers, simulation=True)
    b = _open(client, headers)
    assert a["created"] is True and b["created"] is False
    assert a["session"]["session_id"] == b["session"]["session_id"]
    session = db.query(ExamSession).one()
    assert session.product == "ONE" and session.subject_ref == "SV-2026-001" and session.simulation
    assert db.get(ExamRoom, session.room_id).room_code == "VIGIL-ONE"


def test_events_map_to_review_incidents_without_duplicates(client, db):
    headers = _paired(client, db)
    sid = _open(client, headers)["session"]["session_id"]
    events = [_event("e-1"), _event("e-2", "suspicious_object", "high"), _event("e-3", "low_light", "info")]
    res = client.post(f"/api/v1/one/client/sessions/{sid}/events", headers=headers, json={"events": events})
    assert res.json() == {"created": 3, "duplicate": 0}
    again = client.post(f"/api/v1/one/client/sessions/{sid}/events", headers=headers, json={"events": events})
    assert again.json() == {"created": 0, "duplicate": 3}
    rows = {e.event_id: e for e in db.query(DetectionEvent)}
    assert rows["e-1"].primary_signal == "ONE_LOOK_AWAY" and rows["e-1"].severity == "MEDIUM"
    assert rows["e-2"].severity == "HIGH" and rows["e-2"].risk_score == 80
    assert rows["e-3"].event_type == "SYSTEM_CONDITION"
    assert all(e.source_product == "VIGIL_ONE" and e.subject_ref == "SV-2026-001" and e.review_status == "PENDING" for e in rows.values())
    db.expire_all()
    assert db.query(ExamSession).one().total_events == 3


def test_unknown_event_type_is_rejected(client, db):
    headers = _paired(client, db)
    sid = _open(client, headers)["session"]["session_id"]
    res = client.post(f"/api/v1/one/client/sessions/{sid}/events", headers=headers, json={"events": [_event(event_type="cheating")]})
    assert res.status_code == 422


def test_another_device_cannot_write_to_my_session(client, db):
    mine = _paired(client, db)
    other = _paired(client, db)
    sid = _open(client, mine)["session"]["session_id"]
    res = client.post(f"/api/v1/one/client/sessions/{sid}/events", headers=other, json={"events": [_event()]})
    assert res.status_code == 403


# ---------------------------------------------------------------- evidence and bookmarks

def test_evidence_checks_digest_and_is_served_to_reviewers(client, db):
    headers = _paired(client, db)
    sid = _open(client, headers)["session"]["session_id"]
    client.post(f"/api/v1/one/client/sessions/{sid}/events", headers=headers, json={"events": [_event("e-ev")]})
    image = _jpeg()
    bad = client.post("/api/v1/one/client/events/e-ev/evidence", headers=headers,
                      files={"image": ("e.jpg", image, "image/jpeg")}, data={"sha256": "0" * 64})
    assert bad.status_code == 422
    not_jpeg = client.post("/api/v1/one/client/events/e-ev/evidence", headers=headers,
                           files={"image": ("e.jpg", b"GIF89a....", "image/jpeg")})
    assert not_jpeg.status_code == 422
    digest = hashlib.sha256(image).hexdigest()
    ok = client.post("/api/v1/one/client/events/e-ev/evidence", headers=headers,
                     files={"image": ("e.jpg", image, "image/jpeg")}, data={"sha256": digest})
    assert ok.status_code == 200 and ok.json()["sha256"] == digest
    again = client.post("/api/v1/one/client/events/e-ev/evidence", headers=headers,
                        files={"image": ("e.jpg", image, "image/jpeg")}, data={"sha256": digest})
    assert again.status_code == 200
    item = client.get("/api/v1/proctor/review-queue", params={"product": "ONE"}).json()["items"][0]
    assert item["snapshot_url"] and item["video_url"] is None and item["sha256"] == digest
    served = client.get(item["snapshot_url"])
    assert served.status_code == 200 and served.content == image


def test_bookmark_is_manual_and_idempotent(client, db):
    headers = _paired(client, db)
    sid = _open(client, headers)["session"]["session_id"]
    image = _jpeg((200, 10, 10))
    form = {"request_id": "req-1", "frame_id": "120", "session_elapsed_ms": "4000", "note": "Nhìn sang trái"}
    first = client.post(f"/api/v1/one/client/sessions/{sid}/bookmarks", headers=headers,
                        files={"image": ("b.jpg", image, "image/jpeg")}, data=form)
    assert first.status_code == 200, first.text
    second = client.post(f"/api/v1/one/client/sessions/{sid}/bookmarks", headers=headers,
                         files={"image": ("b.jpg", image, "image/jpeg")}, data=form)
    assert first.json()["created"] is True and second.json()["created"] is False
    bookmark = db.query(ProctorBookmark).one()
    assert bookmark.source_product == "VIGIL_ONE" and bookmark.subject_ref == "SV-2026-001"
    assert bookmark.sha256 == hashlib.sha256(image).hexdigest() and bookmark.evidence_status == "READY"
    queue = client.get("/api/v1/proctor/review-queue", params={"product": "ONE", "source": "MANUAL"}).json()
    assert [i["source_type"] for i in queue["items"]] == ["MANUAL_BOOKMARK"]


# ---------------------------------------------------------------- queue, live, stop

def test_review_queue_separates_products(client, db):
    headers = _paired(client, db)
    sid = _open(client, headers)["session"]["session_id"]
    client.post(f"/api/v1/one/client/sessions/{sid}/events", headers=headers, json={"events": [_event("one-1")]})
    room = ExamRoom(room_code="CLASSROOM-01", name="Room 1")
    db.add(room)
    db.flush()
    cls_session = ExamSession(room_id=room.id, exam_name="Classroom", status="COMPLETED")
    db.add(cls_session)
    db.flush()
    db.add(DetectionEvent(session_id=cls_session.id, room_id=room.id, event_id="cls-1", behavior="REPEATED_NEIGHBOR_GLANCE"))
    db.commit()
    one_q = client.get("/api/v1/proctor/review-queue", params={"product": "ONE"}).json()
    cls_q = client.get("/api/v1/proctor/review-queue", params={"product": "CLASSROOM"}).json()
    all_q = client.get("/api/v1/proctor/review-queue").json()
    assert [i["event_id"] for i in one_q["items"]] == ["one-1"]
    assert one_q["items"][0]["source_product"] == "VIGIL_ONE" and one_q["items"][0]["subject_ref"] == "SV-2026-001"
    assert [i["event_id"] for i in cls_q["items"]] == ["cls-1"]
    assert all_q["facets"]["products"] == {"CLASSROOM": 1, "ONE": 1}
    # the existing review endpoint decides One signals too
    res = client.post("/api/v1/events/one-1/review", json={"decision": "REJECTED", "reason_code": "NORMAL_BEHAVIOR", "reviewer_id": "gt"})
    assert res.status_code in (200, 201), res.text


def test_heartbeat_thumbnail_and_stop(client, db):
    headers = _paired(client, db)
    sid = _open(client, headers)["session"]["session_id"]
    thumb = _jpeg((10, 200, 10))
    res = client.post(f"/api/v1/one/client/sessions/{sid}/heartbeat", headers=headers,
                      data={"status": json.dumps({"fps": 27.5, "eye": "TRONG VÙNG", "alerts": 2, "secret": "x"})},
                      files={"thumbnail": ("t.jpg", thumb, "image/jpeg")})
    assert res.status_code == 200, res.text
    live = client.get("/api/v1/one/sessions").json()
    assert live[0]["connection"] == "LIVE" and live[0]["live"]["fps"] == 27.5 and "secret" not in live[0]["live"]
    assert client.get(live[0]["thumbnail_url"]).content == thumb
    stopped = client.post(f"/api/v1/one/client/sessions/{sid}/stop", headers=headers, json={"frame_count": 900, "average_fps": 28.0})
    assert stopped.json()["session"]["status"] == "COMPLETED"
    assert client.get(live[0]["thumbnail_url"]).status_code == 404
    assert client.get("/api/v1/one/sessions").json() == []


def test_server_restart_keeps_one_sessions_open(client, db):
    headers = _paired(client, db)
    _open(client, headers)
    room = ExamRoom(room_code="CLASSROOM-02", name="Room 2")
    db.add(room)
    db.flush()
    db.add(ExamSession(room_id=room.id, exam_name="Classroom run", status="RUNNING"))
    db.commit()
    assert close_interrupted_sessions(db) == 1
    statuses = {s.product: s.status for s in db.query(ExamSession)}
    assert statuses == {"ONE": "RUNNING", "CLASSROOM": "INTERRUPTED"}


def test_closed_session_accepts_no_more_data(client, db):
    headers = _paired(client, db)
    sid = _open(client, headers)["session"]["session_id"]
    session = db.get(ExamSession, sid)
    session.status = "CLOSED"
    db.commit()
    res = client.post(f"/api/v1/one/client/sessions/{sid}/events", headers=headers, json={"events": [_event()]})
    assert res.status_code == 422


def test_test_room_cleanup_never_removes_the_one_room(client, db):
    from storage.maintenance import plan_test_room_cleanup
    headers = _paired(client, db)
    _open(client, headers)
    db.add(ExamRoom(room_code="TMP-TEST-ROOM", name="left by a test"))
    db.commit()
    plan = plan_test_room_cleanup(db)
    codes = {room.room_code for room in plan.rooms}
    assert "TMP-TEST-ROOM" in codes and "VIGIL-ONE" not in codes
