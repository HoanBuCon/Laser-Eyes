"""Classroom LIVE speed-ups must not change what is analysed or recorded.

Measured on an RTX 4060 laptop (runtime_profile.json): head pose 19.4 ms/frame, mostly PIL
preprocessing on the CPU; YOLO pose 23.7 ms in float32; result.mp4 written in the loop 9.8 ms.
These tests check the faster paths give the same inputs, frames and video."""

from __future__ import annotations

import threading
import time

import cv2
import numpy as np
import pytest

from classroom_monitor.head_pose_provider import sixdrepnet_input
from classroom_monitor.video_io import AsyncVideoWriter, FrameReader, StageTimer


def _face_like(rng, h, w):
    base = rng.integers(0, 255, (max(2, h // 8), max(2, w // 8), 3), dtype=np.uint8)
    return cv2.GaussianBlur(cv2.resize(base, (w, h), interpolation=cv2.INTER_CUBIC), (5, 5), 0)


def test_head_pose_input_matches_sixdrepnets_own_transform():
    torch = pytest.importorskip("torch")
    transforms = pytest.importorskip("torchvision.transforms")
    from PIL import Image

    reference = transforms.Compose([transforms.Resize(224), transforms.CenterCrop(224), transforms.ToTensor(),
                                    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])])
    rng = np.random.default_rng(7)
    crops = [_face_like(rng, h, w) for h, w in [(40, 36), (64, 64), (96, 80), (150, 120), (300, 260), (30, 90)]]
    mine = sixdrepnet_input(crops)
    theirs = torch.stack([reference(Image.fromarray(cv2.cvtColor(c, cv2.COLOR_BGR2RGB))) for c in crops]).numpy()
    assert mine.shape == theirs.shape == (6, 3, 224, 224) and mine.dtype == np.float32
    diff = np.abs(mine - theirs)
    assert diff.mean() < 0.02  # interpolation rounding only (≈ 1/3 grey level)
    assert np.percentile(diff, 99) < 0.1


class _FakeCapture:
    def __init__(self, count):
        self.frames = [np.full((4, 4, 3), i, dtype=np.uint8) for i in range(count)]

    def read(self):
        time.sleep(0.001)
        if not self.frames:
            return False, None
        return True, self.frames.pop(0)


def test_frame_reader_gives_every_frame_in_order_then_stops():
    reader = FrameReader(_FakeCapture(50), max_queue=4)
    seen = []
    while True:
        ok, frame = reader.read()
        if not ok:
            break
        seen.append(int(frame[0, 0, 0]))
    assert seen == list(range(50))
    assert reader.read() == (False, None)  # still the end
    reader.stop()


def test_frame_reader_can_be_stopped_midway():
    reader = FrameReader(_FakeCapture(1000), max_queue=2)
    reader.read()
    reader.stop()
    assert not reader._thread.is_alive()


class _FakeWriter:
    def __init__(self):
        self.frames = []
        self.released = False

    def write(self, frame):
        time.sleep(0.002)  # slower than the producer: the queue fills and the producer waits
        self.frames.append(int(frame[0, 0, 0]))

    def isOpened(self):  # noqa: N802
        return True

    def release(self):
        self.released = True


def test_async_writer_writes_every_frame_in_order_without_dropping():
    fake = _FakeWriter()
    writer = AsyncVideoWriter(fake, max_queue=4)
    for i in range(40):
        writer.write(np.full((2, 2, 3), i, dtype=np.uint8))
    writer.release()
    assert fake.frames == list(range(40)) and fake.released and writer.frames_written == 40


def test_async_writer_writes_a_real_mp4(tmp_path):
    path = tmp_path / "result.mp4"
    writer = AsyncVideoWriter(cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (64, 48)))
    for i in range(20):
        writer.write(np.full((48, 64, 3), i * 10, dtype=np.uint8))
    writer.release()
    cap = cv2.VideoCapture(str(path))
    assert int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) == 20
    cap.release()


def test_stage_timer_averages_recent_samples():
    timer = StageTimer(window=3)
    for ms in (10, 20, 30, 40):
        timer.put("render", ms)
    started = time.perf_counter()
    timer.add("decode_wait", started)
    averages = timer.averages()
    assert averages["render"] == 30.0 and averages["decode_wait"] >= 0


def test_half_precision_only_on_a_gpu_and_can_be_turned_off(monkeypatch):
    from classroom_monitor import detector

    monkeypatch.setenv("VIGIL_YOLO_FP16", "0")
    assert detector._cuda_half("VIGIL_YOLO_FP16") is False
    monkeypatch.setenv("VIGIL_YOLO_FP16", "1")
    torch = pytest.importorskip("torch")
    assert detector._cuda_half("VIGIL_YOLO_FP16") is bool(torch.cuda.is_available())


def test_live_status_carries_stage_times():
    from classroom_monitor.demo.runtime import DemoStatus

    status = DemoStatus(stage_ms={"perception": 12.5})
    assert status.to_dict()["stage_ms"] == {"perception": 12.5}
