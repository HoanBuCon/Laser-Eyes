"""VIGIL One desktop -> VIGIL Hub sync: the client against the real API."""

from __future__ import annotations

import hashlib
import uuid

import cv2
import numpy as np
import pytest
import requests

from exam_monitor.hub_client import DISABLED, OFFLINE, SYNCED, UNPAIRED, HubClient, HubError
from storage.db_models import DetectionEvent, EvidenceFile, ExamSession, ProctorBookmark
from test_one_hub import _sign_in, client, db  # noqa: F401  (fixtures)


class Flaky:
    """Wraps the TestClient; while ``down`` every call fails like a dropped network."""

    def __init__(self, inner):
        self.inner = inner
        self.down = False
        self.calls = 0

    def _go(self, method, *args, **kwargs):
        self.calls += 1
        if self.down:
            raise requests.ConnectionError("network down")
        kwargs.pop("timeout", None)
        return getattr(self.inner, method)(*args, **kwargs)

    def post(self, *a, **k):
        return self._go("post", *a, **k)

    def get(self, *a, **k):
        return self._go("get", *a, **k)


@pytest.fixture(autouse=True)
def _one_evidence_root(tmp_path, monkeypatch):
    monkeypatch.setenv("VIGIL_ONE_EVIDENCE_ROOT", str(tmp_path / "one_evidence"))


def _jpeg_file(path, color=(30, 60, 90)) -> str:
    frame = np.zeros((40, 60, 3), dtype=np.uint8)
    frame[:] = color
    cv2.imwrite(str(path), frame)
    return str(path)


def _pair(client, db, tmp_path, http):  # noqa: F811
    _sign_in(client, db)
    code = client.post("/api/v1/one/pairing-codes").json()["code"]
    client.cookies.clear()
    hub = HubClient(tmp_path / "client", session=http)
    assert hub.status().state == DISABLED
    hub.pair("http://testserver", code, "Laptop SV01")
    return hub


def _drain(hub, limit=50):
    for _ in range(limit):
        if not hub.flush_once():
            return


def _event(event_id):
    return {"event_id": event_id, "event_type": "look_away", "severity": "medium", "started_at": "2026-10-14T09:00:00",
            "ended_at": "2026-10-14T09:00:04", "duration_seconds": 4.0, "session_elapsed_ms": 1000, "confidence": 0.7}


def test_full_session_reaches_the_hub(client, db, tmp_path):  # noqa: F811
    hub = _pair(client, db, tmp_path, Flaky(client))
    key = "20261014-090000-AB12"
    hub.open_session(key, {"candidate_ref": "SV-01", "exam_name": "Thi thử", "simulation": True})
    hub.add_event(key, _event("ev-1"), evidence_path=_jpeg_file(tmp_path / "ev1.jpg"))
    hub.add_bookmark(key, {"request_id": "rq-1", "frame_id": 5, "session_elapsed_ms": 2000, "note": "đánh dấu"},
                     _jpeg_file(tmp_path / "bm.jpg", (200, 0, 0)))
    hub.stop_session(key, {"frame_count": 300, "average_fps": 29.0})
    assert hub.status().pending == 5
    _drain(hub)
    status = hub.status()
    assert status.state == SYNCED and status.pending == 0 and status.failed == 0
    db.expire_all()
    session = db.query(ExamSession).one()
    assert session.client_session_id == key and session.status == "COMPLETED" and session.total_frames == 300
    event = db.query(DetectionEvent).one()
    evidence = db.query(EvidenceFile).one()
    assert event.event_id == "ev-1" and evidence.event_id == event.id
    assert evidence.video_sha256 == hashlib.sha256((tmp_path / "ev1.jpg").read_bytes()).hexdigest()
    assert db.query(ProctorBookmark).one().request_id == "rq-1"


def test_network_drop_keeps_items_and_resends_without_duplicates(client, db, tmp_path):  # noqa: F811
    http = Flaky(client)
    hub = _pair(client, db, tmp_path, http)
    key = "S-OFFLINE"
    hub.open_session(key, {"candidate_ref": "SV-02"})
    _drain(hub)
    http.down = True
    for i in range(3):
        hub.add_event(key, _event(f"off-{i}"))
    with pytest.raises(HubError):
        hub.flush_once()
    assert hub.status().state == OFFLINE and hub.status().pending == 3
    http.down = False
    _drain(hub)
    assert hub.status().state == SYNCED
    # a duplicate resend (e.g. the app crashed after sending, before deleting) is harmless
    hub.add_event(key, _event("off-0"))
    _drain(hub)
    db.expire_all()
    assert sorted(e.event_id for e in db.query(DetectionEvent)) == ["off-0", "off-1", "off-2"]


def test_invalid_item_is_parked_and_does_not_block_the_queue(client, db, tmp_path):  # noqa: F811
    hub = _pair(client, db, tmp_path, Flaky(client))
    key = "S-BAD"
    hub.open_session(key, {"candidate_ref": "SV-03"})
    hub.add_event(key, {**_event("bad-1"), "event_type": "not_a_signal"})
    hub.add_event(key, _event("good-1"))
    _drain(hub)
    status = hub.status()
    assert status.pending == 0 and status.failed == 1
    db.expire_all()
    assert [e.event_id for e in db.query(DetectionEvent)] == ["good-1"]


def test_revoked_device_stops_syncing_and_keeps_data(client, db, tmp_path):  # noqa: F811
    hub = _pair(client, db, tmp_path, Flaky(client))
    hub.open_session("S-REV", {"candidate_ref": "SV-04"})
    _drain(hub)
    _sign_in(client, db)
    device_id = client.get("/api/v1/one/devices").json()[0]["id"]
    client.delete(f"/api/v1/one/devices/{device_id}")
    client.cookies.clear()
    hub.add_event("S-REV", _event("after-revoke"))
    with pytest.raises(HubError):
        hub.flush_once()
    assert hub.status().state == UNPAIRED and hub.status().pending == 1


def test_wrong_pairing_code_is_reported(client, db, tmp_path):  # noqa: F811
    hub = HubClient(tmp_path / "c2", session=Flaky(client))
    with pytest.raises(HubError, match="Mã ghép nối"):
        hub.pair("http://testserver", "ZZZZZZ")
    assert hub.status().state == DISABLED


def test_unpaired_client_records_nothing(tmp_path):
    hub = HubClient(tmp_path / "c3")
    hub.open_session("S", {"candidate_ref": "x"})
    hub.add_event("S", _event(str(uuid.uuid4())))
    assert hub.status().pending == 0 and hub.status().state == DISABLED
