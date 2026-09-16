"""Thread-safe Camera RTSP Ingestion Pipeline with Stale-Frame Dropping & Auto-Reconnect.

Implements Scope 02 (FR-CAM-001 to FR-CAM-007, P0-01, P0-02) of SRS v1.0:
- Thread-safe background frame grabber.
- Ultra-low latency Bounded Queue (size = 1..2) with immediate stale-frame drop.
- Automatic reconnection with exponential backoff on stream disconnection.
- Frame timestamping & latency protection (max_frame_age_ms).
- Per-camera realtime health & throughput metrics.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger("RTSPReader")


@dataclass
class VideoFrame:
    """Wrapped frame container holding precise timestamps and sequence numbers."""

    frame: np.ndarray
    capture_time: float  # Epoch timestamp in seconds
    timestamp_ms: float  # Epoch or monotonic timestamp in milliseconds
    frame_index: int
    camera_id: str
    width: int
    height: int


@dataclass
class CameraStreamMetrics:
    """Real-time observability metrics for an active camera ingestion pipeline."""

    camera_id: str
    source_uri: str
    is_connected: bool = False
    status: str = "OFFLINE"  # "ONLINE", "DEGRADED", "OFFLINE"
    capture_fps: float = 0.0
    inference_fps: float = 0.0
    total_captured_frames: int = 0
    total_dropped_frames: int = 0
    total_processed_frames: int = 0
    reconnect_count: int = 0
    average_frame_age_ms: float = 0.0
    queue_size: int = 0
    last_error: Optional[str] = None
    last_frame_time: float = 0.0


class RTSPStreamReader:
    """Ultra-low latency RTSP camera stream reader with bounded queue and auto-recovery."""

    def __init__(
        self,
        camera_id: str,
        source_uri: str,
        max_queue_size: int = 2,
        max_frame_age_ms: float = 800.0,
        reconnect_interval_sec: float = 3.0,
        max_reconnect_backoff_sec: float = 30.0,
    ):
        self.camera_id = camera_id
        self.source_uri = str(source_uri)
        self.max_queue_size = max(1, max_queue_size)
        self.max_frame_age_ms = max_frame_age_ms
        self.reconnect_interval_sec = reconnect_interval_sec
        self.max_reconnect_backoff_sec = max_reconnect_backoff_sec

        self._frame_queue: queue.Queue[VideoFrame] = queue.Queue(maxsize=self.max_queue_size)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self.metrics = CameraStreamMetrics(camera_id=camera_id, source_uri=self.source_uri)
        self._lock = threading.Lock()

    def start(self) -> RTSPStreamReader:
        """Start the background stream grabber thread."""
        if self._thread is not None and self._thread.is_alive():
            logger.warning("Stream reader for camera %s is already running", self.camera_id)
            return self

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._capture_loop,
            name=f"RTSP-Grabber-{self.camera_id}",
            daemon=True,
        )
        self._thread.start()
        logger.info("Started RTSP grabber for camera %s (Source: %s)", self.camera_id, self.source_uri)
        return self

    def stop(self) -> None:
        """Signal background thread to stop and release video resources."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        self.metrics.is_connected = False
        self.metrics.status = "OFFLINE"
        logger.info("Stopped RTSP grabber for camera %s", self.camera_id)

    def get_latest_frame(self, timeout: float = 0.5) -> Optional[VideoFrame]:
        """Fetch the freshest frame from queue, immediately discarding stale frames.

        Returns None if no frame is available or if frame exceeds max_frame_age_ms.
        """
        try:
            v_frame = self._frame_queue.get(timeout=timeout)
        except queue.Empty:
            return None

        # Check frame age for latency protection
        now_time = time.time()
        age_ms = (now_time - v_frame.capture_time) * 1000.0

        with self._lock:
            self.metrics.average_frame_age_ms = 0.8 * self.metrics.average_frame_age_ms + 0.2 * age_ms
            self.metrics.queue_size = self._frame_queue.qsize()

        if age_ms > self.max_frame_age_ms:
            # Drop stale frame to prevent accumulation of latency backlog
            with self._lock:
                self.metrics.total_dropped_frames += 1
            return None

        with self._lock:
            self.metrics.total_processed_frames += 1

        return v_frame

    def _capture_loop(self) -> None:
        """Continuous stream reading loop with auto-reconnection and exponential backoff."""
        backoff_sec = self.reconnect_interval_sec
        seq_idx = 0

        while not self._stop_event.is_set():
            logger.info("Connecting to video source '%s' (camera %s)...", self.source_uri, self.camera_id)
            cap = None
            try:
                # Open video stream
                # If numeric string e.g. "0", treat as local webcam index
                source = int(self.source_uri) if self.source_uri.isdigit() else self.source_uri
                cap = cv2.VideoCapture(source)

                if not cap.isOpened():
                    raise RuntimeError(f"Cannot open video stream source: {self.source_uri}")

                with self._lock:
                    self.metrics.is_connected = True
                    self.metrics.status = "ONLINE"
                    self.metrics.last_error = None
                backoff_sec = self.reconnect_interval_sec  # Reset backoff on success

                fps_calc_time = time.time()
                fps_frame_count = 0

                # Ingestion sub-loop
                while not self._stop_event.is_set():
                    ret, raw_frame = cap.read()
                    if not ret:
                        logger.warning("Stream read returned empty frame for camera %s", self.camera_id)
                        break

                    now = time.time()
                    now_ms = now * 1000.0
                    seq_idx += 1
                    fps_frame_count += 1

                    h, w = raw_frame.shape[:2]
                    v_frame = VideoFrame(
                        frame=raw_frame,
                        capture_time=now,
                        timestamp_ms=now_ms,
                        frame_index=seq_idx,
                        camera_id=self.camera_id,
                        width=w,
                        height=h,
                    )

                    # Manage ultra-low latency bounded queue: if full, drop oldest
                    if self._frame_queue.full():
                        try:
                            self._frame_queue.get_nowait()
                            with self._lock:
                                self.metrics.total_dropped_frames += 1
                        except queue.Empty:
                            pass

                    try:
                        self._frame_queue.put_nowait(v_frame)
                    except queue.Full:
                        with self._lock:
                            self.metrics.total_dropped_frames += 1

                    # Update throughput metrics
                    with self._lock:
                        self.metrics.total_captured_frames += 1
                        self.metrics.last_frame_time = now

                    # Calculate moving average capture FPS every 1 second
                    if now - fps_calc_time >= 1.0:
                        with self._lock:
                            self.metrics.capture_fps = round(fps_frame_count / (now - fps_calc_time), 1)
                        fps_calc_time = now
                        fps_frame_count = 0

            except Exception as exc:
                logger.error("RTSP stream error on camera %s: %s", self.camera_id, exc)
                with self._lock:
                    self.metrics.is_connected = False
                    self.metrics.status = "DEGRADED"
                    self.metrics.last_error = str(exc)
                    self.metrics.reconnect_count += 1
            finally:
                if cap is not None:
                    cap.release()

            # Wait backoff interval before attempting reconnection
            if not self._stop_event.is_set():
                logger.info("Reconnecting camera %s in %.1f seconds...", self.camera_id, backoff_sec)
                self._stop_event.wait(timeout=backoff_sec)
                backoff_sec = min(self.max_reconnect_backoff_sec, backoff_sec * 1.5)
