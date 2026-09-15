"""Hierarchical File-Based Evidence Store Manager for VIGIL AI."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger("EvidenceStore")


class EvidenceStore:
    """Manages writing, retrieving, and pruning evidence images on filesystem."""

    def __init__(self, base_dir: str | Path = "data/evidence"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save_evidence(
        self,
        event_id: str,
        frame: np.ndarray,
        site_id: str = "default_site",
        room_id: str = "default_room",
        session_id: str = "default_session",
        quality: int = 92,
    ) -> Tuple[str, int]:
        """Save frame to nested folder hierarchy and return relative path and byte size."""
        target_dir = self.base_dir / str(site_id) / str(room_id) / str(session_id)
        target_dir.mkdir(parents=True, exist_ok=True)

        clean_event_id = event_id.replace("/", "_").replace("\\", "_")
        target_file = target_dir / f"{clean_event_id}.jpg"

        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
        success, encimg = cv2.imencode(".jpg", frame, encode_param)

        if not success:
            logger.error("Failed to encode JPEG image for event: %s", event_id)
            return "", 0

        with open(target_file, "wb") as f:
            f.write(encimg)

        file_size = target_file.stat().st_size
        relative_path = str(target_file.relative_to(self.base_dir)).replace("\\", "/")
        return relative_path, file_size

    def get_absolute_path(self, relative_path: str) -> Optional[Path]:
        """Resolve relative evidence path to full absolute system path."""
        p = self.base_dir / relative_path
        if p.exists() and p.is_file():
            return p
        return None

    def cleanup_old_evidence(self, days: int = 90) -> int:
        """Remove evidence files older than specified retention period."""
        cutoff = time.time() - (days * 86400)
        pruned_count = 0
        for f in self.base_dir.rglob("*.jpg"):
            if f.is_file() and f.stat().st_mtime < cutoff:
                try:
                    f.unlink()
                    pruned_count += 1
                except Exception as exc:
                    logger.warning("Could not delete stale evidence file %s: %s", f, exc)
        return pruned_count
