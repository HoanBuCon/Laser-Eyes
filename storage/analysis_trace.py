"""What the analysis saw on each frame, for the Review page's debug overlay.

One JSON line per analysed frame, ``{"t": <ms of source time>, ...}``:

* VIGIL Classroom — ``frames.jsonl`` next to the run's other artifacts:
  ``{"t", "p": [[x1, y1, x2, y2, seat_or_null], ...]}`` (person boxes, frame pixels).
* VIGIL One — ``trace.jsonl`` in the session's video folder: face box ``fb`` (x, y, w, h),
  gaze ``gx``/``gy``, eye/head direction, faces/people counts and object boxes.

Only the debug overlay reads these files; a write that fails never stops the analysis.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

CLASSROOM_TRACE_FILE = "frames.jsonl"
ONE_TRACE_FILE = "trace.jsonl"
FLUSH_EVERY = 30  # lines


class TraceWriter:
    """Appends records to a JSONL file, keeping it open between frames."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._handle = None
        self._pending = 0
        self._broken = False

    def write(self, record: dict) -> None:
        if self._broken:
            return
        line = json.dumps(record, separators=(",", ":"), default=_plain)
        with self._lock:
            try:
                if self._handle is None:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    self._handle = open(self.path, "a", encoding="utf-8")
                self._handle.write(line + "\n")
                self._pending += 1
                if self._pending >= FLUSH_EVERY:
                    self._handle.flush()
                    self._pending = 0
            except OSError:
                logger.warning("Analysis trace: cannot write %s; tracing stops for it", self.path, exc_info=True)
                self._broken = True

    def flush(self) -> None:
        with self._lock:
            if self._handle is not None:
                try:
                    self._handle.flush()
                except OSError:
                    pass
                self._pending = 0

    def close(self) -> None:
        with self._lock:
            if self._handle is not None:
                try:
                    self._handle.close()
                except OSError:
                    pass
                self._handle = None


def append(path: Path, records: Iterable[dict]) -> None:
    """Open, append, close: for writers that come and go (no handle left open on Windows)."""
    lines = [json.dumps(r, separators=(",", ":"), default=_plain) for r in records]
    if not lines:
        return
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError:
        logger.warning("Analysis trace: cannot write %s", path, exc_info=True)


def _plain(value: Any) -> Any:
    """numpy numbers and tuples -> JSON."""
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, (tuple, set)):
        return list(value)
    return str(value)


def classroom_record(t_ms: float, detections: Iterable, occupancies: Optional[dict] = None) -> dict:
    """Person boxes of one frame, each with the seat it is assigned to (or null)."""
    seat_of = {}
    for code, occ in (occupancies or {}).items():
        det = getattr(occ, "assigned_detection", None)
        if det is not None:
            seat_of[id(det)] = code
    people = []
    for det in detections or ():
        x1, y1, x2, y2 = (int(v) for v in det.bbox)
        people.append([x1, y1, x2, y2, seat_of.get(id(det))])
    return {"t": round(float(t_ms), 1), "p": people}


def one_record(t_ms: float, result: Any, frame_size: tuple[int, int]) -> dict:
    """The face, gaze and objects of one VIGIL One frame (``result`` is an AnalysisResult)."""
    w, h = frame_size
    record: dict[str, Any] = {
        "t": round(float(t_ms), 1), "w": int(w), "h": int(h),
        "n": int(result.face_count), "p": int(result.person_count),
        "cal": bool(result.calibrated),
    }
    boxes = list(result.face_boxes or ([result.face_box] if result.face_box else []))
    if boxes:
        record["fb"] = [[int(v) for v in box] for box in boxes]
    if result.face_count:
        record.update({
            "gx": round(float(result.eye_gaze_x), 3), "gy": round(float(result.eye_gaze_y), 3),
            "out": bool(result.eyes_outside_zone),
            "eye": result.eye_direction, "head": result.head_direction,
            "yaw": round(float(result.yaw), 1), "pitch": round(float(result.pitch), 1),
        })
    objects = [[label, round(float(conf), 2), [int(v) for v in box]]
               for label, conf, box in (result.object_detections or [])]
    if objects:
        record["obj"] = objects
    return record


def read_window(path: Path, from_ms: float, to_ms: float, limit: int = 20000) -> list[dict]:
    """Records with ``from_ms <= t <= to_ms`` (the file is in time order, mostly)."""
    path = Path(path)
    if not path.is_file():
        return []
    out: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                # Cheap pre-filter on the leading "t" before parsing the whole line
                try:
                    t = float(line[5:line.index(",", 5)]) if line.startswith('{"t":') else None
                except ValueError:
                    t = None
                if t is not None and (t < from_ms or t > to_ms):
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue  # a line cut short by a crash
                t = float(record.get("t", -1))
                if from_ms <= t <= to_ms:
                    out.append(record)
                    if len(out) >= limit:
                        break
    except OSError:
        return []
    out.sort(key=lambda r: r["t"])
    return out
