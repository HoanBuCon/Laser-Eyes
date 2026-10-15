"""VIGIL One analysed on the Hub (analysis_mode SERVER).

The candidate's computer only uploads video segments; ``storage.one_analysis`` turns them into
review incidents with snapshot and clip.  A scripted analyser stands in for MediaPipe so the
behaviour of the pipeline (order, gaps, restart, speech, evidence) is tested deterministically.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from exam_monitor.models import AnalysisResult
from exam_monitor.session_recorder import SessionRecorder
from storage import one_analysis
from storage import online_exam_service as online
from storage.db_models import DetectionEvent, EvidenceFile, ExamSession, SessionVideoSegment
from test_one_hub import _event, _open, _paired, client, db  # noqa: F401  (fixtures)

BRIGHT = 230   # a frame this bright = "nobody in front of the camera" for the scripted analyser
NORMAL = 90


@pytest.fixture(autouse=True)
def _one_evidence_root(tmp_path, monkeypatch):
    monkeypatch.setenv("VIGIL_ONE_EVIDENCE_ROOT", str(tmp_path / "one_evidence"))


class ScriptedAnalyzer:
    """Reads the scene from the frame: very bright = no face; calibrates after 3 frames."""

    def __init__(self, talking_score: float = 0.0):
        self.frames = 0
        self.talking_score = talking_score
        self.restored = None
        self.calibrated = False

    def analyze(self, frame, timestamp):
        self.frames += 1
        if self.frames >= 3:
            self.calibrated = True
        mean = float(frame.mean())
        if mean > 180:
            return AnalysisResult(timestamp=timestamp, face_count=0, brightness=mean, calibrated=self.calibrated)
        return AnalysisResult(timestamp=timestamp, face_count=1, head_direction="center", eye_direction="center",
                              brightness=mean, calibrated=self.calibrated, talking_score=self.talking_score,
                              confidence=0.9)

    def calibration_snapshot(self):
        return {"calibrated": self.calibrated, "baseline_x": 0.1, "baseline_y": -0.05, "quality": 0.8,
                "state": "READY" if self.calibrated else "COLLECTING"}

    def restore_calibration(self, snapshot):
        self.restored = snapshot
        self.calibrated = bool(snapshot.get("calibrated"))


def _segments(folder: Path, brightness, seconds: float, *, fps=5, segment_seconds=2.0) -> list[tuple]:
    """Record ``seconds`` of a fake camera whose brightness follows ``brightness(t)``."""
    done = []
    recorder = SessionRecorder(folder, lambda *seg: done.append(seg), fps=fps, width=160,
                               segment_seconds=segment_seconds, started=0.0)
    frame = np.zeros((120, 160, 3), dtype=np.uint8)
    t = 0.0
    while t < seconds:
        frame[:] = brightness(t)
        recorder.push(frame, now=t)
        t += 1 / 30
    recorder.close()
    return done


def _upload(client, headers, sid, seg, speech=None):  # noqa: F811
    seq, path, start_ms, duration_s = seg
    data = Path(path).read_bytes()
    form = {"seq": seq, "start_ms": start_ms, "duration_s": duration_s, "sha256": hashlib.sha256(data).hexdigest()}
    if speech is not None:
        form["speech"] = json.dumps(speech)
    res = client.post(f"/api/v1/one/client/sessions/{sid}/video", headers=headers,
                      files={"video": (f"s{seq}.mp4", data, "video/mp4")}, data=form)
    assert res.status_code == 200, res.text
    return res.json()


def _service(db, factory=None):  # noqa: F811
    return one_analysis.OneAnalysisService(lambda: db, workers=1,
                                           analyzer_factory=factory or (lambda policy: ScriptedAnalyzer()))


def _server_session(client, db, cid="cs-srv"):  # noqa: F811
    headers = _paired(client, db)
    sid = _open(client, headers, cid=cid, analysis="server")["session"]["session_id"]
    return headers, sid


# ---------------------------------------------------------------- session mode and policy

def test_session_mode_comes_from_the_client_and_simulation_stays_client(client, db):  # noqa: F811
    headers = _paired(client, db)
    server = _open(client, headers, cid="cs-a", analysis="server")["session"]
    legacy = _open(client, headers, cid="cs-b")["session"]
    simulated = _open(client, headers, cid="cs-c", analysis="server", simulation=True)["session"]
    assert server["analysis_mode"] == "SERVER"
    assert legacy["analysis_mode"] == "CLIENT"
    assert simulated["analysis_mode"] == "CLIENT"  # scripted demo sessions keep their own signals


def test_policy_defaults_and_validation(db):  # noqa: F811
    from storage.db_models import Exam

    exam = Exam(name="Thi thử")
    db.add(exam)
    db.commit()
    policy = online.policy_for(exam)
    assert policy["analysis_mode"] == "server" and policy["segment_seconds"] == 10 and policy["analysis_fps"] == 5
    with pytest.raises(online.OnlineExamError):
        online.update_policy(db, exam, {"analysis_mode": "cloud"})
    changed = online.update_policy(db, exam, {"analysis_mode": "client", "segment_seconds": 1, "analysis_fps": 99})
    assert changed["analysis_mode"] == "client"
    assert changed["segment_seconds"] == 4 and changed["analysis_fps"] == changed["video_fps"]


def test_server_session_only_takes_device_facts_from_the_client(client, db):  # noqa: F811
    headers, sid = _server_session(client, db)
    look = client.post(f"/api/v1/one/client/sessions/{sid}/events", headers=headers,
                       json={"events": [_event(event_type="look_away")]})
    assert look.status_code == 422  # the Hub raises perception signals itself
    fact = client.post(f"/api/v1/one/client/sessions/{sid}/events", headers=headers,
                       json={"events": [_event(event_type="virtual_camera")]})
    assert fact.status_code == 200 and fact.json()["created"] == 1


# ---------------------------------------------------------------- analysis

def test_segments_become_incidents_with_snapshot_and_clip(client, db, tmp_path):  # noqa: F811
    headers, sid = _server_session(client, db)
    # 0-2.4 s candidate present, 2.4-6 s nobody (NO_FACE needs 3 s), then present again
    segs = _segments(tmp_path / "rec", lambda t: BRIGHT if 2.4 <= t < 6.0 else NORMAL, 10.0)
    assert len(segs) == 5
    for seg in segs:
        _upload(client, headers, sid, seg)
    rows = db.query(SessionVideoSegment).filter_by(session_id=sid).all()
    assert {r.analysis_status for r in rows} == {"PENDING"}

    result = _service(db).process_session(db, sid)
    assert result["segments"] == 5 and result["events"] == 1
    db.expire_all()
    assert {r.analysis_status for r in db.query(SessionVideoSegment).filter_by(session_id=sid)} == {"DONE"}
    event = db.query(DetectionEvent).filter_by(session_id=sid).one()
    assert event.primary_signal == "ONE_NO_FACE" and event.source_product == "VIGIL_ONE"
    assert event.model_version == "vigil-one-server" and event.review_status == "PENDING"
    meta = json.loads(event.incident_metadata_json)
    assert 2000 <= meta["first_seen_ms"] <= 3000  # when the candidate left, on the video's clock
    evidence = db.query(EvidenceFile).filter_by(event_id=event.id).one()
    assert Path(evidence.snapshot_path).read_bytes()[:2] == b"\xff\xd8"
    clip = Path(evidence.video_path).read_bytes()
    assert clip[4:8] == b"ftyp" and evidence.video_sha256 == hashlib.sha256(clip).hexdigest()
    session = db.get(ExamSession, sid)
    live = json.loads(session.live_status_json)
    assert live["analysis"]["mode"] == "SERVER" and live["analysis"]["calibration"] == "READY"
    # running it again changes nothing (idempotent)
    assert _service(db).process_session(db, sid) == {"segments": 0, "events": 0}


def test_a_missing_segment_holds_the_session_then_is_skipped(client, db, tmp_path, monkeypatch):  # noqa: F811
    headers, sid = _server_session(client, db)
    segs = _segments(tmp_path / "rec", lambda t: NORMAL, 6.0)
    _upload(client, headers, sid, segs[0])
    _upload(client, headers, sid, segs[2])  # segment 1 still in the candidate's outbox
    service = _service(db)
    assert service.process_session(db, sid)["segments"] == 1
    db.expire_all()
    status = {r.seq: r.analysis_status for r in db.query(SessionVideoSegment).filter_by(session_id=sid)}
    assert status == {0: "DONE", 2: "PENDING"}
    monkeypatch.setattr(one_analysis, "GAP_WAIT_SECONDS", 0.0)  # it never came
    assert service.process_session(db, sid)["segments"] == 1
    db.expire_all()
    late = _upload(client, headers, sid, segs[1])  # arrives after the session moved on
    assert late["created"] is True
    service.process_session(db, sid)
    db.expire_all()
    status = {r.seq: r.analysis_status for r in db.query(SessionVideoSegment).filter_by(session_id=sid)}
    assert status == {0: "DONE", 1: "SKIPPED", 2: "DONE"}


def test_a_restarted_server_resumes_with_the_saved_calibration(client, db, tmp_path):  # noqa: F811
    headers, sid = _server_session(client, db)
    segs = _segments(tmp_path / "rec", lambda t: NORMAL, 4.0)
    _upload(client, headers, sid, segs[0])
    _service(db).process_session(db, sid)
    saved = json.loads(db.get(ExamSession, sid).analysis_state_json)
    assert saved["next_seq"] == 1 and saved["calibration"]["calibrated"] is True

    made = []

    def factory(policy):
        made.append(ScriptedAnalyzer())
        return made[-1]

    _upload(client, headers, sid, segs[1])
    _service(db, factory).process_session(db, sid)  # a new process: no state in memory
    assert made and made[0].restored["calibrated"] is True and made[0].restored["baseline_x"] == 0.1
    db.expire_all()
    assert db.query(SessionVideoSegment).filter_by(session_id=sid, seq=1).one().analysis_status == "DONE"


def test_microphone_speech_is_fused_with_lip_movement(client, db, tmp_path):  # noqa: F811
    headers, sid = _server_session(client, db)
    segs = _segments(tmp_path / "rec", lambda t: NORMAL, 4.0)
    _upload(client, headers, sid, segs[0], speech=[[200.0, 1900.0]])  # a voice for 1.7 s
    _upload(client, headers, sid, segs[1], speech=[])
    stored = db.query(SessionVideoSegment).filter_by(session_id=sid, seq=0).one()
    assert json.loads(stored.speech_json) == [[200.0, 1900.0]]
    service = _service(db, lambda policy: ScriptedAnalyzer(talking_score=0.6))
    assert service.process_session(db, sid)["events"] == 1
    event = db.query(DetectionEvent).filter_by(session_id=sid).one()
    assert event.primary_signal == "ONE_TALKING"


def test_lips_alone_do_not_raise_talking_when_the_microphone_heard_nothing(client, db, tmp_path):  # noqa: F811
    headers, sid = _server_session(client, db)
    for seg in _segments(tmp_path / "rec", lambda t: NORMAL, 4.0):
        _upload(client, headers, sid, seg, speech=[])
    assert _service(db, lambda policy: ScriptedAnalyzer(talking_score=0.6)).process_session(db, sid)["events"] == 0


def test_client_mode_segments_are_not_analysed(client, db, tmp_path):  # noqa: F811
    headers = _paired(client, db)
    sid = _open(client, headers, cid="cs-legacy")["session"]["session_id"]
    for seg in _segments(tmp_path / "rec", lambda t: BRIGHT, 4.0):
        _upload(client, headers, sid, seg)
    assert {r.analysis_status for r in db.query(SessionVideoSegment).filter_by(session_id=sid)} == {None}
    assert _service(db).process_session(db, sid) == {"segments": 0, "events": 0}


def test_heartbeat_keeps_the_server_view_of_the_candidate(client, db, tmp_path):  # noqa: F811
    headers, sid = _server_session(client, db)
    for seg in _segments(tmp_path / "rec", lambda t: NORMAL, 2.0):
        _upload(client, headers, sid, seg)
    _service(db).process_session(db, sid)
    res = client.post(f"/api/v1/one/client/sessions/{sid}/heartbeat", headers=headers,
                      data={"status": json.dumps({"fps": 25, "eye": "client says", "calibrated": False})})
    assert res.status_code == 200
    body = res.json()
    assert body["analysis"]["mode"] == "SERVER" and body["analysis"]["calibration"] == "READY"
    db.expire_all()
    live = json.loads(db.get(ExamSession, sid).live_status_json)
    assert live["fps"] == 25 and live["eye"] == "TRONG VÙNG" and live["calibrated"] is True


def test_the_hub_checks_the_camera_before_the_exam(client, db):  # noqa: F811
    import cv2

    headers = _paired(client, db)
    dark = np.full((240, 320, 3), 10, dtype=np.uint8)
    ok, jpeg = cv2.imencode(".jpg", dark)
    res = client.post("/api/v1/one/client/check", headers=headers,
                      files={"image": ("check.jpg", jpeg.tobytes(), "image/jpeg")})
    assert res.status_code == 200, res.text
    seen = res.json()
    assert seen["face_count"] == 0 and seen["too_dark"] is True and seen["ok"] is False
    assert client.post("/api/v1/one/client/check", files={"image": ("c.jpg", jpeg.tobytes(), "image/jpeg")}).status_code == 401


def test_clip_is_cut_across_two_segments(tmp_path, monkeypatch):
    segs = _segments(tmp_path / "rec", lambda t: int(t * 20) % 255, 4.0)
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    monkeypatch.setattr(one_analysis.tempfile, "tempdir", str(scratch))
    data = one_analysis.cut_clip([(path, start, dur) for _, path, start, dur in segs], 1000.0, 3200.0)
    assert data is not None and data[4:8] == b"ftyp"
    assert list(scratch.iterdir()) == []  # nothing left behind, and nothing held open (Windows)
    out = tmp_path / "clip.mp4"
    out.write_bytes(data)
    import cv2

    cap = cv2.VideoCapture(str(out))
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    assert 9 <= frames <= 13  # ~2.2 s at 5 fps, taken from both segments


# ---------------------------------------------------------------- the candidate's computer in SERVER mode

def test_candidate_app_keeps_speech_intervals_per_segment():
    import threading
    from types import SimpleNamespace

    from exam_monitor.candidate_app import CandidateApp

    app = SimpleNamespace(_speech_lock=threading.Lock(), _speech=[], _speech_open=None, _started_mono=100.0)
    for t, voice in ((100.5, True), (101.0, True), (101.7, False), (109.0, True)):
        CandidateApp._track_speech(app, voice, t)
    assert app._speech == [[500.0, 1700.0]] and app._speech_open == 9000.0
    # the segment 0-10 s gets the closed stretch and the open one cut at its end
    assert CandidateApp._speech_between(app, 0.0, 10000.0) == [[500.0, 1700.0], [9000.0, 10000.0]]
    assert CandidateApp._speech_between(app, 10000.0, 20000.0) == [[10000.0, 20000.0]]
    assert CandidateApp._server_analysis(SimpleNamespace(policy={"analysis_mode": "server"}, simulate=False))
    assert not CandidateApp._server_analysis(SimpleNamespace(policy={"analysis_mode": "client"}, simulate=False))
    assert not CandidateApp._server_analysis(SimpleNamespace(policy={}, simulate=True))


def test_hub_client_sends_speech_as_json_with_the_segment(tmp_path):
    import sqlite3

    from exam_monitor.hub_client import HubClient

    hub = HubClient(tmp_path / "hub", base_url="http://127.0.0.1:9", token="t")  # never started: nothing is sent
    video = tmp_path / "seg.mp4"
    video.write_bytes(b"....ftypisom")
    hub.add_video_segment("k1", 0, 0.0, 10.0, str(video), speech=[[1.0, 2.25]])
    hub.add_video_segment("k1", 1, 10000.0, 10.0, str(video))
    with sqlite3.connect(tmp_path / "hub" / "one_outbox.db") as conn:
        payloads = [json.loads(row[0]) for row in conn.execute("SELECT payload FROM outbox WHERE kind='video' ORDER BY id")]
    assert json.loads(payloads[0]["speech"]) == [[1.0, 2.2]] or json.loads(payloads[0]["speech"]) == [[1.0, 2.3]]
    assert "speech" not in payloads[1]
