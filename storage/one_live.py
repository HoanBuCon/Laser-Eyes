"""Live video of VIGIL One candidates on the Hub (memory only, never written to disk).

The candidate app streams small JPEG frames (about ``analysis_fps`` per second) over a
WebSocket.  Each frame lands in the session's slot here:

* the Hub's analysis (``storage.one_analysis``) takes the newest frame when it is free —
  a busy Hub skips frames instead of falling behind;
* proctors watching the session on the web get the newest frame, with the analysis drawn
  on it, as an MJPEG stream (``/api/v1/one/sessions/{id}/live.mjpg``).

The full-quality recording still arrives as H.264 segments (``storage.one_video``): it is
the archive, the source of evidence clips and of the analysis of stretches the live
stream missed.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

MAX_FRAME_BYTES = 1024 * 1024
VIEWER_TTL_SECONDS = 6.0     # a viewer that stopped asking for frames no longer costs an overlay
STALE_SECONDS = 5.0          # no frame for this long = the stream is down
ANNOTATED_MAX_AGE_S = 1.5    # an analysed picture this recent is shown instead of the raw one
AHEAD_TOLERANCE_S = 2.0      # the app's clock may not run ahead of the Hub's by more than this
START_TOLERANCE_MS = 60_000  # first frame: at most this far past the time elapsed since the session began


@dataclass
class Frame:
    jpeg: bytes
    t_ms: float                      # session clock of the candidate app (same as the video segments)
    voice: Optional[bool] = None     # microphone heard speech around this frame (None = no microphone)
    received: float = field(default_factory=time.time)
    seq: int = 0


@dataclass
class _Slot:
    latest: Optional[Frame] = None
    taken_seq: int = 0               # newest frame the analysis has taken
    annotated: Optional[bytes] = None
    annotated_seq: int = 0
    annotated_at: float = 0.0
    viewers_until: float = 0.0
    received: list[float] = field(default_factory=list)   # arrival times over the last seconds (fps)
    analysed: list[float] = field(default_factory=list)
    connected: bool = False
    clock_offset: Optional[float] = None   # Hub time - frame time, from the first frame accepted
    last_t_ms: float = -1.0
    refused: int = 0


_clock = time.time   # the Hub's clock (tests drive it)
_lock = threading.Lock()
_slots: dict[str, _Slot] = {}
_seq = 0


def _slot(session_id: str) -> _Slot:
    slot = _slots.get(session_id)
    if slot is None:
        slot = _slots[session_id] = _Slot()
    return slot


def _recent(times: list[float], now: float, window: float = 5.0) -> list[float]:
    return [t for t in times if now - t <= window]


def put_frame(session_id: str, jpeg: bytes, t_ms: float, voice: Optional[bool] = None,
              elapsed_ms: Optional[float] = None) -> Frame:
    """A frame from the candidate app.  Also becomes the session's live thumbnail.

    The frame's time comes from the app, so the Hub holds it to its own clock: it must move
    forward, never faster than real time, and (``elapsed_ms``: time since the session began on the
    Hub) not start in the future.  Otherwise a stream could claim to have covered stretches of the
    exam it never showed, and the recording of them would not be analysed."""
    global _seq
    if not jpeg or len(jpeg) > MAX_FRAME_BYTES or jpeg[:2] != b"\xff\xd8":
        raise ValueError("Khung hình phải là JPEG nhỏ hơn 1 MB.")
    t_ms = float(t_ms)
    now = _clock()
    with _lock:
        slot = _slot(session_id)
        reason = None
        if not (t_ms >= 0) or t_ms <= slot.last_t_ms:
            reason = "Thời điểm khung hình phải tăng dần."
        elif slot.clock_offset is not None and (now - t_ms / 1000.0) - slot.clock_offset < -AHEAD_TOLERANCE_S:
            reason = "Thời điểm khung hình chạy nhanh hơn đồng hồ máy chủ."
        elif slot.clock_offset is None and elapsed_ms is not None and t_ms > elapsed_ms + START_TOLERANCE_MS:
            reason = "Thời điểm khung hình vượt quá thời gian của phiên."
        if reason:
            slot.refused += 1
            raise ValueError(reason)
        if slot.clock_offset is None:
            slot.clock_offset = now - t_ms / 1000.0
        else:  # follow network delay that shrinks, never let the frame clock run ahead
            slot.clock_offset = min(slot.clock_offset, now - t_ms / 1000.0)
        slot.last_t_ms = t_ms
        _seq += 1
        frame = Frame(jpeg=jpeg, t_ms=t_ms, voice=voice, received=now, seq=_seq)
        slot.latest = frame
        slot.connected = True
        slot.received = _recent(slot.received, now) + [now]
    from storage import one_service

    one_service.set_live_thumbnail(session_id, jpeg)
    return frame


def take(session_id: str) -> Optional[Frame]:
    """The newest frame not yet given to the analysis (older ones are skipped)."""
    with _lock:
        slot = _slots.get(session_id)
        if slot is None or slot.latest is None or slot.latest.seq <= slot.taken_seq:
            return None
        slot.taken_seq = slot.latest.seq
        return slot.latest


def analysed(session_id: str, frame: Frame, annotated: Optional[bytes]) -> None:
    now = _clock()
    with _lock:
        slot = _slot(session_id)
        slot.analysed = _recent(slot.analysed, now) + [now]
        if annotated is not None and frame.seq >= slot.annotated_seq:
            slot.annotated, slot.annotated_seq, slot.annotated_at = annotated, frame.seq, now


def disconnected(session_id: str) -> None:
    with _lock:
        slot = _slots.get(session_id)
        if slot is not None:
            slot.connected = False


def forget(session_id: str) -> None:
    with _lock:
        _slots.pop(session_id, None)


def watched(session_id: str) -> bool:
    """Someone is watching: worth drawing the analysis on the frames."""
    with _lock:
        slot = _slots.get(session_id)
        return slot is not None and slot.viewers_until > _clock()


def view(session_id: str, after_seq: int = 0, annotated: bool = True) -> Optional[tuple[int, bytes]]:
    """For a viewer: the newest picture (annotated when the analysis has drawn it), if newer than after_seq."""
    now = _clock()
    with _lock:
        slot = _slot(session_id)
        if annotated:
            slot.viewers_until = now + VIEWER_TTL_SECONDS
        frame = slot.latest
        if frame is None or now - frame.received > STALE_SECONDS:
            return None
        # The annotated copy lags one analysis behind. Keep showing it while it is recent, even when the
        # analysis runs a few frames behind a busy Hub: falling back to raw frames then made the drawing
        # (face box, gaze) flicker on and off for the proctor
        if annotated and slot.annotated is not None and now - slot.annotated_at <= ANNOTATED_MAX_AGE_S:
            seq, data = slot.annotated_seq, slot.annotated
        else:
            seq, data = frame.seq, frame.jpeg
        return (seq, data) if seq > after_seq else None


def stats(session_id: str) -> dict[str, Any]:
    now = _clock()
    with _lock:
        slot = _slots.get(session_id)
        if slot is None or slot.latest is None:
            return {"live": False}
        received = _recent(slot.received, now)
        done = _recent(slot.analysed, now)
        age = now - slot.latest.received
        return {"live": slot.connected and age <= STALE_SECONDS, "age_s": round(age, 1), "refused": slot.refused,
                "fps_in": round(len(received) / 5.0, 1), "fps_analysed": round(len(done) / 5.0, 1)}


def latest(session_id: str) -> Optional[Frame]:
    """The newest raw frame (for a proctor's bookmark), if the stream is up."""
    with _lock:
        slot = _slots.get(session_id)
        frame = slot.latest if slot is not None else None
    if frame is None or _clock() - frame.received > STALE_SECONDS:
        return None
    return frame


def parse_message(data: bytes) -> tuple[dict, bytes]:
    """A live message from the app: 4-byte big-endian header length, JSON header, JPEG."""
    import json
    import struct

    if len(data) < 6:
        raise ValueError("Tin nhắn quá ngắn.")
    (size,) = struct.unpack(">I", data[:4])
    if size > 4096 or 4 + size > len(data):
        raise ValueError("Phần đầu tin nhắn không hợp lệ.")
    header = json.loads(data[4:4 + size].decode("utf-8"))
    if not isinstance(header, dict):
        raise ValueError("Phần đầu phải là JSON object.")
    return header, data[4 + size:]


def build_message(header: dict, jpeg: bytes) -> bytes:
    import json
    import struct

    raw = json.dumps(header, separators=(",", ":")).encode("utf-8")
    return struct.pack(">I", len(raw)) + raw + jpeg
