"""Classroom LIVE on real cameras and several rooms at once.

- A room's camera from the database is the source (RTSP / device / file).
- LiveFrameReader: frames with their capture time, the oldest dropped when the analysis is
  behind, reconnection when the stream drops.
- One runtime per room, at most VIGIL_MAX_LIVE_ROOMS analysed at once.
"""

from __future__ import annotations

import time
import uuid

import numpy as np
import pytest

from classroom_monitor import camera_source
from classroom_monitor.camera_source import LiveFrameReader, redact
from classroom_monitor.demo import runtime as runtime_module
from classroom_monitor.demo.config import get_room_config, is_live_source, room_key
from classroom_monitor.demo.runtime import DemoRuntime, DemoState
from storage.database import SessionLocal, init_db
from storage.db_models import Camera, ExamRoom, SeatROI


class FakeCap:
    def __init__(self, frames: int, fail_open: bool = False):
        self.left = frames
        self.fail_open = fail_open

    def isOpened(self):  # noqa: N802 - cv2 name
        return not self.fail_open

    def read(self):
        if self.left <= 0:
            return False, None
        self.left -= 1
        time.sleep(0.002)
        return True, np.zeros((4, 6, 3), dtype=np.uint8)

    def get(self, _prop):
        return 0

    def release(self):
        pass


@pytest.fixture(autouse=True)
def fast_reconnect(monkeypatch):
    monkeypatch.setattr(camera_source, "RECONNECT_MIN_S", 0.01)
    monkeypatch.setattr(camera_source, "RECONNECT_MAX_S", 0.02)


@pytest.fixture
def clean_registry():
    saved = (DemoRuntime._instance, dict(DemoRuntime._rooms))
    DemoRuntime._instance, DemoRuntime._rooms = None, {}
    yield
    DemoRuntime._instance, DemoRuntime._rooms = saved[0], saved[1]


def test_sources_and_redaction():
    assert is_live_source("rtsp://cam/stream") and is_live_source("0") and not is_live_source("demo_video/a.mp4")
    assert not is_live_source(None)
    assert redact("rtsp://admin:secret@10.0.0.5:554/h264") == "rtsp://***@10.0.0.5:554/h264"
    assert redact("rtsp://10.0.0.5/h264") == "rtsp://10.0.0.5/h264"
    assert room_key("classroom-02") == "CLASSROOM-02" and room_key("room-101") == "ROOM-101"


def test_live_reader_reconnects_when_the_stream_drops():
    caps = iter([FakeCap(3), FakeCap(0, fail_open=True), FakeCap(50)])
    reader = LiveFrameReader("rtsp://cam", opener=lambda _src: next(caps, FakeCap(0, fail_open=True)), max_queue=64)
    try:
        assert reader.wait_first_frame(2.0)
        got = []
        deadline = time.monotonic() + 3.0
        while len(got) < 20 and time.monotonic() < deadline:
            ok, frame, at = reader.read(timeout=0.2)
            if ok:
                got.append(at)
        assert len(got) == 20  # frames from the first stream and from the one after reconnecting
        assert reader.reconnects >= 2 and all(b >= a for a, b in zip(got, got[1:]))
    finally:
        reader.stop()
    assert reader.status == "STOPPED"


def test_live_reader_drops_the_oldest_frames_when_behind():
    reader = LiveFrameReader("0", opener=lambda _src: FakeCap(40), max_queue=4)
    try:
        assert reader.wait_first_frame(2.0)
        time.sleep(0.4)  # the analysis does not read: the queue keeps only the newest frames
        assert reader.dropped > 0 and reader._queue.qsize() <= 4
    finally:
        reader.stop()


def test_a_room_camera_from_the_database_is_the_source():
    init_db()
    db = SessionLocal()
    code = f"ROOM-CAM-{uuid.uuid4().hex[:6].upper()}"
    try:
        room = ExamRoom(room_code=code, name="Phòng camera thật")
        db.add(room)
        db.flush()
        idle = Camera(room_id=room.id, name="Cam phụ", source_uri="1")
        main = Camera(room_id=room.id, name="Cam chính", source_uri="rtsp://admin:pw@10.1.1.9/stream")
        db.add_all([idle, main])
        db.flush()
        db.add(SeatROI(room_id=room.id, camera_id=main.id, seat_code="S01", polygon_json="[[0,0],[10,0],[10,10]]"))
        db.commit()
        config = get_room_config(code)
        assert config.is_live and config.source == "rtsp://admin:pw@10.1.1.9/stream"  # the camera with the seats
        assert config.room_code == code and config.camera_id == "Cam chính"
        with pytest.raises(LookupError):
            get_room_config("NO-SUCH-ROOM")
    finally:
        db.close()


def test_one_runtime_per_room_and_a_limit_on_rooms_at_once(clean_registry, monkeypatch):
    first = DemoRuntime.get_instance("classroom-01")
    assert DemoRuntime.get_instance("CLASSROOM-01") is first and DemoRuntime.get_instance() is first
    second = DemoRuntime.get_instance("classroom-02")
    assert second is not first and sorted(DemoRuntime.all_rooms()) == ["CLASSROOM-01", "CLASSROOM-02"]

    monkeypatch.setenv("VIGIL_MAX_LIVE_ROOMS", "1")
    first.status.state = DemoState.RUNNING.value  # room 01 is being analysed
    with pytest.raises(RuntimeError, match="at most 1"):
        second.start("classroom-02")
    assert DemoRuntime.active_rooms() == ["CLASSROOM-01"]
    first.status.state = DemoState.IDLE.value


def test_callbacks_registered_once_reach_every_room(clean_registry):
    seen = []

    def callback(status):
        seen.append(status)

    DemoRuntime.register_global_callback("status", callback)
    try:
        for room in ("classroom-01", "classroom-03"):
            assert callback in DemoRuntime.get_instance(room)._callback_snapshot("status")
    finally:
        DemoRuntime.unregister_global_callback("status", callback)
    assert callback not in DemoRuntime.get_instance("classroom-01")._callback_snapshot("status")
    assert runtime_module.max_live_rooms() >= 1
