"""Video decoding and writing beside the analysis loop.

The analysis loop used to decode a frame, run the models, draw, and encode ``result.mp4``
one after the other, so the GPU waited while the CPU decoded or wrote and the other way
round.  ``FrameReader`` decodes the next frames on its own thread and ``AsyncVideoWriter``
encodes the annotated video on its own thread; the loop only hands frames over.

Neither drops frames: a full queue makes the producer wait, so the result video and the
analysis see exactly the frames they saw before, only sooner.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from typing import Any, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_END = object()


class FrameReader:
    """``cap.read()`` on a background thread, a few frames ahead (same frames, same order)."""

    def __init__(self, cap: Any, max_queue: int = 8):
        self.cap = cap
        self._queue: "queue.Queue[Any]" = queue.Queue(maxsize=max(1, int(max_queue)))
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="vigil-frame-reader", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                ok, frame = self.cap.read()
                if not ok or frame is None:
                    break
                while not self._stop.is_set():
                    try:
                        self._queue.put(frame, timeout=0.2)
                        break
                    except queue.Full:
                        continue
        except Exception:  # a broken stream ends the video like its last frame
            logger.exception("Video decoding failed")
        finally:
            self._put_end()

    def _put_end(self) -> None:
        while True:
            try:
                self._queue.put(_END, timeout=0.2)
                return
            except queue.Full:
                if self._stop.is_set():
                    try:
                        self._queue.get_nowait()
                    except queue.Empty:
                        pass

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        item = self._queue.get()
        if item is _END:
            self._queue.put(_END)  # later reads keep answering "no more frames"
            return False, None
        return True, item

    def stop(self) -> None:
        self._stop.set()
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass
        self._thread.join(timeout=2.0)


class AsyncVideoWriter:
    """``cv2.VideoWriter`` fed from a queue on its own thread; ``release()`` waits for the last frame."""

    def __init__(self, writer: Any, max_queue: int = 64):
        self.writer = writer
        self.frames_written = 0
        self.error: Optional[str] = None
        self._queue: "queue.Queue[Any]" = queue.Queue(maxsize=max(1, int(max_queue)))
        self._thread = threading.Thread(target=self._run, name="vigil-video-writer", daemon=True)
        self._thread.start()

    def isOpened(self) -> bool:  # noqa: N802 - same name as cv2.VideoWriter
        return bool(self.writer.isOpened())

    def _run(self) -> None:
        while True:
            frame = self._queue.get()
            if frame is _END:
                return
            if self.error is not None:
                continue
            try:
                self.writer.write(frame)
                self.frames_written += 1
            except Exception as exc:  # keep the analysis going; the video is reported incomplete
                self.error = f"{exc.__class__.__name__}: {exc}"
                logger.error("Writing the result video failed: %s", self.error)

    def write(self, frame: np.ndarray) -> None:
        """Queue a frame (the caller must not draw on it afterwards)."""
        self._queue.put(frame)

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    def release(self, timeout: float = 120.0) -> None:
        self._queue.put(_END)
        self._thread.join(timeout=timeout)
        self.writer.release()


class StageTimer:
    """Rolling average time per stage of the loop (ms), for the live status and the profile."""

    def __init__(self, window: int = 30):
        self._window = window
        self._samples: dict[str, deque] = {}

    def put(self, stage: str, ms: float) -> None:
        samples = self._samples.get(stage)
        if samples is None:
            samples = self._samples[stage] = deque(maxlen=self._window)
        samples.append(float(ms))

    def add(self, stage: str, started: float) -> None:
        """Record the time since ``started`` (a ``time.perf_counter()`` value)."""
        self.put(stage, (time.perf_counter() - started) * 1000.0)

    def averages(self) -> dict[str, float]:
        return {stage: round(sum(v) / len(v), 2) for stage, v in self._samples.items() if v}


class RealtimePacer:
    """Which frames a LIVE run analyses: as many as the machine can, never falling behind the video.

    No fixed rate: every frame is analysed while the machine keeps up.  When the analysis of a
    frame takes longer than the gap to the next one, the frames whose moment has already passed
    are skipped (they still reach the evidence clips and the result video), so the rate settles
    at what this machine can do and the picture stays live.  At most ``max_skip`` frames in a
    row are skipped (one second of video), so even a very slow machine analyses every second.
    """

    def __init__(self, fps: float, clock: Any = time.perf_counter, max_skip: Optional[int] = None, window: int = 30):
        self.fps = float(fps) if fps and fps > 0 else 30.0
        self._clock = clock
        self.max_skip = max_skip if max_skip is not None else max(1, int(round(self.fps)))
        self._origin: Optional[float] = None  # clock time at which the video's t=0 "played"
        self._paused_at: Optional[float] = None
        self._run = 0
        self.analysed = 0
        self.skipped = 0
        self._recent: deque = deque(maxlen=window)  # (clock, video seconds) of analysed frames

    def should_analyse(self, timestamp_ms: float) -> bool:
        if self._origin is None:  # first frame: the model warm-up is not "falling behind"
            return True
        behind = (self._clock() - self._origin) - timestamp_ms / 1000.0
        if behind > 1.0 / self.fps and self._run < self.max_skip:
            self._run += 1
            self.skipped += 1
            return False
        self._run = 0
        return True

    def analysed_frame(self, timestamp_ms: float) -> None:
        """Call once the frame has been analysed."""
        now = self._clock()
        self.analysed += 1
        if self._origin is None:
            self._origin = now - timestamp_ms / 1000.0
        self._recent.append((now, timestamp_ms / 1000.0))

    def pause(self) -> None:
        if self._paused_at is None:
            self._paused_at = self._clock()

    def resume(self) -> None:
        if self._paused_at is not None:
            if self._origin is not None:
                self._origin += self._clock() - self._paused_at
            self._recent.clear()
            self._paused_at = None

    def realtime_factor(self) -> float:
        """Seconds of video analysed per second of wall time (1.0 = keeping up with the camera)."""
        if len(self._recent) < 2:
            return 0.0
        (c0, v0), (c1, v1) = self._recent[0], self._recent[-1]
        return (v1 - v0) / max(1e-6, c1 - c0)
