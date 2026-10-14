"""Full-session recording for VIGIL Hub.

Records the whole exam at a fixed frame rate (a frame that comes late is repeated, so one
second of video is always one second of the exam and a moment of the exam can be found in the
video), H.264 when Windows can encode it, in segments of ``segment_seconds``.  Each finished
segment goes to ``on_segment(seq, path, start_ms, duration_s)``; the Hub joins them into one
file for the session.  Encoding runs on its own thread so monitoring never waits for it.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import cv2

logger = logging.getLogger(__name__)

# Windows Media Foundation encodes H.264 (plays in every browser, about 3x smaller than mp4v).  Asked for
# by name: the generic backend tries OpenCV's FFmpeg first, whose openh264 is missing in the wheels.
_CODECS = (((cv2.CAP_MSMF, "avc1"),) if os.name == "nt" else ()) + ((cv2.CAP_ANY, "mp4v"),)


class SessionRecorder:
    def __init__(self, folder: str | Path, on_segment: Callable[[int, str, float, float], None], *,
                 fps: float = 10.0, width: int = 640, segment_seconds: float = 60.0, started: Optional[float] = None):
        self.folder = Path(folder)
        self.on_segment = on_segment
        self.fps = max(1.0, float(fps))
        self.width = max(160, int(width)) // 2 * 2
        self.segment_frames = max(1, int(round(segment_seconds * self.fps)))
        self.codec = ""
        self._t0: Optional[float] = started  # time.monotonic() of the session start (video 0:00)
        self._written = 0            # frames handed to the encoder (the timeline)
        self._queue: "queue.Queue[Optional[tuple]]" = queue.Queue(maxsize=60)
        self._carry = 0              # frames to repeat that did not fit in the queue
        self._last = None            # the last frame handed over (repeated for the carry at the end)
        self._thread = threading.Thread(target=self._encode, name="vigil-record", daemon=True)
        self._thread.start()
        self._closed = False

    @property
    def elapsed_frames(self) -> int:
        return self._written

    def push(self, frame, now: Optional[float] = None) -> None:
        """Offer the current camera frame (BGR).  Cheap between two video frames."""
        if self._closed or frame is None:
            return
        now = time.monotonic() if now is None else now
        if self._t0 is None:
            self._t0 = now
        due = int((now - self._t0) * self.fps) + 1
        count = due - self._written
        if count <= 0:
            return
        h, w = frame.shape[:2]
        height = max(2, int(h * self.width / max(1, w))) // 2 * 2
        small = cv2.resize(frame, (self.width, height), interpolation=cv2.INTER_AREA)
        count += self._carry
        self._last = small
        try:
            self._queue.put_nowait((small, count))
            self._carry = 0
        except queue.Full:
            self._carry = count  # the encoder is behind: repeat the next frame longer instead
        self._written = due

    def close(self, timeout: float = 30.0) -> None:
        """Finish the last segment (the session is ending) and wait for it to be written."""
        if self._closed:
            return
        self._closed = True
        if self._carry and self._last is not None:
            self._queue.put((self._last, self._carry))  # the video must last as long as the session
            self._carry = 0
        self._queue.put(None)
        self._thread.join(timeout)

    # ------------------------------------------------------------ encoder thread
    def _open(self, path: Path, size: tuple[int, int]):
        for api, fourcc in _CODECS:
            writer = cv2.VideoWriter(str(path), api, cv2.VideoWriter_fourcc(*fourcc), self.fps, size)
            if writer.isOpened():
                self.codec = fourcc
                return writer
            writer.release()
        raise OSError("No video encoder available")

    def _encode(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        writer = None
        seq, seg_frames, seg_start = 0, 0, 0
        path: Optional[Path] = None
        size = None

        def finish() -> None:
            nonlocal writer, seq, seg_frames, path
            if writer is None:
                return
            writer.release()
            writer = None
            final = self.folder / f"seg_{seq:05d}.mp4"
            path.replace(final)
            try:
                self.on_segment(seq, str(final), seg_start * 1000.0 / self.fps, seg_frames / self.fps)
            except Exception:
                logger.exception("Could not queue video segment %s", seq)
            seq += 1
            seg_frames = 0

        position = 0  # frames written so far over the whole session
        try:
            while True:
                item = self._queue.get()
                if item is None:
                    break
                frame, count = item
                if size is None:
                    size = (frame.shape[1], frame.shape[0])
                for _ in range(count):
                    if writer is None:
                        seg_start = position
                        path = self.folder / f"seg_{seq:05d}.part.mp4"
                        writer = self._open(path, size)
                    writer.write(frame if (frame.shape[1], frame.shape[0]) == size else cv2.resize(frame, size))
                    seg_frames += 1
                    position += 1
                    if seg_frames >= self.segment_frames:
                        finish()
            finish()
        except Exception:  # never stop monitoring because of the recording
            logger.exception("Full-session recording stopped")
            try:
                finish()
            except Exception:
                pass
