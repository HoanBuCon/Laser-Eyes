"""Bounded, short-lived storage for exact captured Classroom frame bytes."""

from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Optional

import cv2
import numpy as np


@dataclass(frozen=True, slots=True)
class CapturedFrame:
    capture_id: str
    run_id: str
    session_id: str
    source_product: str
    source_kind: str
    source_ref: str
    frame_id: int
    source_timestamp_ms: Optional[float]
    captured_at_epoch: float
    width: int
    height: int
    jpeg_bytes: bytes
    coordinate_space: dict[str, Any]

    def metadata(self) -> dict[str, Any]:
        return {
            "capture_id": self.capture_id,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "source_product": self.source_product,
            "source_kind": self.source_kind,
            "source_ref": self.source_ref,
            "frame_id": self.frame_id,
            "source_timestamp_ms": self.source_timestamp_ms,
            "captured_at_epoch": self.captured_at_epoch,
            "width": self.width,
            "height": self.height,
            "coordinate_space": dict(self.coordinate_space),
        }


class FrameCaptureStore:
    """Stores encoded frames briefly so note-taking cannot change the evidence."""

    def __init__(self, *, ttl_seconds: float = 120.0, max_items: int = 32, jpeg_quality: int = 92):
        self.ttl_seconds = float(ttl_seconds)
        self.max_items = int(max_items)
        self.jpeg_quality = int(jpeg_quality)
        self._lock = threading.Lock()
        self._items: OrderedDict[str, CapturedFrame] = OrderedDict()

    def capture(
        self,
        frame: np.ndarray,
        *,
        run_id: str,
        session_id: str,
        source_product: str,
        source_kind: str,
        source_ref: str,
        frame_id: int,
        source_timestamp_ms: Optional[float],
    ) -> CapturedFrame:
        if frame is None or frame.size == 0:
            raise ValueError("No frame is available to capture")
        ok, encoded = cv2.imencode(
            ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality]
        )
        if not ok:
            raise ValueError("Frame JPEG encoding failed")
        height, width = frame.shape[:2]
        captured = CapturedFrame(
            capture_id=str(uuid.uuid4()),
            run_id=run_id,
            session_id=session_id,
            source_product=source_product,
            source_kind=source_kind,
            source_ref=source_ref,
            frame_id=int(frame_id),
            source_timestamp_ms=(float(source_timestamp_ms) if source_timestamp_ms is not None else None),
            captured_at_epoch=time.time(),
            width=int(width),
            height=int(height),
            jpeg_bytes=encoded.tobytes(),
            coordinate_space={
                "name": "SOURCE_FRAME_PIXELS",
                "origin": "TOP_LEFT",
                "width": int(width),
                "height": int(height),
            },
        )
        with self._lock:
            self._prune_locked()
            self._items[captured.capture_id] = captured
            while len(self._items) > self.max_items:
                self._items.popitem(last=False)
        return captured

    def get(self, capture_id: str) -> Optional[CapturedFrame]:
        with self._lock:
            self._prune_locked()
            return self._items.get(capture_id)

    def consume(self, capture_id: str) -> Optional[CapturedFrame]:
        with self._lock:
            self._prune_locked()
            return self._items.pop(capture_id, None)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def _prune_locked(self) -> None:
        cutoff = time.time() - self.ttl_seconds
        expired = [key for key, value in self._items.items() if value.captured_at_epoch < cutoff]
        for key in expired:
            self._items.pop(key, None)
