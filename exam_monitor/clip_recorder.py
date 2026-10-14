"""Short video clips around each signal, for VIGIL Hub.

The recorder keeps the last few seconds of camera frames (downscaled, a few frames per
second) in memory.  When a signal fires it waits for the seconds after it, writes one
MP4 (seconds before + after) on a background thread and hands the path to ``on_ready``.
The Hub converts the clip to H.264 for browser playback; the archived file and its
SHA-256 stay as sent.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import cv2

logger = logging.getLogger(__name__)


@dataclass
class _Request:
    event_id: str
    path: Path
    start: float
    deadline: float


class ClipRecorder:
    def __init__(self, on_ready: Callable[[str, str], None], *, before: float = 5.0, after: float = 4.0,
                 fps: float = 8.0, width: int = 480):
        self.on_ready = on_ready
        self.before = max(0.0, float(before))
        self.after = max(0.0, float(after))
        self.fps = max(1.0, float(fps))
        self.width = max(160, int(width))
        self._frames: deque[tuple[float, object]] = deque()
        self._pending: list[_Request] = []
        self._last = 0.0
        self._lock = threading.Lock()
        self._writers: list[threading.Thread] = []

    def push(self, frame, now: Optional[float] = None) -> None:
        """Offer a camera frame (BGR).  Cheap when it is not time for the next clip frame."""
        now = time.monotonic() if now is None else now
        if frame is not None and now - self._last >= 1.0 / self.fps:
            self._last = now
            h, w = frame.shape[:2]
            small = cv2.resize(frame, (self.width, max(2, int(h * self.width / max(1, w))) // 2 * 2),
                               interpolation=cv2.INTER_AREA) if w > self.width else frame.copy()
            with self._lock:
                self._frames.append((now, small))
                keep = self.before + self.after + 2.0
                while self._frames and now - self._frames[0][0] > keep:
                    self._frames.popleft()
        self._flush(now)

    def request(self, event_id: str, path: str | Path, now: Optional[float] = None) -> None:
        """A signal fired now: record a clip from ``before`` seconds ago to ``after`` seconds ahead."""
        now = time.monotonic() if now is None else now
        with self._lock:
            self._pending.append(_Request(event_id, Path(path), now - self.before, now + self.after))

    def close(self, wait: float = 10.0) -> None:
        """Write what is pending with the frames there are (the session is ending)."""
        self._flush(float("inf"))
        deadline = time.monotonic() + wait
        for thread in list(self._writers):
            thread.join(max(0.0, deadline - time.monotonic()))

    def _flush(self, now: float) -> None:
        with self._lock:
            due = [r for r in self._pending if r.deadline <= now]
            if not due:
                return
            self._pending = [r for r in self._pending if r.deadline > now]
            jobs = [(r, [f for t, f in self._frames if r.start <= t <= r.deadline]) for r in due]
        for request, frames in jobs:
            if len(frames) < 2:
                continue
            thread = threading.Thread(target=self._write, args=(request, frames), daemon=True, name="vigil-clip")
            self._writers = [t for t in self._writers if t.is_alive()] + [thread]
            thread.start()

    def _write(self, request: _Request, frames: list) -> None:
        h, w = frames[0].shape[:2]
        request.path.parent.mkdir(parents=True, exist_ok=True)
        temp = request.path.with_suffix(".part.mp4")
        try:
            writer = cv2.VideoWriter(str(temp), cv2.VideoWriter_fourcc(*"mp4v"), self.fps, (w, h))
            if not writer.isOpened():
                raise OSError("VideoWriter could not open")
            for frame in frames:
                writer.write(frame if frame.shape[:2] == (h, w) else cv2.resize(frame, (w, h)))
            writer.release()
            temp.replace(request.path)
        except Exception:  # a missing clip must never stop monitoring; the snapshot is still sent
            logger.exception("Could not write the clip for %s", request.event_id)
            temp.unlink(missing_ok=True)
            return
        self.on_ready(request.event_id, str(request.path))
