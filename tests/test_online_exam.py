"""Online exams with VIGIL One on candidates' computers: join by code + PIN,
consent, identity from the roster, live-picture rate and server-side
integrity signals (connection lost, left early, computer changed, clock)."""

from __future__ import annotations

import datetime
import json

import pytest

from storage.db_models import ClientDevice, DetectionEvent, Exam, ExamSession
from storage import online_exam_service as online
from test_one_hub import _jpeg, _sign_in, client, db  # noqa: F401  (fixtures)


@pytest.fixture(autouse=True)
def _one_evidence_root(tmp_path, monkeypatch):
    monkeypatch.setenv("VIGIL_ONE_EVIDENCE_ROOT", str(tmp_path / "one_evidence"))


def _exam(client, db, candidates=None):  # noqa: F811
    exam = Exam(name="Toán 12 — thi online", subject_code="MATH12", status="ACTIVE")
    db.add(exam)
    db.commit()
    _sign_in(client, db)
    opened = client.post(f"/api/v1/one/exams/{exam.id}/joining", json={"open": True}).json()
    imported = client.post(f"/api/v1/one/exams/{exam.id}/candidates", json={"candidates": candidates or [
        {"candidate_code": "HS001", "full_name": "Nguyễn Văn A", "pin": "2468"},
        {"candidate_code": "HS002", "full_name": "Trần Thị B"},
    ]}).json()["imported"]
    client.cookies.clear()
    return exam.id, opened["join_code"], {row["candidate_code"]: row["pin"] for row in imported}


def _join(client, code, candidate="HS001", pin="2468", hostname="LAPTOP-A"):  # noqa: F811
    return client.post("/api/v1/one/client/join", json={"join_code": code, "candidate_code": candidate, "pin": pin,
                                                         "hostname": hostname, "app_version": "test"})


def _start(client, headers, cid="cs-online-1", **extra):  # noqa: F811
    return client.post("/api/v1/one/client/sessions", headers=headers, json={
        "client_session_id": cid, "candidate_ref": "SOMEONE-ELSE", "exam_name": "fake", **extra})


def _signals(db, session_id):  # noqa: F811
    db.expire_all()
    return [e.primary_signal for e in db.query(DetectionEvent).filter(DetectionEvent.session_id == session_id)]


def test_setup_needs_a_chief_and_issues_pins(client, db):  # noqa: F811
    exam = Exam(name="x", status="ACTIVE")
    db.add(exam)
    db.commit()
    assert client.post(f"/api/v1/one/exams/{exam.id}/joining", json={"open": True}).status_code == 401
    _, code, pins = _exam(client, db)
    assert len(code) == 7
    assert pins["HS001"] == "2468" and len(pins["HS002"]) == 6 and pins["HS002"].isdigit()


def test_join_refuses_wrong_pin_closed_exam_with_one_message(client, db):  # noqa: F811
    exam_id, code, _ = _exam(client, db)
    wrong = _join(client, code, pin="0000")
    unknown = _join(client, code, candidate="HS999")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"]
    _sign_in(client, db)
    client.post(f"/api/v1/one/exams/{exam_id}/joining", json={"open": False})
    client.cookies.clear()
    assert _join(client, code).status_code == 401


def test_candidate_identity_comes_from_the_roster_after_consent(client, db):  # noqa: F811
    exam_id, code, _ = _exam(client, db)
    joined = _join(client, code).json()
    assert joined["candidate"]["full_name"] == "Nguyễn Văn A" and joined["policy"]["live_width"] == 320
    headers = {"Authorization": f"Bearer {joined['token']}"}
    assert _start(client, headers).status_code == 422  # no consent yet
    assert client.post("/api/v1/one/client/consent", headers=headers).status_code == 200
    started = _start(client, headers)
    assert started.status_code == 200, started.text
    session = db.query(ExamSession).one()
    assert session.subject_ref == "HS001" and session.subject_name == "Nguyễn Văn A" and session.exam_id == exam_id
    roster = client.get(f"/api/v1/one/exams/{exam_id}/roster").json()
    assert roster["counts"] == {"LIVE": 1, "NOT_JOINED": 1}


def test_heartbeat_returns_live_rate_and_boost_raises_it(client, db):  # noqa: F811
    _, code, _ = _exam(client, db)
    headers = {"Authorization": f"Bearer {_join(client, code).json()['token']}"}
    client.post("/api/v1/one/client/consent", headers=headers)
    sid = _start(client, headers).json()["session"]["session_id"]
    beat = lambda: client.post(f"/api/v1/one/client/sessions/{sid}/heartbeat", headers=headers,  # noqa: E731
                               data={"status": "{}"}, files={"thumbnail": ("t.jpg", _jpeg(), "image/jpeg")}).json()
    assert beat()["live"] == {"enabled": True, "width": 320, "interval": 5, "boosted": False}
    client.post(f"/api/v1/one/sessions/{sid}/boost")
    assert beat()["live"]["width"] == 640 and beat()["live"]["interval"] == 2


def test_silent_computer_raises_and_closes_offline_signal(client, db):  # noqa: F811
    _, code, _ = _exam(client, db)
    headers = {"Authorization": f"Bearer {_join(client, code).json()['token']}"}
    client.post("/api/v1/one/client/consent", headers=headers)
    sid = _start(client, headers).json()["session"]["session_id"]
    session = db.get(ExamSession, sid)
    session.last_heartbeat_at = datetime.datetime.utcnow() - datetime.timedelta(seconds=90)
    db.commit()
    assert online.check_connections(db) == 1
    assert online.check_connections(db) == 0  # one open signal per outage
    client.post(f"/api/v1/one/client/sessions/{sid}/heartbeat", headers=headers, data={"status": "{}"})
    db.expire_all()
    offline = db.query(DetectionEvent).filter(DetectionEvent.primary_signal == "ONE_CLIENT_OFFLINE").one()
    assert offline.end_timestamp is not None and offline.event_type == "INTEGRITY"
    assert "trở lại" in json.loads(offline.incident_metadata_json)["reason"]


def test_abandoned_session_is_closed_after_the_limit(client, db, monkeypatch):  # noqa: F811
    _, code, _ = _exam(client, db)
    headers = {"Authorization": f"Bearer {_join(client, code).json()['token']}"}
    client.post("/api/v1/one/client/consent", headers=headers)
    sid = _start(client, headers).json()["session"]["session_id"]
    session = db.get(ExamSession, sid)
    last = datetime.datetime.utcnow() - datetime.timedelta(minutes=8)
    session.last_heartbeat_at = last
    db.commit()
    online.check_connections(db)  # the outage signal opens first
    assert online.close_abandoned(db) == []  # 8 min < the 15 min default: still waiting for the computer
    monkeypatch.setenv("VIGIL_ONE_ABANDON_MINUTES", "5")
    assert online.close_abandoned(db) == [sid]
    db.expire_all()
    session = db.get(ExamSession, sid)
    assert session.status == "COMPLETED" and abs((session.ended_at - last).total_seconds()) < 1
    assert sorted(_signals(db, sid)) == ["ONE_ABANDONED", "ONE_CLIENT_OFFLINE"]
    offline = db.query(DetectionEvent).filter(DetectionEvent.primary_signal == "ONE_CLIENT_OFFLINE").one()
    assert offline.end_timestamp is not None  # the outage ends with the session
    assert online.close_abandoned(db) == []  # closed once
    # a session left INTERRUPTED by a Hub restart is closed the same way
    other = db.get(ExamSession, sid)
    other.status, other.last_heartbeat_at = "INTERRUPTED", last
    db.commit()
    assert online.close_abandoned(db) == [sid]


@pytest.mark.parametrize("reason,expected", [("QUIT", ["ONE_LEFT_EARLY"]), ("SUBMITTED", [])])
def test_leaving_without_submitting_is_flagged(client, db, reason, expected):  # noqa: F811
    _, code, _ = _exam(client, db)
    headers = {"Authorization": f"Bearer {_join(client, code).json()['token']}"}
    client.post("/api/v1/one/client/consent", headers=headers)
    sid = _start(client, headers).json()["session"]["session_id"]
    client.post(f"/api/v1/one/client/sessions/{sid}/stop", headers=headers, json={"reason": reason})
    assert _signals(db, sid) == expected
    roster = client.get(f"/api/v1/one/exams/{db.get(ExamSession, sid).exam_id}/roster").json()
    assert roster["candidates"][0]["state"] == ("LEFT" if reason == "QUIT" else "SUBMITTED")


def test_rejoining_from_another_computer_and_wrong_clock(client, db):  # noqa: F811
    _, code, _ = _exam(client, db)
    first = {"Authorization": f"Bearer {_join(client, code, hostname='LAPTOP-A').json()['token']}"}
    client.post("/api/v1/one/client/consent", headers=first)
    sid1 = _start(client, first, cid="cs-a").json()["session"]["session_id"]
    second = {"Authorization": f"Bearer {_join(client, code, hostname='LAPTOP-B').json()['token']}"}
    assert "ONE_DEVICE_CHANGED" in _signals(db, sid1)
    skewed = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=10)).isoformat()
    sid2 = _start(client, second, cid="cs-b", client_time=skewed).json()["session"]["session_id"]
    assert "ONE_CLOCK_SKEW" in _signals(db, sid2)
    db.expire_all()
    assert db.get(ExamSession, sid1).status == "INTERRUPTED"


def test_candidate_token_expires(client, db):  # noqa: F811
    _, code, _ = _exam(client, db)
    token = _join(client, code).json()["token"]
    device = db.query(ClientDevice).filter(ClientDevice.kind == "CANDIDATE").one()
    device.expires_at = datetime.datetime.utcnow() - datetime.timedelta(minutes=1)
    db.commit()
    assert client.get("/api/v1/one/client/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_policy_update_validates_keys(client, db):  # noqa: F811
    exam_id, _, _ = _exam(client, db)
    _sign_in(client, db)
    assert client.put(f"/api/v1/one/exams/{exam_id}/policy", json={"microphone": False, "live_interval": 3}).json()["microphone"] is False
    assert client.put(f"/api/v1/one/exams/{exam_id}/policy", json={"hack": 1}).status_code == 422
