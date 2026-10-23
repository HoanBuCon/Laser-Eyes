"""Opening a room's camera and reading it live.

A video file is read frame by frame and ends; a camera (RTSP/HTTP stream or device index)
has no end and runs in real time. ``LiveFrameReader`` reads a camera on its own thread:

* every frame carries the moment it was captured (``time.monotonic``), because a stream's
  advertised frame rate is often wrong and frames arrive unevenly;
* when the analysis falls behind, the oldest waiting frames are dropped: a camera cannot be
  paused, and the picture must stay current;
* when the stream drops, it reconnects with a growing pause (1 s .. 10 s) until it is back
  or the run is stopped. ``status`` says what the reader is doing for the web page.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Callable, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

RECONNECT_MIN_S = 1.0
RECONNECT_MAX_S = 10.0


def open_capture(source: str) -> Any:
    """``cv2.VideoCapture`` for a file path, a stream URL or a device index ("0")."""
    import cv2

    text = str(source).strip()
    if text.isdigit():
        cap = cv2.VideoCapture(int(text), cv2.CAP_DSHOW) if _windows() else cv2.VideoCapture(int(text))
        if not cap.isOpened():
            cap.release()
            cap = cv2.VideoCapture(int(text))
        return cap
    if "://" in text:
        cap = cv2.VideoCapture(text, cv2.CAP_FFMPEG)
        try:  # a little buffering only: the newest frame matters, not every queued one
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)
        except Exception:
            pass
        return cap
    return cv2.VideoCapture(text)


def _windows() -> bool:
    import os

    return os.name == "nt"


def redact(source: str) -> str:
    """A stream address without its password, for logs and the web page."""
    text = str(source)
    if "://" in text and "@" in text.split("://", 1)[1]:
        scheme, rest = text.split("://", 1)
        return f"{scheme}://***@{rest.split('@', 1)[1]}"
    return text


class LiveFrameReader:
    """Reads a camera on a background thread; ``read()`` gives (ok, frame, monotonic capture time)."""

    def __init__(self, source: str, opener: Callable[[str], Any] = open_capture, max_queue: int = 4,
                 clock: Callable[[], float] = time.monotonic):
        self.source = source
        self._opener = opener
        self._clock = clock
        self._queue: "queue.Queue[Tuple[np.ndarray, float]]" = queue.Queue(maxsize=max(1, int(max_queue)))
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.status = "CONNECTING"
        self.reconnects = 0
        self.dropped = 0
        self.width = 0
        self.height = 0
        self.fps = 0.0
        self._first = threading.Event()   # set once the first frame (or a final failure) is known
        self._thread = threading.Thread(target=self._run, name="vigil-camera-reader", daemon=True)
        self._thread.start()

    # ---------------------------------------------------------------- reader thread
    def _set_status(self, value: str) -> None:
        with self._lock:
            self.status = value

    def _run(self) -> None:
        pause = RECONNECT_MIN_S
        while not self._stop.is_set():
            cap = None
            try:
                cap = self._opener(self.source)
                if cap is None or not cap.isOpened():
                    raise ConnectionError("camera did not open")
                self._remember_format(cap)
                got_frame = False
                while not self._stop.is_set():
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        raise ConnectionError("camera stopped sending frames")
                    if not got_frame:
                        got_frame = True
                        pause = RECONNECT_MIN_S
                        self._set_status("LIVE")
                        if not self.width:
                            self.height, self.width = frame.shape[:2]
                        self._first.set()
                    self._offer(frame, self._clock())
            except Exception as exc:  # any failure: wait, then reconnect
                if self._stop.is_set():
                    break
                self.reconnects += 1
                self._set_status("RECONNECTING")
                logger.warning("Camera %s: %s; reconnecting in %.0f s", redact(self.source), exc, pause)
                self._stop.wait(pause)
                pause = min(RECONNECT_MAX_S, pause * 2)
            finally:
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass
        self._set_status("STOPPED")
        self._first.set()

    def _remember_format(self, cap: Any) -> None:
        import cv2

        try:
            self.width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            self.height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
            self.fps = fps if 1.0 <= fps <= 120.0 else self.fps
        except Exception:
            pass

    def _offer(self, frame: np.ndarray, captured: float) -> None:
        while True:
            try:
                self._queue.put_nowait((frame, captured))
                return
            except queue.Full:
                try:
                    self._queue.get_nowait()  # drop the oldest: the picture stays current
                    self.dropped += 1
                except queue.Empty:
                    pass

    # ---------------------------------------------------------------- caller side
    def wait_first_frame(self, timeout: float) -> bool:
        """True once a frame arrived (False after ``timeout`` or when stopped first)."""
        self._first.wait(timeout)
        with self._lock:
            return self.status == "LIVE" or not self._queue.empty()

    def read(self, timeout: float = 0.5) -> Tuple[bool, Optional[np.ndarray], float]:
        """The next frame; (False, None, 0) when none came within ``timeout`` (the caller retries)."""
        try:
            frame, captured = self._queue.get(timeout=timeout)
            return True, frame, captured
        except queue.Empty:
            return False, None, 0.0

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3.0)
