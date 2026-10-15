"""Live camera stream from VIGIL One to VIGIL Hub (WebSocket).

The candidate's computer does no analysis: it sends small JPEG frames (``fps`` per second,
``width`` px) to the Hub, which analyses them as they arrive and shows them to proctors.
Each message is ``4-byte header length + JSON {t, voice} + JPEG``; ``t`` is the session
clock in ms (the same clock as the recorded segments), ``voice`` whether the microphone
hears speech (None without a microphone).  About once a second the Hub answers with what
it sees (calibration, faces), which the app turns into hints for the candidate.

The full-quality recording still goes through the outbox as H.264 segments; when the
stream is down (network, Hub restart) the Hub analyses those instead, so nothing is lost.
"""

from __future__ import annotations

import json
import logging
import struct
import threading
import time
from typing import Any, Callable, Optional

import cv2

logger = logging.getLogger(__name__)

RETRY_SECONDS = (1, 2, 5, 10)
REFUSED_WAIT_SECONDS = 30


def build_message(header: dict, jpeg: bytes) -> bytes:
    raw = json.dumps(header, separators=(",", ":")).encode("utf-8")
    return struct.pack(">I", len(raw)) + raw + jpeg


def ws_url(base_url: str, server_session_id: str) -> str:
    base = base_url.rstrip("/")
    base = "wss://" + base[8:] if base.startswith("https://") else "ws://" + base.split("://", 1)[-1]
    return f"{base}/api/v1/one/client/sessions/{server_session_id}/live"


class LiveStreamer:
    """Sends the newest camera frame to the Hub at a steady rate; older frames are skipped."""

    def __init__(self, hub, session_key: str, *, fps: float = 5.0, width: int = 640, quality: int = 70,
                 on_reply: Optional[Callable[[dict], None]] = None, connect=None):
        self.hub = hub
        self.session_key = session_key
        self.interval = 1.0 / max(1.0, min(15.0, float(fps)))
        self.width = int(width)
        self.quality = int(quality)
        self.on_reply = on_reply
        self._connect = connect  # tests inject a fake socket factory
        self._lock = threading.Lock()
        self._latest: Optional[tuple[Any, float, Optional[bool]]] = None
        self._stop = threading.Event()
        self.connected = False
        self.ended = False
        self.sent = 0
        self.last_error = ""
        self._thread = threading.Thread(target=self._run, name="vigil-one-live", daemon=True)
        self._thread.start()

    def push(self, frame, t_ms: float, voice: Optional[bool] = None) -> None:
        with self._lock:
            self._latest = (frame, float(t_ms), voice)

    def close(self, timeout: float = 2.0) -> None:
        self._stop.set()
        self._thread.join(timeout=timeout)

    # ------------------------------------------------------------ worker
    def _take(self):
        with self._lock:
            item, self._latest = self._latest, None
        return item

    def _encode(self, frame) -> Optional[bytes]:
        h, w = frame.shape[:2]
        if w > self.width:
            frame = cv2.resize(frame, (self.width, max(1, int(h * self.width / w))), interpolation=cv2.INTER_AREA)
        ok, enc = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), self.quality])
        return enc.tobytes() if ok else None

    def _open(self, server_id: str):
        if self._connect is not None:
            return self._connect(server_id)
        from websockets.sync.client import connect

        return connect(ws_url(self.hub.base_url, server_id), additional_headers={"Authorization": f"Bearer {self.hub.token}"},
                       open_timeout=5, close_timeout=2, max_size=1 << 20)

    def _run(self) -> None:
        attempt = 0
        while not self._stop.is_set() and not self.ended:
            server_id = self.hub._server_id(self.session_key) if self.hub.configured else None
            if server_id is None:  # the session is opened through the outbox first
                self._stop.wait(0.5)
                continue
            try:
                socket = self._open(server_id)
            except Exception as exc:  # Hub down, network, refused
                self.last_error = f"{exc.__class__.__name__}: {exc}"
                refused = "1008" in str(exc) or "403" in str(exc)
                self._stop.wait(REFUSED_WAIT_SECONDS if refused else RETRY_SECONDS[min(attempt, len(RETRY_SECONDS) - 1)])
                attempt += 1
                continue
            attempt = 0
            self.connected = True
            try:
                self._pump(socket)
            except Exception as exc:
                self.last_error = f"{exc.__class__.__name__}: {exc}"
            finally:
                self.connected = False
                try:
                    socket.close()
                except Exception:
                    pass
            if not self.ended:
                self._stop.wait(RETRY_SECONDS[0])

    def _pump(self, socket) -> None:
        next_send = time.monotonic()
        while not self._stop.is_set():
            now = time.monotonic()
            if now >= next_send:
                item = self._take()
                if item is not None:
                    frame, t_ms, voice = item
                    jpeg = self._encode(frame)
                    if jpeg:
                        socket.send(build_message({"t": round(t_ms, 1), "voice": voice}, jpeg))
                        self.sent += 1
                    next_send = now + self.interval
            try:
                text = socket.recv(timeout=0.02)
            except TimeoutError:
                text = None
            if text:
                try:
                    reply = json.loads(text)
                except (TypeError, ValueError):
                    reply = None
                if isinstance(reply, dict):
                    if self.on_reply is not None:
                        self.on_reply(reply)
                    if reply.get("ended"):
                        self.ended = True
                        return
