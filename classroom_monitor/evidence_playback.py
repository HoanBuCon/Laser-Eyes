"""Create browser-compatible playback derivatives without mutating archived evidence."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import threading
import uuid
from pathlib import Path


class PlaybackUnavailable(RuntimeError):
    """Raised when an archived clip cannot be prepared for browser playback."""


logger = logging.getLogger(__name__)
_locks_guard = threading.Lock()
_conversion_locks: dict[str, threading.Lock] = {}


def playback_cache_root() -> Path:
    return Path(os.getenv("VIGIL_PLAYBACK_CACHE_ROOT", "data/evidence_playback")).resolve()


def probe_video_codec(path: str | Path) -> str | None:
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        return None
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        streams = json.loads(result.stdout).get("streams") or []
        return str(streams[0].get("codec_name")) if streams else None
    except (json.JSONDecodeError, AttributeError, IndexError, TypeError):
        return None


def _conversion_key(source: Path) -> str:
    stat = source.stat()
    identity = f"{source.resolve()}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8")
    return hashlib.sha256(identity).hexdigest()[:20]


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        return _conversion_locks.setdefault(key, threading.Lock())


def ensure_browser_playback(source_path: str | Path) -> tuple[Path, bool]:
    """Return an H.264 MP4 path and whether it is a generated derivative."""
    source = Path(source_path).resolve()
    if not source.is_file() or source.stat().st_size <= 0:
        raise PlaybackUnavailable("Archived evidence video is missing or empty")
    if source.suffix.lower() != ".mp4":
        raise PlaybackUnavailable("Only MP4 evidence can be prepared for browser playback")
    if probe_video_codec(source) == "h264":
        return source, False

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise PlaybackUnavailable("FFmpeg is required to convert archived evidence to browser-compatible H.264")

    key = _conversion_key(source)
    output_dir = playback_cache_root()
    output = output_dir / f"{source.stem}-{key}.mp4"
    with _lock_for(key):
        if output.is_file() and output.stat().st_size > 0 and probe_video_codec(output) == "h264":
            return output, True
        output_dir.mkdir(parents=True, exist_ok=True)
        temporary = output_dir / f".{output.stem}.{uuid.uuid4().hex}.tmp.mp4"
        try:
            result = subprocess.run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(source),
                    "-map",
                    "0:v:0",
                    "-an",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "23",
                    "-pix_fmt",
                    "yuv420p",
                    "-movflags",
                    "+faststart",
                    str(temporary),
                ],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if result.returncode != 0 or not temporary.is_file() or temporary.stat().st_size <= 0:
                detail = (result.stderr or "FFmpeg did not produce a playable file").strip()
                logger.warning("Evidence playback conversion failed for %s: %s", source.name, detail[-500:])
                raise PlaybackUnavailable("Archived evidence could not be converted for browser playback")
            if probe_video_codec(temporary) != "h264":
                raise PlaybackUnavailable("Evidence playback conversion did not produce H.264 video")
            temporary.replace(output)
            return output, True
        finally:
            if temporary.exists():
                temporary.unlink()
