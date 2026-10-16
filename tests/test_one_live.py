"""VIGIL One live stream: frames over a WebSocket analysed by the Hub as they arrive,
the recorded segments filling only what the stream missed, and the proctor's live view
and commands on the web."""

from __future__ import annotations

import json

import cv2
import numpy as np
import pytest

from storage import one_analysis, one_live
from storage.db_models import DetectionEvent, EvidenceFile, ExamSession, ProctorBookmark, SessionVideoSegment
from test_one_hub import client, db  # noqa: F401  (fixtures)
from test_one_server_analysis import (BRIGHT, NORMAL, ScriptedAnalyzer, _one_evidence_root,  # noqa: F401
                                      _segments, _server_session, _upload)


class CalibratingAnalyzer(ScriptedAnalyzer):
    """Also answers the proctor's commands."""

    def __init__(self):
        super().__init__()
        self.materials = None
        self.recalibrations = 0

    def begin_calibration(self):
        self.recalibrations += 1
        self.calibrated = False
        self.frames = 0

    def set_allowed_materials(self, labels):
        self.materials = set(labels)


@pytest.fixture
def service(db, monkeypatch):  # noqa: F811
    created = []

    def factory(policy):
        analyzer = CalibratingAnalyzer()
        created.append(analyzer)
        return analyzer

    svc = one_analysis.OneAnalysisService(lambda: db, workers=1, analyzer_factory=factory)
    svc.created = created
    monkeypatch.setattr(one_analysis, "_service", svc)
    yield svc
    monkeypatch.setattr(one_analysis, "_service", None)


CLOCK = {"base": 1_000_000.0, "t": 0.0}


@pytest.fixture(autouse=True)
def _fresh_slots(monkeypatch):
    one_live._slots.clear()
    monkeypatch.setattr(one_live, "_clock", lambda: CLOCK["base"] + CLOCK["t"])
    CLOCK["t"] = 0.0
    yield
    one_live._slots.clear()


def _jpeg(value: int) -> bytes:
    frame = np.full((120, 160, 3), value, dtype=np.uint8)
    ok, enc = cv2.imencode(".jpg", frame)
    assert ok
    return enc.tobytes()


def _stream(svc, db, sid, brightness, start_s: float, end_s: float, fps: int = 5):  # noqa: F811
    """Frames as the WebSocket would deliver them, analysed one by one."""
    t = start_s
    while t < end_s:
        CLOCK["t"] = t + 0.05  # the Hub receives each frame just after the app took it
        one_live.put_frame(sid, _jpeg(brightness(t)), t * 1000.0)
        svc.process_live(db, sid)
        t += 1 / fps


def test_message_format_round_trips():
    jpeg = _jpeg(NORMAL)
    header, body = one_live.parse_message(one_live.build_message({"t": 1234.5, "voice": True}, jpeg))
    assert header == {"t": 1234.5, "voice": True} and body == jpeg
    with pytest.raises(ValueError):
        one_live.parse_message(b"\x00\x00\x10\x00{}")
    with pytest.raises(ValueError):
        one_live.put_frame("s", b"not a jpeg", 0)


def test_websocket_frames_reach_the_analysis_and_the_app_hears_back(client, db, service):  # noqa: F811
    headers, sid = _server_session(client, db)
    with client.websocket_connect(f"/api/v1/one/client/sessions/{sid}/live", headers=headers) as ws:
        ws.send_bytes(one_live.build_message({"t": 100.0, "voice": None}, _jpeg(NORMAL)))
        reply = json.loads(ws.receive_text())
        assert reply["ended"] is False and reply["stream"]["live"] is True
    assert one_live.latest(sid).t_ms == 100.0
    assert one_live.stats(sid)["live"] is False  # the socket closed
    assert service.process_live(db, sid) == 0
    state = json.loads(db.get(ExamSession, sid).analysis_state_json)
    assert state["live_frames"] == 1 and state["covered"] == [[100.0, 100.0]]


def test_websocket_refuses_unknown_devices_and_client_sessions(client, db, service):  # noqa: F811
    from starlette.websockets import WebSocketDisconnect

    headers, sid = _server_session(client, db)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/api/v1/one/client/sessions/{sid}/live",
                                      headers={"Authorization": "Bearer nope"}) as ws:
            ws.receive_text()
    from test_one_hub import _open

    legacy = _open(client, headers, cid="cs-legacy")["session"]["session_id"]
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/api/v1/one/client/sessions/{legacy}/live", headers=headers) as ws:
            ws.receive_text()


def test_live_signal_gets_its_clip_from_the_recording_and_segments_do_not_repeat_it(client, db, service, tmp_path):  # noqa: F811
    headers, sid = _server_session(client, db)
    scene = lambda t: BRIGHT if 2.4 <= t < 6.0 else NORMAL  # noqa: E731  nobody 2.4-6 s
    _stream(service, db, sid, scene, 0.0, 10.0)
    event = db.query(DetectionEvent).filter_by(session_id=sid).one()
    assert event.primary_signal == "ONE_NO_FACE"
    evidence = db.query(EvidenceFile).filter_by(event_id=event.id).one()
    assert evidence.snapshot_path and not evidence.video_path  # the clip waits for the recording

    for seg in _segments(tmp_path / "rec", scene, 10.0):
        _upload(client, headers, sid, seg)
    result = service.process_session(db, sid)
    assert result == {"segments": 5, "events": 0}  # the live stream already covered every moment
    assert {s.frames_analyzed for s in db.query(SessionVideoSegment).filter_by(session_id=sid)} == {0}
    db.expire_all()
    evidence = db.query(EvidenceFile).filter_by(event_id=event.id).one()
    assert evidence.video_path and open(evidence.video_path, "rb").read()[4:8] == b"ftyp"
    assert db.query(DetectionEvent).filter_by(session_id=sid).count() == 1


def test_a_hole_in_the_live_stream_is_analysed_from_the_recording(client, db, service, tmp_path):  # noqa: F811
    headers, sid = _server_session(client, db)
    scene = lambda t: BRIGHT if 3.4 <= t < 7.4 else NORMAL  # noqa: E731  nobody while the stream was down
    _stream(service, db, sid, scene, 0.0, 3.0)
    _stream(service, db, sid, scene, 8.0, 10.0)
    assert db.query(DetectionEvent).filter_by(session_id=sid).count() == 0  # the live stream saw nothing
    for seg in _segments(tmp_path / "rec", scene, 10.0):
        _upload(client, headers, sid, seg)
    result = service.process_session(db, sid)
    assert result["events"] == 1
    event = db.query(DetectionEvent).filter_by(session_id=sid).one()
    meta = json.loads(event.incident_metadata_json)
    assert event.primary_signal == "ONE_NO_FACE" and 3000 <= meta["first_seen_ms"] <= 4200
    frames = sum(s.frames_analyzed for s in db.query(SessionVideoSegment).filter_by(session_id=sid))
    assert 15 <= frames <= 30  # only the ~5 s hole, not the whole recording
    assert len(service.created) == 2  # the main analyser and one for the hole


def test_without_a_live_stream_the_recording_is_analysed_as_before(client, db, service, tmp_path):  # noqa: F811
    headers, sid = _server_session(client, db)
    scene = lambda t: BRIGHT if 2.4 <= t < 6.0 else NORMAL  # noqa: E731
    for seg in _segments(tmp_path / "rec", scene, 10.0):
        _upload(client, headers, sid, seg)
    assert service.process_session(db, sid)["events"] == 1
    assert len(service.created) == 1


def test_proctor_watches_recalibrates_allows_materials_and_marks(client, db, service):  # noqa: F811
    headers, sid = _server_session(client, db)
    _stream(service, db, sid, lambda t: NORMAL, 0.0, 1.0)
    analyzer = service.created[0]
    assert analyzer.calibrated

    assert one_live.view(sid) is not None and one_live.watched(sid)
    _stream(service, db, sid, lambda t: NORMAL, 1.0, 1.4)
    seq, picture = one_live.view(sid)
    assert picture[:2] == b"\xff\xd8" and one_live._slots[sid].annotated is not None  # drawn for the viewer

    res = client.post(f"/api/v1/one/sessions/{sid}/recalibrate")
    assert res.status_code == 200 and analyzer.recalibrations == 1 and not analyzer.calibrated
    res = client.post(f"/api/v1/one/sessions/{sid}/materials", json={"allowed": True})
    assert res.status_code == 200 and analyzer.materials == {"book"}
    state = json.loads(db.get(ExamSession, sid).analysis_state_json)
    assert state["allow_materials"] is True

    res = client.post(f"/api/v1/one/sessions/{sid}/mark", json={"note": "nhìn sang bên"})
    assert res.status_code == 200, res.text
    bookmark = db.query(ProctorBookmark).filter_by(session_id=sid).one()
    assert bookmark.note == "nhìn sang bên"

    monitor = client.get(f"/api/v1/one/sessions/{sid}/monitor").json()
    assert monitor["analysis_mode"] == "SERVER" and "stream" in monitor
    assert monitor["live"]["analysis"]["allow_materials"] is True


def test_commands_need_a_live_frame_or_a_server_session(client, db, service):  # noqa: F811
    headers, sid = _server_session(client, db)
    assert client.post(f"/api/v1/one/sessions/{sid}/mark", json={}).status_code == 409
    from test_one_hub import _open

    legacy = _open(client, headers, cid="cs-legacy")["session"]["session_id"]
    assert client.post(f"/api/v1/one/sessions/{legacy}/recalibrate").status_code == 409


def test_mjpeg_stream_sends_the_newest_picture(client, db, service, monkeypatch):  # noqa: F811
    from api.routes import one as one_routes

    monkeypatch.setattr(one_routes, "MJPEG_IDLE_SECONDS", 0.5)  # the test client never reports the disconnect
    headers, sid = _server_session(client, db)
    _stream(service, db, sid, lambda t: NORMAL, 0.0, 0.6)
    with client.stream("GET", f"/api/v1/one/sessions/{sid}/live.mjpg") as res:
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("multipart/x-mixed-replace")
        chunk = b""
        for part in res.iter_bytes():
            chunk += part
            if b"\xff\xd9" in chunk:
                break
    assert chunk.startswith(b"--frame\r\nContent-Type: image/jpeg") and b"\xff\xd8" in chunk



# ---------------------------------------------------------------- the frame clock is held to the Hub's

def test_frame_times_must_move_forward_and_not_outrun_the_hub_clock():
    jpeg = _jpeg(NORMAL)
    CLOCK["t"] = 10.0
    one_live.put_frame("s", jpeg, 5000.0, elapsed_ms=10_000)
    with pytest.raises(ValueError):
        one_live.put_frame("s", jpeg, 5000.0)                 # not forward
    CLOCK["t"] = 11.0
    with pytest.raises(ValueError):
        one_live.put_frame("s", jpeg, 3_605_000.0)            # claims an hour went by in one second
    one_live.put_frame("s", jpeg, 6000.0)                     # real time: accepted
    assert one_live.stats("s")["refused"] == 2
    with pytest.raises(ValueError):                           # a first frame far in the future of the session
        one_live.put_frame("other", jpeg, 7_200_000.0, elapsed_ms=30_000)


def test_live_frames_with_invented_times_cannot_hide_the_recording(client, db, service, tmp_path):  # noqa: F811
    """A stream claiming to cover the whole exam is refused, so the recording is still analysed."""
    headers, sid = _server_session(client, db)
    with client.websocket_connect(f"/api/v1/one/client/sessions/{sid}/live", headers=headers) as ws:
        ws.send_bytes(one_live.build_message({"t": 1000.0, "voice": None}, _jpeg(NORMAL)))
        ws.receive_text()
        ws.send_bytes(one_live.build_message({"t": 3_600_000.0, "voice": None}, _jpeg(NORMAL)))
        assert "error" in json.loads(ws.receive_text())
    service.process_live(db, sid)
    scene = lambda t: BRIGHT if 2.4 <= t < 6.0 else NORMAL  # noqa: E731
    for seg in _segments(tmp_path / "rec", scene, 10.0):
        _upload(client, headers, sid, seg)
    assert service.process_session(db, sid)["events"] == 1  # the absence was found in the recording


# ---------------------------------------------------------------- nothing is lost or closed too early

def _stop(client, headers, sid):  # noqa: F811
    res = client.post(f"/api/v1/one/client/sessions/{sid}/stop", headers=headers, json={"reason": "SUBMITTED"})
    assert res.status_code == 200, res.text


def _clips(db, sid):  # noqa: F811
    db.expire_all()
    return json.loads(db.get(ExamSession, sid).analysis_state_json or "{}").get("pending_clips") or []


def test_a_clip_waits_for_video_that_arrives_late(client, db, service, tmp_path):  # noqa: F811
    headers, sid = _server_session(client, db)
    scene = lambda t: BRIGHT if 2.4 <= t < 6.0 else NORMAL  # noqa: E731
    _stream(service, db, sid, scene, 0.0, 10.0)
    segs = _segments(tmp_path / "rec", scene, 10.0)
    _upload(client, headers, sid, segs[0])                    # only the first seconds so far
    service.process_session(db, sid)
    assert len(_clips(db, sid)) == 1                          # still waiting, not dropped
    for seg in segs[1:]:                                      # the rest arrives after a network drop
        _upload(client, headers, sid, seg)
    service.process_session(db, sid)
    assert _clips(db, sid) == []
    evidence = db.query(EvidenceFile).one()
    assert evidence.video_path and evidence.error_message is None


def test_a_clip_whose_video_never_comes_is_reported_not_dropped(client, db, service, monkeypatch):  # noqa: F811
    headers, sid = _server_session(client, db)
    _stream(service, db, sid, lambda t: BRIGHT if 2.4 <= t < 6.0 else NORMAL, 0.0, 10.0)
    _stop(client, headers, sid)
    service.process_session(db, sid)
    assert len(_clips(db, sid)) == 1                          # within the grace period: still owed
    monkeypatch.setattr(one_analysis, "CLIP_GIVE_UP_SECONDS", 0.0)
    analysers = len(service.created)
    service.process_session(db, sid)
    assert _clips(db, sid) == []
    assert "video" in db.query(EvidenceFile).one().error_message
    assert len(service.created) == analysers                  # no face model loaded again for that


def test_report_waits_for_the_hub_or_a_written_reason(client, db, service, tmp_path):  # noqa: F811
    from test_one_hub import _sign_in

    headers, sid = _server_session(client, db)
    for seg in _segments(tmp_path / "rec", lambda t: NORMAL, 4.0):
        _upload(client, headers, sid, seg)                    # uploaded, not analysed yet
    _stop(client, headers, sid)
    _sign_in(client, db)
    res = client.post(f"/api/v1/reports/sessions/{sid}/approve", json={})
    assert res.status_code == 409 and "chưa được phân tích" in res.json()["detail"]
    res = client.post(f"/api/v1/reports/sessions/{sid}/approve", json={"note": "Hội đồng chốt theo biên bản"})
    assert res.status_code == 200, res.text
    report = client.get(f"/api/v1/reports/sessions/{sid}", params={"version": 1}).json()["report"]
    assert report["approval"]["analysis_incomplete"]          # the reason and what was missing are on record
    service.process_session(db, sid)                          # closed: the rest is skipped, not turned into signals
    assert {s.analysis_status for s in db.query(SessionVideoSegment).filter_by(session_id=sid)} == {"SKIPPED"}


def test_report_of_a_fully_analysed_session_needs_no_reason(client, db, service, tmp_path):  # noqa: F811
    from test_one_hub import _sign_in

    headers, sid = _server_session(client, db)
    for seg in _segments(tmp_path / "rec", lambda t: NORMAL, 4.0):
        _upload(client, headers, sid, seg)
    _stop(client, headers, sid)
    service.process_session(db, sid)
    _sign_in(client, db)
    assert one_analysis.analysis_backlog(db, db.get(ExamSession, sid)) == []
    assert client.post(f"/api/v1/reports/sessions/{sid}/approve", json={}).status_code == 200
