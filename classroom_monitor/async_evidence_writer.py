"""Asynchronous Evidence Package Writer with SHA-256 File Integrity.

Implements Scope 08 (FR-EVI-001 to FR-EVI-008, P0-11, P0-12) of SRS v1.0:
- Non-blocking asynchronous video encoding using dedicated background ThreadPoolExecutor.
- Bounded concurrency queue to protect CPU/Disk I/O during concurrent room alerts.
- Produces complete Evidence Package:
    event_id/
    ├── snapshot.jpg
    ├── evidence.mp4
    └── metadata.json
- Computes cryptographic SHA-256 digest on output MP4 video.
- Graceful failure isolation (disk errors never crash the AI inference loop).
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import cv2
import numpy as np

logger = logging.getLogger("AsyncEvidenceWriter")


@dataclass
class EvidenceJob:
    """Specification for an evidence encoding task."""

    event_id: str
    frames: List[np.ndarray]
    peak_snapshot_frame: Optional[np.ndarray]
    fps: float
    output_dir: str
    metadata: Dict[str, Any]
    created_time: float = field(default_factory=time.time)
    callback: Optional[Callable[[Dict[str, Any]], None]] = None


def compute_file_sha256(file_path: str | Path) -> str:
    """Compute SHA-256 hexadecimal hash digest of a file on disk."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


class AsyncEvidenceWriter:
    """Non-blocking background evidence package writer."""

    def __init__(
        self,
        base_evidence_dir: str = "data/evidence_clips",
        max_workers: int = 2,
        max_queue_depth: int = 20,
    ):
        self.base_evidence_dir = Path(base_evidence_dir)
        self.base_evidence_dir.mkdir(parents=True, exist_ok=True)
        self.max_workers = max_workers
        self.max_queue_depth = max_queue_depth

        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="EvidenceWriter",
        )
        self._active_jobs_count = 0
        self._lock = threading.Lock()

    def submit_job(
        self,
        event_id: str,
        frames: List[np.ndarray],
        peak_snapshot_frame: Optional[np.ndarray] = None,
        fps: float = 30.0,
        metadata: Optional[Dict[str, Any]] = None,
        on_complete: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> bool:
        """Submit an asynchronous evidence packaging job.

        Returns True if accepted, False if queue is saturated.
        """
        with self._lock:
            if self._active_jobs_count >= self.max_queue_depth:
                logger.warning(
                    "Evidence queue is saturated (%d jobs). Dropping video clip for event %s",
                    self._active_jobs_count,
                    event_id,
                )
                return False
            self._active_jobs_count += 1

        job = EvidenceJob(
            event_id=event_id,
            frames=frames,
            peak_snapshot_frame=peak_snapshot_frame,
            fps=max(1.0, float(fps)),
            output_dir=str(self.base_evidence_dir),
            metadata=metadata or {},
            callback=on_complete,
        )

        self._executor.submit(self._execute_job, job)
        return True

    def _execute_job(self, job: EvidenceJob) -> None:
        """Worker thread execution: encodes MP4, saves snapshot, computes SHA-256, writes metadata.json."""
        event_dir = Path(job.output_dir) / job.event_id
        event_dir.mkdir(parents=True, exist_ok=True)

        snapshot_path: Optional[str] = None
        video_path: Optional[str] = None
        video_sha256: Optional[str] = None
        status = "READY"
        error_msg: Optional[str] = None

        try:
            # 1. Save Peak Snapshot JPEG
            if job.peak_snapshot_frame is not None and job.peak_snapshot_frame.size > 0:
                snap_p = event_dir / "snapshot.jpg"
                cv2.imwrite(str(snap_p), job.peak_snapshot_frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
                snapshot_path = str(snap_p.resolve())
            elif job.frames and len(job.frames) > 0:
                # Fallback to middle frame
                mid_frame = job.frames[len(job.frames) // 2]
                snap_p = event_dir / "snapshot.jpg"
                cv2.imwrite(str(snap_p), mid_frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
                snapshot_path = str(snap_p.resolve())

            # 2. Encode ~10-second MP4 Video
            if job.frames and len(job.frames) > 0:
                vid_p = event_dir / "evidence.mp4"
                h, w = job.frames[0].shape[:2]
                fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                writer = cv2.VideoWriter(str(vid_p), fourcc, job.fps, (w, h))

                for f in job.frames:
                    writer.write(f)
                writer.release()

                video_path = str(vid_p.resolve())

                # 3. Compute SHA-256 File Digest
                video_sha256 = compute_file_sha256(vid_p)

            # 4. Write metadata.json
            meta_payload = {
                "event_id": job.event_id,
                "created_at": time.time(),
                "snapshot_path": snapshot_path,
                "video_path": video_path,
                "video_sha256": video_sha256,
                "frame_count": len(job.frames),
                "fps": job.fps,
                "details": job.metadata,
            }
            meta_p = event_dir / "metadata.json"
            with open(meta_p, "w", encoding="utf-8") as mf:
                json.dump(meta_payload, mf, indent=2)

            logger.info(
                "Successfully generated Evidence Package for %s (SHA256: %s...)",
                job.event_id,
                video_sha256[:12] if video_sha256 else "None",
            )

        except Exception as exc:
            logger.error("Failed to generate evidence package for %s: %s", job.event_id, exc)
            status = "FAILED"
            error_msg = str(exc)

        finally:
            with self._lock:
                self._active_jobs_count = max(0, self._active_jobs_count - 1)

            # Fire completion callback if registered
            if job.callback:
                try:
                    job.callback({
                        "event_id": job.event_id,
                        "status": status,
                        "snapshot_path": snapshot_path,
                        "video_path": video_path,
                        "video_sha256": video_sha256,
                        "error_message": error_msg,
                    })
                except Exception as cb_exc:
                    logger.warning("Evidence job callback exception: %s", cb_exc)

    def shutdown(self, wait: bool = True) -> None:
        """Shutdown background threadpool."""
        self._executor.shutdown(wait=wait)
