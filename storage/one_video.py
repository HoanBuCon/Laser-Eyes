"""VIGIL One full-session recording on the Hub.

The desktop app records the whole exam and uploads it in segments of about a minute while
the exam runs (a crash or a dropped network loses at most the current minute, and nearly all
of the video is already on the Hub when the candidate submits).  When the session ends the Hub
joins the segments into ONE file, ``session.mp4`` (``ffmpeg -c copy`` for H.264: no re-encode,
no quality loss), with its own SHA-256.  The uploaded segments and their digests are kept.

Minutes with no video (app closed, power cut) are listed as gaps instead of being hidden; a
moment of the exam (ms from its start) maps to a position in the joined file with ``locate``.
"""

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
from typing import Any, Callable, Iterator, Optional

from sqlalchemy.orm import Session

from classroom_monitor.evidence_playback import probe_video_codec
from storage.db_models import ExamSession, SessionVideoSegment

logger = logging.getLogger(__name__)

MAX_SEGMENT_BYTES = 64 * 1024 * 1024
GAP_TOLERANCE_MS = 1500        # timing jitter between segments that is not a gap
MERGE_TIMEOUT_SECONDS = 900

_merge_lock = threading.Lock()
_merging: set[str] = set()


class VideoError(ValueError):
    pass


def video_root() -> Path:
    from storage.one_service import one_evidence_root

    return one_evidence_root()


def session_dir(session_id: str) -> Path:
    return video_root() / session_id / "video"


def _check_mp4(data: bytes, sha256: Optional[str]) -> str:
    if not data:
        raise VideoError("Đoạn video rỗng.")
    if len(data) > MAX_SEGMENT_BYTES:
        raise VideoError(f"Đoạn video vượt quá {MAX_SEGMENT_BYTES // (1024 * 1024)} MB.")
    if data[4:8] != b"ftyp":
        raise VideoError("Chỉ nhận video MP4.")
    digest = hashlib.sha256(data).hexdigest()
    if sha256 and sha256.strip().lower() != digest:
        raise VideoError("SHA-256 không khớp với nội dung đoạn video.")
    return digest


def add_segment(db: Session, session: ExamSession, *, seq: int, start_ms: float, duration_s: float,
                data: bytes, sha256: Optional[str], speech: Optional[list] = None) -> tuple[SessionVideoSegment, bool]:
    """Store one uploaded segment.  Resending the same segment is a no-op; returns (segment, created).

    In a SERVER-analysis session every new segment is queued for the Hub's analysis
    (``storage.one_analysis``); ``speech`` are the stretches, in ms of the session, in which the
    candidate's microphone heard a voice (fused with lip movement there)."""
    if seq < 0 or seq > 100_000:
        raise VideoError("Số thứ tự đoạn video không hợp lệ.")
    if start_ms < 0 or not (0 < duration_s <= 3600):
        raise VideoError("Thời điểm hoặc độ dài đoạn video không hợp lệ.")
    digest = _check_mp4(data, sha256)
    existing = (db.query(SessionVideoSegment)
                .filter(SessionVideoSegment.session_id == session.id, SessionVideoSegment.seq == seq).first())
    if existing is not None:
        if existing.sha256 != digest:
            raise VideoError(f"Đoạn video số {seq} đã được gửi với nội dung khác.")
        return existing, False
    folder = session_dir(session.id)
    folder.mkdir(parents=True, exist_ok=True)
    final_path = folder / f"seg_{seq:05d}.mp4"
    temp_path = folder / f".seg_{seq:05d}.{uuid.uuid4().hex}.tmp"
    temp_path.write_bytes(data)
    os.replace(temp_path, final_path)
    segment = SessionVideoSegment(session_id=session.id, seq=seq, start_ms=float(start_ms), duration_s=float(duration_s),
                                  file_path=str(final_path), sha256=digest, file_size_bytes=len(data),
                                  speech_json=json.dumps(_clean_speech(speech)) if speech is not None else None,
                                  analysis_status="PENDING" if session.analysis_mode == "SERVER" else None)
    db.add(segment)
    if session.status in ("COMPLETED", "CLOSED"):
        session.video_status = "MERGING"  # a late segment: join again
    elif session.video_status in (None, "", "READY", "PARTIAL", "FAILED"):
        session.video_status = "RECORDING"
    db.commit()
    db.refresh(segment)
    return segment, True


def _clean_speech(items: Optional[list]) -> list[list[float]]:
    clean = []
    for item in (items or [])[:500]:
        try:
            start, end = float(item[0]), float(item[1])
        except (TypeError, ValueError, IndexError):
            continue
        if end > start >= 0:
            clean.append([round(start, 1), round(end, 1)])
    return clean


def segments(db: Session, session_id: str) -> list[SessionVideoSegment]:
    return (db.query(SessionVideoSegment).filter(SessionVideoSegment.session_id == session_id)
            .order_by(SessionVideoSegment.start_ms, SessionVideoSegment.seq).all())


def timeline(items: list[SessionVideoSegment], session_ms: Optional[float] = None) -> tuple[list[dict], list[list[float]]]:
    """Where each segment sits in the joined file, and the stretches of the exam with no video."""
    rows, gaps, offset, expected = [], [], 0.0, 0.0
    for seg in items:
        if seg.start_ms > expected + GAP_TOLERANCE_MS:
            gaps.append([round(expected), round(seg.start_ms)])
        rows.append({"seq": seg.seq, "start_ms": round(seg.start_ms, 1), "duration_s": round(seg.duration_s, 3),
                     "offset_s": round(offset, 3), "sha256": seg.sha256, "bytes": seg.file_size_bytes})
        offset += seg.duration_s
        expected = max(expected, seg.start_ms + seg.duration_s * 1000.0)
    if session_ms is not None and session_ms > expected + max(GAP_TOLERANCE_MS, 65_000):
        gaps.append([round(expected), round(session_ms)])  # the end of the exam never arrived
    return rows, gaps


def locate(rows: list[dict], elapsed_ms: float) -> Optional[float]:
    """Seconds into the joined file for a moment of the exam (the next recorded moment if it falls in a gap)."""
    for row in rows:
        end_ms = row["start_ms"] + row["duration_s"] * 1000.0
        if elapsed_ms < row["start_ms"]:
            return row["offset_s"]
        if elapsed_ms <= end_ms:
            return round(row["offset_s"] + (elapsed_ms - row["start_ms"]) / 1000.0, 3)
    return None


def _session_ms(session: ExamSession) -> Optional[float]:
    if session.started_at and session.ended_at:
        return max(0.0, (session.ended_at - session.started_at).total_seconds() * 1000.0)
    return None


def info(db: Session, session: ExamSession) -> dict[str, Any]:
    items = segments(db, session.id)
    rows, gaps = timeline(items, _session_ms(session))
    ready = session.video_status in ("READY", "PARTIAL") and session.video_path and Path(session.video_path).is_file()
    base = f"/api/v1/one/sessions/{session.id}/video"
    for row in rows:
        row["url"] = f"{base}/segments/{row['seq']}"
    return {
        "session_id": session.id,
        "status": session.video_status or ("RECORDING" if items else "NONE"),
        "url": f"{base}/file" if ready else None,
        "sha256": session.video_sha256 if ready else None,
        "duration_s": round(session.video_duration_s or sum(r["duration_s"] for r in rows), 3),
        "segment_count": len(rows),
        "bytes": sum(r["bytes"] or 0 for r in rows),
        "segments": rows,
        "gaps": gaps,
        "error": session.video_error,
    }


def merge(db: Session, session_id: str) -> dict[str, Any]:
    """Join the uploaded segments into one MP4 for the session (safe to run again)."""
    session = db.get(ExamSession, session_id)
    if session is None:
        raise VideoError("Phiên không tồn tại.")
    items = [s for s in segments(db, session_id) if Path(s.file_path).is_file()]
    if not items:
        session.video_status, session.video_error = "FAILED", "Không có đoạn video nào trên máy chủ."
        db.commit()
        return info(db, session)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        session.video_status = "FAILED"
        session.video_error = "Máy chủ cần FFmpeg để ghép video; các đoạn vẫn được giữ và xem được từng đoạn."
        db.commit()
        return info(db, session)
    session.video_status = "MERGING"
    db.commit()
    folder = session_dir(session_id)
    listing = folder / f".concat-{uuid.uuid4().hex}.txt"
    temp = folder / f".session-{uuid.uuid4().hex}.mp4"
    final = folder / "session.mp4"
    listing.write_text("".join(f"file '{Path(s.file_path).resolve().as_posix()}'\n" for s in items), encoding="utf-8")
    # H.264 segments are joined as they are; anything else (older clients) is converted once
    copy = all(probe_video_codec(s.file_path) == "h264" for s in items)
    codec = ["-c", "copy"] if copy else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p"]
    try:
        result = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                                 "-i", str(listing), "-map", "0:v:0", "-an", *codec, "-movflags", "+faststart", str(temp)],
                                capture_output=True, text=True, timeout=MERGE_TIMEOUT_SECONDS, check=False)
        if result.returncode != 0 or not temp.is_file() or temp.stat().st_size == 0:
            raise VideoError((result.stderr or "FFmpeg không tạo được file").strip()[-400:])
        os.replace(temp, final)
    except (VideoError, subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("Joining the video of session %s failed: %s", session_id, exc)
        session.video_status, session.video_error = "FAILED", f"Không ghép được video: {exc}"
        db.commit()
        return info(db, session)
    finally:
        listing.unlink(missing_ok=True)
        temp.unlink(missing_ok=True)
    digest = hashlib.sha256()
    with final.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    rows, gaps = timeline(items, _session_ms(session))
    session.video_path = str(final)
    session.video_sha256 = digest.hexdigest()
    session.video_duration_s = round(sum(s.duration_s for s in items), 3)
    session.video_gaps_json = json.dumps(gaps)
    session.video_status = "PARTIAL" if gaps else "READY"
    session.video_error = None
    db.commit()
    return info(db, session)


def merge_in_background(session_id: str, session_factory: Callable[[], Iterator[Session]]) -> None:
    """Join after the response; one join per session at a time (a second request runs it again after)."""
    with _merge_lock:
        if session_id in _merging:
            return
        _merging.add(session_id)
    try:
        for _ in range(2):  # a segment arriving during the join gets a second pass
            provider = session_factory()
            db = next(provider)
            try:
                before = db.query(SessionVideoSegment).filter(SessionVideoSegment.session_id == session_id).count()
                merge(db, session_id)
                after = db.query(SessionVideoSegment).filter(SessionVideoSegment.session_id == session_id).count()
            finally:
                provider.close()
            if after == before:
                break
    except Exception:
        logger.exception("Joining the video of session %s failed", session_id)
    finally:
        with _merge_lock:
            _merging.discard(session_id)


def needs_merge(db: Session, session: ExamSession) -> bool:
    if session.status not in ("COMPLETED", "CLOSED"):
        return False
    return db.query(SessionVideoSegment).filter(SessionVideoSegment.session_id == session.id).count() > 0

