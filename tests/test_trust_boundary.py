"""What the Hub decides, not the candidate's computer: where a session is analysed, which signals
count as the Hub's own, where evidence files go, and the names of downloads."""

from __future__ import annotations

from pathlib import Path

import pytest

from storage import one_service as one
from storage.db_models import DetectionEvent, EvidenceFile, ExamSession
from test_one_hub import _event, _jpeg, _open, _paired, client, db  # noqa: F401  (fixtures)
from test_online_exam import _exam, _join, _start


@pytest.fixture(autouse=True)
def _one_evidence_root(tmp_path, monkeypatch):
    monkeypatch.setenv("VIGIL_ONE_EVIDENCE_ROOT", str(tmp_path / "one_evidence"))


def _candidate(client, db):  # noqa: F811
    _, code, _ = _exam(client, db)
    headers = {"Authorization": f"Bearer {_join(client, code).json()['token']}"}
    assert client.post("/api/v1/one/client/consent", headers=headers).status_code == 200
    return headers


def test_a_candidate_cannot_ask_for_on_device_analysis(client, db):  # noqa: F811
    headers = _candidate(client, db)
    started = _start(client, headers, analysis="client")
    assert started.status_code == 200, started.text
    assert started.json()["session"]["analysis_mode"] == "SERVER"


def test_a_candidate_cannot_open_a_simulation_from_another_computer(client, db, monkeypatch):  # noqa: F811
    from api.routes import one as one_routes

    headers = _candidate(client, db)
    monkeypatch.setattr(one_routes, "_from_this_computer", lambda request: False)
    assert _start(client, headers, simulation=True).status_code == 422
    monkeypatch.setattr(one_routes, "_from_this_computer", lambda request: True)  # vigil.py --sim on the Hub
    assert _start(client, headers, cid="cs-sim", simulation=True).json()["session"]["simulation"] is True


def test_a_client_cannot_pass_its_signals_off_as_the_hubs(client, db):  # noqa: F811
    headers = _paired(client, db)
    sid = _open(client, headers, analysis="server")["session"]["session_id"]
    forged = {**_event(event_type="look_away"), "_server": True, "config_version": "vigil-one-server-v2"}
    res = client.post(f"/api/v1/one/client/sessions/{sid}/events", headers=headers, json={"events": [forged]})
    assert res.status_code == 422
    assert db.query(DetectionEvent).count() == 0
    legacy = _open(client, headers, cid="cs-legacy")["session"]["session_id"]  # on-device analysis (paired app)
    res = client.post(f"/api/v1/one/client/sessions/{legacy}/events", headers=headers, json={"events": [forged]})
    assert res.status_code == 200
    event = db.query(DetectionEvent).one()
    assert event.model_version == "vigil-one-mediapipe" and event.config_version == "vigil-one-client"


def test_session_start_time_from_a_wrong_clock_is_not_trusted(client, db):  # noqa: F811
    headers = _paired(client, db)
    sid = _open(client, headers, started_at="2020-01-01T00:00:00+00:00")["session"]["session_id"]
    import datetime

    started = db.get(ExamSession, sid).started_at
    assert abs((datetime.datetime.utcnow() - started).total_seconds()) < 60


@pytest.mark.parametrize("bad_id", ["../../../escape", "..\\..\\escape", "/etc/passwd", "C:\\x", "a b", "x" * 37])
def test_event_ids_that_could_name_files_are_refused(client, db, bad_id):  # noqa: F811
    headers = _paired(client, db)
    sid = _open(client, headers)["session"]["session_id"]
    res = client.post(f"/api/v1/one/client/sessions/{sid}/events", headers=headers,
                      json={"events": [_event(event_id=bad_id)]})
    assert res.status_code == 422


def test_evidence_files_are_named_by_the_hub_inside_its_folder(client, db, tmp_path):  # noqa: F811
    headers = _paired(client, db)
    sid = _open(client, headers)["session"]["session_id"]
    client.post(f"/api/v1/one/client/sessions/{sid}/events", headers=headers, json={"events": [_event(event_id="EV-1")]})
    jpeg = _jpeg()
    import hashlib

    res = client.post("/api/v1/one/client/events/EV-1/evidence", headers=headers,
                      files={"image": ("e.jpg", jpeg, "image/jpeg")}, data={"sha256": hashlib.sha256(jpeg).hexdigest()})
    assert res.status_code == 200, res.text
    event = db.query(DetectionEvent).one()
    path = Path(db.query(EvidenceFile).one().snapshot_path).resolve()
    root = (tmp_path / "one_evidence").resolve()
    assert root in path.parents and path.name == f"{event.id}.jpg"


def test_evidence_path_check_refuses_an_escaping_session_folder(db, tmp_path):  # noqa: F811
    event = DetectionEvent(id="ok-id", session_id="../../outside", event_id="EV-2")
    with pytest.raises(one.OneError):
        one._evidence_path(event, ".jpg")


def test_csv_download_name_with_vietnamese():
    from api.routes.reports import attachment_header

    header = attachment_header("session-Kỳ thi cuối học kỳ — Đợt 1", ".csv")
    header.encode("latin-1")  # what the HTTP server does with headers
    assert 'filename="session-Ky_thi_cuoi_hoc_ky' in header
    assert "filename*=UTF-8''session-K%E1%BB%B3_thi" in header
