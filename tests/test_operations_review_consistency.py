"""Operations is an overview; decisions are made only in the review queue.

Operations used to carry its own review modal (decisions saved as a fixed
"proctor_admin" with a different reason list) and ranked rooms by
``incident count × 15``, so a room whose incidents had all been rejected still
showed as maximum risk.  Both pages now read the same review-status data.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from api.main import app
from storage.database import Base, get_db
from storage.db_models import DetectionEvent, ExamRoom, ExamSession

engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)


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


def _room_with(db, code, statuses):
    room = ExamRoom(room_code=code, name=code)
    db.add(room)
    db.flush()
    session = ExamSession(room_id=room.id, exam_name=code, status="COMPLETED")
    db.add(session)
    db.flush()
    for n, (status, risk, severity) in enumerate(statuses):
        db.add(DetectionEvent(session_id=session.id, room_id=room.id, event_id=f"{code}-{n}", behavior="HEADS_TOGETHER",
                              primary_signal="HEADS_TOGETHER", severity=severity, risk_score=risk, review_status=status))
    db.commit()
    return room


def test_room_priority_reflects_only_incidents_still_waiting_for_a_decision(client, db):
    _room_with(db, "DECIDED", [("REJECTED", 95, "HIGH")] * 8)
    _room_with(db, "OPEN", [("PENDING", 72, "MEDIUM"), ("PENDING", 81, "HIGH"), ("CONFIRMED", 99, "HIGH")])
    _room_with(db, "EMPTY", [])
    rows = {r["room_code"]: r for r in client.get("/api/v1/statistics/rankings").json()}
    assert rows["DECIDED"]["risk_score"] == 0 and rows["DECIDED"]["pending"] == 0 and rows["DECIDED"]["rejected"] == 8
    assert rows["OPEN"]["risk_score"] == 81  # highest pending, not the confirmed 99
    assert (rows["OPEN"]["pending"], rows["OPEN"]["confirmed"], rows["OPEN"]["total_events"]) == (2, 1, 3)
    assert [r["room_code"] for r in client.get("/api/v1/statistics/rankings").json()][0] == "OPEN"


def test_operations_pending_number_matches_the_review_queue(client, db):
    _room_with(db, "R1", [("PENDING", 80, "HIGH"), ("PENDING", 60, "MEDIUM"), ("REJECTED", 90, "HIGH")])
    stats = client.get("/api/v1/statistics/summary").json()
    queue = client.get("/api/v1/proctor/review-queue", params={"source": "AI"}).json()
    assert stats["unreviewed_events_count"] == queue["counts"]["PENDING"] == 2
    assert stats["pending_by_severity"] == {"HIGH": 1, "MEDIUM": 1}


def test_review_queue_opens_one_incident_by_its_event_id(client, db):
    _room_with(db, "R1", [("REJECTED", 80, "HIGH"), ("PENDING", 60, "MEDIUM")])
    data = client.get("/api/v1/proctor/review-queue", params={"status": "ALL", "event_id": "R1-0"}).json()
    assert [i["event_id"] for i in data["items"]] == ["R1-0"]


def test_operations_has_no_review_controls_and_links_to_the_queue():
    js = Path("dashboard/js/app.js").read_text(encoding="utf-8")
    html = Path("dashboard/index.html").read_text(encoding="utf-8")
    for removed in ("submitReviewDecision", "/review`, {", "proctor_admin", "CLEAR_CHEATING", "/api/v1/events?"):
        assert removed not in js, removed
    assert "evidenceModal" not in html and "reviewReasonCode" not in html
    assert "/api/v1/proctor/review-queue" in js and "open: `AI:" in js
    assert "openLinkedItem" in Path("dashboard/js/review.js").read_text(encoding="utf-8")
