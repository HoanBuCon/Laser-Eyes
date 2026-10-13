"""A closed session's decisions are frozen with its approved report.

Decisions can no longer change through any review endpoint; what was left
undecided at closing leaves the PENDING queue and the Operations counts; a
chief proctor reopens the session, with a reason, to change anything.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.main import app
from storage.auth_service import create_user
from storage.database import Base, get_db
from storage.db_models import AuditLog, DetectionEvent, ExamRoom, ExamSession

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
PASSWORD = "correct-horse-1"


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


def _chief(client, db):
    create_user(db, "chief", "Chief", "CHIEF", PASSWORD)
    assert client.post("/api/v1/auth/login", json={"username": "chief", "password": PASSWORD}).status_code == 200


def _closed_session_with_leftovers(client, db):
    room = ExamRoom(room_code="R1", name="R1")
    db.add(room)
    db.flush()
    closed = ExamSession(room_id=room.id, exam_name="closed", status="INTERRUPTED")
    open_ = ExamSession(room_id=room.id, exam_name="open", status="COMPLETED")
    db.add_all([closed, open_])
    db.flush()
    for n, (sess, status) in enumerate([(closed, "PENDING"), (closed, "PENDING"), (closed, "CONFIRMED"), (open_, "PENDING")]):
        db.add(DetectionEvent(session_id=sess.id, room_id=room.id, event_id=f"e{n}", behavior="X", primary_signal="X",
                              severity="HIGH", risk_score=80 + n, review_status=status))
    db.commit()
    _chief(client, db)
    res = client.post(f"/api/v1/reports/sessions/{closed.id}/approve", json={"note": "two duplicates left"})
    assert res.status_code == 200, res.text
    return closed, open_


def test_no_review_endpoint_can_change_a_closed_session(client, db):
    closed, open_ = _closed_session_with_leftovers(client, db)
    body = {"decision": "REJECTED", "reason_code": "FALSE_POSITIVE", "note": "late"}
    assert client.post("/api/v1/events/e0/review", json=body).status_code == 409
    assert client.post("/api/v1/demo/events/e0/review", json={**body, "notes": "late"}).status_code == 409
    assert client.post("/api/v1/events/e3/review", json=body).status_code == 200  # open session still works
    db.expire_all()
    assert db.query(DetectionEvent).filter(DetectionEvent.event_id == "e0").one().review_status == "PENDING"


def test_proctor_bookmarks_of_a_closed_session_are_locked_too(client, db, monkeypatch):
    import storage.proctor_service as proctor_service
    from storage.review_service import SessionClosed

    closed, _ = _closed_session_with_leftovers(client, db)
    db.expire_all()  # the approval was committed through the API's own session
    monkeypatch.setattr(proctor_service, "get_bookmark", lambda db_, bid: type("B", (), {"session_id": closed.id})())
    with pytest.raises(SessionClosed):
        proctor_service.review_bookmark(db, bookmark_id="b1", reviewer_id="x", decision="REJECTED", reason_code=None, note="")


def test_leftovers_of_a_closed_session_leave_the_pending_queue_and_counts(client, db):
    _closed_session_with_leftovers(client, db)
    queue = client.get("/api/v1/proctor/review-queue").json()
    assert [i["event_id"] for i in queue["items"]] == ["e3"]
    assert queue["counts"]["PENDING"] == 1 and queue["closed_unresolved"] == 2
    everything = client.get("/api/v1/proctor/review-queue", params={"status": "ALL"}).json()
    locked = {i["event_id"]: i["session_closed"] for i in everything["items"]}
    assert locked == {"e0": True, "e1": True, "e2": True, "e3": False}

    stats = client.get("/api/v1/statistics/summary").json()
    assert stats["unreviewed_events_count"] == 1 and stats["closed_unresolved_count"] == 2
    room = client.get("/api/v1/statistics/rankings").json()[0]
    assert room["pending"] == 1 and room["risk_score"] == 83 and room["total_events"] == 4


def test_reopening_needs_a_chief_and_a_reason_and_restores_the_session(client, db):
    closed, _ = _closed_session_with_leftovers(client, db)
    assert client.post(f"/api/v1/reports/sessions/{closed.id}/reopen", json={"reason": ""}).status_code == 400
    res = client.post(f"/api/v1/reports/sessions/{closed.id}/reopen", json={"reason": "Appeal from candidate 12"})
    assert res.status_code == 200 and res.json()["status"] == "INTERRUPTED"  # the state it had before closing
    audit = db.query(AuditLog).filter(AuditLog.action == "REOPEN_SESSION").one()
    assert json.loads(audit.metadata_json)["reason"] == "Appeal from candidate 12"
    # Decisions are possible again, and the first approved version is kept
    assert client.post("/api/v1/events/e0/review", json={"decision": "REJECTED", "reason_code": "FALSE_POSITIVE"}).status_code == 200
    versions = client.get(f"/api/v1/reports/sessions/{closed.id}").json()["versions"]
    assert [v["version"] for v in versions] == [1]
    client.post("/api/v1/auth/logout")
    assert client.post(f"/api/v1/reports/sessions/{closed.id}/reopen", json={"reason": "anonymous try"}).status_code in (401, 409)


def test_review_page_shows_closed_sessions_read_only():
    js = Path("dashboard/js/review.js").read_text(encoding="utf-8")
    html = Path("dashboard/review.html").read_text(encoding="utf-8")
    assert "session_closed" in js and 'id="closedNotice"' in html
