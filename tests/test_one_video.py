"""VIGIL One full-session video: recorded in segments during the exam, joined into one file on the Hub."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from exam_monitor.hub_client import SYNCED
from exam_monitor.session_recorder import SessionRecorder
from storage import one_video
from storage.db_models import ExamSession, SessionVideoSegment
from test_one_hub import _open, _paired, client, db  # noqa: F401  (fixtures)
from test_one_hub_client import Flaky, _drain, _event, _pair

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@pytest.fixture(autouse=True)
def _one_evidence_root(tmp_path, monkeypatch):
    monkeypatch.setenv("VIGIL_ONE_EVIDENCE_ROOT", str(tmp_path / "one_evidence"))


def _record(folder: Path, seconds: float, *, fps=5, segment_seconds=2.0, skip=None) -> list[tuple]:
    """Feed a fake camera for ``seconds`` (simulated clock); returns the finished segments."""
    done = []
    recorder = SessionRecorder(folder, lambda *seg: done.append(seg), fps=fps, width=160,
                               segment_seconds=segment_seconds, started=0.0)
    frame = np.zeros((120, 160, 3), dtype=np.uint8)
    t = 0.0
    while t < seconds:
        if not (skip and skip[0] <= t < skip[1]):  # the camera froze: no frames, the clock goes on
            frame[:] = int(t * 40) % 255
            recorder.push(frame, now=t)
        t += 1 / 30
    recorder.close()
    return done


def _duration(path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip())


# ---------------------------------------------------------------- the recorder on the candidate's computer

def test_recorder_keeps_real_time_in_fixed_segments(tmp_path):
    done = _record(tmp_path / "rec", 5.0, skip=(1.0, 2.0))
    assert [seq for seq, *_ in done] == [0, 1, 2]
    starts = [start for _, _, start, _ in done]
    lengths = [length for *_, length in done]
    assert starts == [0.0, 2000.0, 4000.0]
    assert lengths[:2] == [2.0, 2.0] and 0.8 <= lengths[2] <= 1.2  # a frozen second is still a second of video
    for _, path, _, _ in done:
        assert Path(path).is_file() and Path(path).read_bytes()[4:8] == b"ftyp"


# ---------------------------------------------------------------- the Hub

def _upload(client, headers, sid, seq, start_ms, duration_s, data, sha=None):  # noqa: F811
    return client.post(f"/api/v1/one/client/sessions/{sid}/video", headers=headers,
                       files={"video": (f"s{seq}.mp4", data, "video/mp4")},
                       data={"seq": seq, "start_ms": start_ms, "duration_s": duration_s,
                             "sha256": sha if sha is not None else hashlib.sha256(data).hexdigest()})


def test_segments_are_checked_and_idempotent(client, db, tmp_path):  # noqa: F811
    headers = _paired(client, db)
    sid = _open(client, headers)["session"]["session_id"]
    segs = _record(tmp_path / "rec", 4.0)
    data = Path(segs[0][1]).read_bytes()
    assert _upload(client, headers, sid, 0, 0, 2.0, data, sha="0" * 64).status_code == 422
    assert _upload(client, headers, sid, 0, 0, 2.0, b"not a video").status_code == 422
    first = _upload(client, headers, sid, 0, 0, 2.0, data)
    assert first.status_code == 200 and first.json()["created"] is True
    again = _upload(client, headers, sid, 0, 0, 2.0, data)
    assert again.status_code == 200 and again.json()["created"] is False
    other = Path(segs[1][1]).read_bytes()
    assert _upload(client, headers, sid, 0, 0, 2.0, other).status_code == 422  # same number, other content
    info = client.get(f"/api/v1/one/sessions/{sid}/video").json()
    assert info["status"] == "RECORDING" and info["segment_count"] == 1 and info["url"] is None
    assert client.get(info["segments"][0]["url"]).content == data


def test_timeline_maps_exam_moments_and_reports_gaps():
    class Seg:
        def __init__(self, seq, start_ms, duration_s):
            self.seq, self.start_ms, self.duration_s, self.sha256, self.file_size_bytes = seq, start_ms, duration_s, "x", 1

    # minute 2 never arrived (app closed), and the exam ran 5 minutes but video stops at 4
    rows, gaps = one_video.timeline([Seg(0, 0, 60), Seg(1, 60_000, 60), Seg(3, 180_000, 60), Seg(4, 240_000, 60)],
                                    session_ms=420_000)
    assert gaps == [[120_000, 180_000], [300_000, 420_000]]
    assert [r["offset_s"] for r in rows] == [0, 60, 120, 180]
    assert one_video.locate(rows, 90_000) == 90          # inside minute 1
    assert one_video.locate(rows, 150_000) == 120        # in the gap: the next recorded moment
    assert one_video.locate(rows, 200_000) == 140        # minute 3 sits at 2:00 of the joined file
    assert one_video.locate(rows, 999_000) is None


@pytest.mark.skipif(not HAS_FFMPEG, reason="FFmpeg joins the segments")
def test_end_of_session_joins_one_video(client, db, tmp_path):  # noqa: F811
    headers = _paired(client, db)
    sid = _open(client, headers)["session"]["session_id"]
    segs = _record(tmp_path / "rec", 6.0)
    for seq, path, start, length in segs:
        assert _upload(client, headers, sid, seq, start, length, Path(path).read_bytes()).status_code == 200
    stop = client.post(f"/api/v1/one/client/sessions/{sid}/stop", headers=headers, json={"reason": "SUBMITTED"})
    assert stop.status_code == 200
    info = client.get(f"/api/v1/one/sessions/{sid}/video").json()  # joined right after the stop response
    assert info["status"] in ("READY", "PARTIAL") and info["url"]
    video = client.get(info["url"])
    assert video.status_code == 200 and video.headers["content-type"] == "video/mp4"
    assert hashlib.sha256(video.content).hexdigest() == info["sha256"]
    joined = tmp_path / "joined.mp4"
    joined.write_bytes(video.content)
    assert abs(_duration(joined) - sum(s[3] for s in segs)) < 0.6
    ranged = client.get(info["url"], headers={"Range": "bytes=0-99"})  # the browser seeks with ranges
    assert ranged.status_code == 206 and len(ranged.content) == 100
    db.expire_all()
    assert db.query(SessionVideoSegment).filter_by(session_id=sid).count() == len(segs)  # segments are kept


@pytest.mark.skipif(not HAS_FFMPEG, reason="FFmpeg joins the segments")
def test_late_segment_after_the_end_is_joined_again(client, db, tmp_path):  # noqa: F811
    headers = _paired(client, db)
    sid = _open(client, headers)["session"]["session_id"]
    segs = _record(tmp_path / "rec", 4.0)
    seq, path, start, length = segs[0]
    _upload(client, headers, sid, seq, start, length, Path(path).read_bytes())
    client.post(f"/api/v1/one/client/sessions/{sid}/stop", headers=headers, json={"reason": "SUBMITTED"})
    first = client.get(f"/api/v1/one/sessions/{sid}/video").json()
    seq, path, start, length = segs[1]
    _upload(client, headers, sid, seq, start, length, Path(path).read_bytes())
    second = client.get(f"/api/v1/one/sessions/{sid}/video").json()
    assert second["segment_count"] == 2 and second["sha256"] != first["sha256"]
    assert second["duration_s"] == pytest.approx(segs[0][3] + segs[1][3], abs=0.01)


def test_web_join_needs_a_chief(client, db):  # noqa: F811
    headers = _paired(client, db)
    sid = _open(client, headers)["session"]["session_id"]
    assert client.post(f"/api/v1/one/sessions/{sid}/video/merge").status_code in (401, 403)


# ---------------------------------------------------------------- the desktop's send queue

def test_video_goes_after_signals_and_before_the_stop(client, db, tmp_path):  # noqa: F811
    http = Flaky(client)
    hub = _pair(client, db, tmp_path, http)
    key = "20261014-120000-VID1"
    segs = _record(tmp_path / "rec", 2.0)
    hub.open_session(key, {"candidate_ref": "SV-09", "exam_name": "Thi thử"})
    hub.add_video_segment(key, *segs[0][:1], segs[0][2], segs[0][3], segs[0][1])
    hub.add_event(key, _event("ev-after-video"))
    hub.stop_session(key, {"frame_count": 10, "average_fps": 10.0})
    sent = []
    original = http.post

    def spy(url, *a, **k):
        sent.append(url.rsplit("/", 1)[-1])
        return original(url, *a, **k)

    http.post = spy
    _drain(hub)
    assert hub.status().state == SYNCED
    assert sent == ["sessions", "events", "video", "stop"]
    db.expire_all()
    session = db.query(ExamSession).filter_by(client_session_id=key).one()
    assert db.query(SessionVideoSegment).filter_by(session_id=session.id).count() == 1


def test_recorder_that_falls_behind_still_covers_the_whole_session(tmp_path):
    done = []
    recorder = SessionRecorder(tmp_path / "rec", lambda *seg: done.append(seg), fps=10, width=640,
                               segment_seconds=4, started=0.0)
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    for i in range(300):  # 10 s of camera pushed far faster than real time: the encoder queue overflows
        recorder.push(frame, now=i / 30)
    recorder.close()
    assert sum(length for *_, length in done) == pytest.approx(10.0)
