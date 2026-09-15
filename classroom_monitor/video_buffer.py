"""10-Second Evidence Video Ring Buffer and Clipping Pipeline.

Maintains a rolling RAM buffer of video frames (e.g. 5.0s pre-event)
and automatically records subsequent frames (e.g. 5.0s post-event)
when a cheating event is flagged, packaging the entire 10-second episode into
a verifiable MP4 evidence video for academic integrity reviews.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Deque, Dict, List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger("EvidenceVideoBuffer")


@dataclass
class BufferedFrame:
    """Timestamped video frame stored in rolling RAM buffer."""

    frame_idx: int
    timestamp_ms: float
    frame: np.ndarray


@dataclass
class VideoClipJob:
    """Active asynchronous or synchronous video clipping job."""

    event_id: str
    track_id: int
    behavior: str
    trigger_frame_idx: int
    trigger_timestamp_ms: float
    target_post_frames: int
    output_path: Path
    pre_frames: List[BufferedFrame] = field(default_factory=list)
    post_frames: List[BufferedFrame] = field(default_factory=list)
    is_completed: bool = False
    saved_file_path: Optional[str] = None


class EvidenceVideoBuffer:
    """Rolling Ring Buffer and Exporter for 10-Second Disciplinary Video Clips."""

    def __init__(
        self,
        pre_event_seconds: float = 5.0,
        post_event_seconds: float = 5.0,
        fps: float = 30.0,
        output_dir: str | Path = "data/evidence_clips",
    ):
        self.pre_event_seconds = max(0.5, pre_event_seconds)
        self.post_event_seconds = max(0.5, post_event_seconds)
        self.fps = max(1.0, fps)
        self.output_dir = Path(output_dir)

        self.max_pre_frames = int(round(self.pre_event_seconds * self.fps))
        self.max_post_frames = int(round(self.post_event_seconds * self.fps))

        # Rolling circular buffer for pre-event frames
        self._ring_buffer: Deque[BufferedFrame] = deque(maxlen=self.max_pre_frames)
        self._active_jobs: List[VideoClipJob] = []
        self._lock = threading.Lock()

    def add_frame(
        self,
        frame: np.ndarray,
        frame_idx: int,
        timestamp_ms: Optional[float] = None,
    ) -> List[VideoClipJob]:
        """Ingest a new video frame into the ring buffer and advance active recording jobs."""
        if frame is None or frame.size == 0:
            return []

        ts = (
            float(timestamp_ms)
            if timestamp_ms is not None
            else (time.time() * 1000.0)
        )

        buffered_frame = BufferedFrame(
            frame_idx=frame_idx,
            timestamp_ms=ts,
            frame=frame.copy(),
        )

        completed_jobs: List[VideoClipJob] = []

        with self._lock:
            # 1. Add to rolling circular buffer
            self._ring_buffer.append(buffered_frame)

            # 2. Append to any actively running clipping jobs
            remaining_jobs: List[VideoClipJob] = []
            for job in self._active_jobs:
                job.post_frames.append(buffered_frame)
                if len(job.post_frames) >= job.target_post_frames:
                    # Finalize and write MP4 clip
                    self._write_clip_to_disk(job)
                    job.is_completed = True
                    completed_jobs.append(job)
                else:
                    remaining_jobs.append(job)

            self._active_jobs = remaining_jobs

        return completed_jobs

    def trigger_clip(
        self,
        event_id: str,
        track_id: int,
        behavior: str,
        frame_idx: int,
        timestamp_ms: Optional[float] = None,
    ) -> VideoClipJob:
        """Start capturing a 10-second evidence clip around the trigger point."""
        ts = (
            float(timestamp_ms)
            if timestamp_ms is not None
            else (time.time() * 1000.0)
        )

        self.output_dir.mkdir(parents=True, exist_ok=True)
        safe_behavior = behavior.replace(" ", "_")
        filename = f"{event_id}_T{track_id}_{safe_behavior}.mp4"
        out_path = self.output_dir / filename

        with self._lock:
            # Snapshot all frames currently in ring buffer as pre-event history
            pre_frames_snapshot = list(self._ring_buffer)

            job = VideoClipJob(
                event_id=event_id,
                track_id=track_id,
                behavior=behavior,
                trigger_frame_idx=frame_idx,
                trigger_timestamp_ms=ts,
                target_post_frames=self.max_post_frames,
                output_path=out_path,
                pre_frames=pre_frames_snapshot,
                post_frames=[],
            )
            self._active_jobs.append(job)

        return job

    def _write_clip_to_disk(self, job: VideoClipJob) -> Optional[str]:
        """Concatenate pre- and post-event frames and encode to MP4."""
        all_frames = job.pre_frames + job.post_frames
        if not all_frames:
            return None

        job.output_path.parent.mkdir(parents=True, exist_ok=True)
        h, w = all_frames[0].frame.shape[:2]

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(job.output_path), fourcc, self.fps, (w, h))

        if not writer.isOpened():
            logger.error("Failed to initialize VideoWriter for clip: %s", job.output_path)
            return None

        try:
            for bf in all_frames:
                frame_to_write = bf.frame.copy()
                # Render metadata banner on bottom of evidence clip
                banner_h = 36
                cv2.rectangle(frame_to_write, (0, h - banner_h), (w, h), (0, 0, 0), -1)
                caption = (
                    f"EVIDENCE CLIP | ID: {job.event_id} | Track: {job.track_id} | "
                    f"Act: {job.behavior.upper()} | Frame: {bf.frame_idx}"
                )
                cv2.putText(
                    frame_to_write,
                    caption,
                    (12, h - 12),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.45,
                    (0, 255, 255),
                    1,
                    cv2.LINE_AA,
                )
                writer.write(frame_to_write)
        finally:
            writer.release()

        job.saved_file_path = str(job.output_path)
        logger.info("Saved 10s evidence clip (%d frames): %s", len(all_frames), job.output_path)
        return job.saved_file_path

    def flush_all(self) -> List[str]:
        """Force write all pending clipping jobs to disk immediately."""
        saved_paths: List[str] = []
        with self._lock:
            for job in self._active_jobs:
                path = self._write_clip_to_disk(job)
                if path:
                    job.is_completed = True
                    job.saved_file_path = path
                    saved_paths.append(path)
            self._active_jobs.clear()
        return saved_paths

    def reset(self) -> None:
        """Clear ring buffer and cancel all active jobs."""
        with self._lock:
            self._ring_buffer.clear()
            self._active_jobs.clear()
