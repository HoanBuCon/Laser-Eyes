"""vigil.py, the one way to start VIGIL; the candidate app's live stream; thresholds in the policy."""

from __future__ import annotations

import json
import sys
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

import vigil
from exam_monitor.live_stream import LiveStreamer, build_message, ws_url
from storage import one_live
from vigil_build import fingerprint
from test_one_hub import client, db  # noqa: F401  (fixtures)


# ---------------------------------------------------------------- vigil.py

def test_default_run_starts_hub_and_candidate_app_on_the_local_hub():
    args = vigil.build_parser().parse_args([])
    assert not (args.hub_only or args.one_only or args.legacy or args.sim)
    cmd = vigil.one_command(args, 8000)
    assert cmd[1].endswith("main.py") and cmd[2:] == ["--candidate", "--hub", "http://127.0.0.1:8000"]


def test_options_choose_the_one_app():
    parse = vigil.build_parser().parse_args
    assert vigil.one_command(parse(["--sim"]), 8000)[2] == "--sim"
    assert vigil.one_command(parse(["--legacy"]), 8000)[2:] == []
    assert vigil.one_command(parse(["--one-only", "--hub", "http://10.0.0.5:8000"]), 8000)[2:] == [
        "--candidate", "--hub", "http://10.0.0.5:8000"]
    assert "--hub" not in vigil.one_command(parse(["--one-only"]), 8000)  # the address saved on that computer
    with pytest.raises(SystemExit):
        parse(["--hub-only", "--one-only"])
    old = parse(["--candidate", "--one-sim"])  # earlier names still work
    assert old.candidate and old.one_sim


def test_a_hub_with_other_code_or_database_is_not_reused():
    build = fingerprint()
    assert vigil.stale_reason({"build": build, "database": "vigil_demo.db"}, build, "vigil_demo.db") is None
    assert "older version" in vigil.stale_reason({"status": "ok"}, build, "vigil_demo.db")
    assert "code changed" in vigil.stale_reason({"build": "0" * 12, "database": "vigil_demo.db"}, build, "vigil_demo.db")
    assert "another database" in vigil.stale_reason({"build": build, "database": "vigil_proctoring.db"}, build, "vigil_demo.db")


def test_fingerprint_follows_the_code(tmp_path):
    (tmp_path / "api").mkdir()
    (tmp_path / "server.py").write_text("x")
    code = tmp_path / "api" / "a.py"
    code.write_text("print(1)")
    first = fingerprint(tmp_path)
    assert fingerprint(tmp_path) == first
    code.write_text("print(22)")
    assert fingerprint(tmp_path) != first


def test_health_reports_build_and_database():
    from fastapi.testclient import TestClient

    from api.main import app

    body = TestClient(app).get("/health").json()
    assert body["build"] == fingerprint() or body["build"]  # computed when the Hub started
    assert body["database"] and "one-live-stream" in body["features"]


def test_candidate_app_does_not_load_the_ai_models():
    import subprocess

    code = ("import sys, exam_monitor.candidate_app; "
            "print(int(any(m in sys.modules for m in ('mediapipe', 'torch', 'exam_monitor.engine'))))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-500:]
    assert out.stdout.strip().endswith("0")


# ---------------------------------------------------------------- candidate live stream

class FakeSocket:
    def __init__(self, replies):
        self.sent = []
        self.replies = list(replies)
        self.closed = False

    def send(self, data):
        self.sent.append(data)

    def recv(self, timeout=None):
        if self.replies and len(self.sent) >= 1:
            return self.replies.pop(0)
        time.sleep(timeout or 0)
        raise TimeoutError

    def close(self):
        self.closed = True


def test_streamer_sends_newest_frames_and_passes_the_hubs_answer_on():
    hub = SimpleNamespace(configured=True, base_url="http://127.0.0.1:8000", token="t",
                          _server_id=lambda key: "srv-1" if key == "k" else None)
    socket = FakeSocket([json.dumps({"analysis": {"calibration": "READY", "faces": 1}, "ended": False})])
    opened, replies = [], []

    def connect(server_id):
        opened.append(server_id)
        return socket

    streamer = LiveStreamer(hub, "k", fps=15, on_reply=replies.append, connect=connect)
    try:
        frame = np.zeros((480, 1280, 3), dtype=np.uint8)
        for i in range(30):
            streamer.push(frame, i * 10.0, voice=bool(i % 2))
            time.sleep(0.01)
        deadline = time.time() + 3
        while time.time() < deadline and (not replies or len(socket.sent) < 2):
            time.sleep(0.02)
    finally:
        streamer.close()
    assert opened == ["srv-1"]
    assert replies and replies[0]["analysis"]["calibration"] == "READY"
    assert 1 <= len(socket.sent) < 30  # a steady rate, older frames skipped
    header, jpeg = one_live.parse_message(socket.sent[0])
    assert set(header) == {"t", "voice"} and jpeg[:2] == b"\xff\xd8"
    import cv2

    assert cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR).shape[1] == 640  # resized


def test_streamer_stops_when_the_hub_says_the_session_ended():
    hub = SimpleNamespace(configured=True, base_url="http://h", token="t", _server_id=lambda key: "srv")
    socket = FakeSocket([json.dumps({"ended": True})])
    streamer = LiveStreamer(hub, "k", fps=10, connect=lambda sid: socket)
    streamer.push(np.zeros((10, 10, 3), dtype=np.uint8), 0.0)
    deadline = time.time() + 3
    while time.time() < deadline and not streamer.ended:
        time.sleep(0.02)
    streamer.close()
    assert streamer.ended and socket.closed


def test_ws_url_and_message_format():
    assert ws_url("http://10.0.0.5:8000/", "s1") == "ws://10.0.0.5:8000/api/v1/one/client/sessions/s1/live"
    assert ws_url("https://hub.example", "s1").startswith("wss://hub.example/")
    assert one_live.parse_message(build_message({"t": 1.0, "voice": None}, b"\xff\xd8x"))[0] == {"t": 1.0, "voice": None}


# ---------------------------------------------------------------- thresholds in the exam policy

def test_thresholds_are_validated(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from storage import online_exam_service as online
    from storage.database import Base
    from storage.db_models import Exam

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    exam = Exam(name="Thi thử")
    db.add(exam)
    db.commit()
    policy = online.update_policy(db, exam, {"thresholds": {"look_away": 2.5, "no_face": 999, "talking": ""}})
    assert policy["thresholds"] == {"look_away": 2.5, "no_face": 60.0}
    with pytest.raises(online.OnlineExamError):
        online.update_policy(db, exam, {"thresholds": {"cheating": 1}})
    with pytest.raises(online.OnlineExamError):
        online.update_policy(db, exam, {"thresholds": [1, 2]})
    db.close()


def test_new_thresholds_reach_a_running_session():
    from storage import one_analysis
    from exam_monitor.models import EventType

    state = one_analysis._SessionState(analyzer=object(), detector=one_analysis._detector({}))
    state.thresholds = json.dumps({}, sort_keys=True)
    one_analysis._apply_thresholds(state, {"thresholds": {"look_away": 4.0}})
    assert state.detector.rules[EventType.LOOK_AWAY].threshold_seconds == 4.0


# ---------------------------------------------------------------- organiser-chosen join code, setup script

def test_setup_script_creates_proctor_exam_join_code_and_candidates(tmp_path):
    import subprocess

    db_file = tmp_path / "hub.db"
    cmd = [sys.executable, "scripts/setup_exam.py", "--database", str(db_file), "--proctor", "giamthi01",
           "--password", "Mat-khau-2026", "--exam", "Kỳ thi cuối học kỳ", "--join-code", "dtc",
           "--candidate", "HS01,Nguyễn Văn A,1111", "--candidate", "HS02"]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "Join code : DTC" in out.stdout and "HS02" in out.stdout

    import sqlite3

    con = sqlite3.connect(db_file)
    assert con.execute("select role from users where username='giamthi01'").fetchone() == ("PROCTOR",)
    assert con.execute("select join_code, join_open from exams where name='Kỳ thi cuối học kỳ'").fetchone() == ("DTC", 1)
    assert con.execute("select count(*) from candidates").fetchone()[0] == 2
    again = subprocess.run(cmd[:10], capture_output=True, text=True, timeout=120)  # account + exam only, again
    assert again.returncode == 0 and "already existed" in again.stdout


def test_join_code_rules(client, db):  # noqa: F811
    from storage import online_exam_service as online
    from storage.db_models import Exam

    first, second = Exam(name="A"), Exam(name="B")
    db.add_all([first, second])
    db.commit()
    assert online.set_join_code(db, first, " dt-c ").join_code == "DTC"
    for bad in ("AB", "QUÁ", "TOOLONGCODE13", ""):
        with pytest.raises(online.OnlineExamError):
            online.set_join_code(db, second, bad)
    with pytest.raises(online.OnlineExamError):
        online.set_join_code(db, second, "dtc")  # taken by the other exam
    online.set_joining(db, first, True)
    online.import_candidates(db, first, [{"candidate_code": "HS01", "pin": "1111"}])
    res = client.post("/api/v1/one/client/join", json={"join_code": "dtc", "candidate_code": "HS01", "pin": "1111",
                                                        "device": {}})
    assert res.status_code == 200, res.text
