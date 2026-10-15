"""VIGIL One perception on the Hub (sessions in analysis_mode SERVER).

The candidate's computer only records the exam and uploads it in short segments
(``storage.one_video``).  This module analyses those segments on the server, next to
VIGIL Classroom: the same ``GazeAnalyzer`` + ``EventDetector`` the desktop app used,
fed with the video frames at ``analysis_fps`` and the video's own clock.

* Segments are the job queue: ``SessionVideoSegment.analysis_status`` goes
  PENDING -> DONE | FAILED | SKIPPED, so a restarted server simply carries on.
* A session is analysed in segment order by one worker at a time (the analyser and
  the event detector keep state across segments).  A missing segment holds the session
  for ``GAP_WAIT_SECONDS``; after that it is skipped and the behaviour state restarts.
* Calibration happens on the first seconds of video (the app asks the candidate to
  look at the screen) and is saved with the session, so a restart does not redo it.
* Each signal becomes a review incident through ``one_service.ingest_events`` with a
  JPEG of the frame and, once the following video has arrived, a clip of
  ``CLIP_BEFORE_MS`` + ``CLIP_AFTER_MS`` cut from the segments (SHA-256 as usual).

Signals are observations for a proctor to review, never verdicts.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import queue
import tempfile
import threading
import time
import zlib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Optional

import cv2
from sqlalchemy.orm import Session

from exam_monitor.audio import AudioSpeechState, speech_is_confirmed
from exam_monitor.events import EventDetector
from exam_monitor.models import AnalysisResult, EventType
from storage import one_service as one
from storage.db_models import DetectionEvent, Exam, ExamSession, SessionVideoSegment

logger = logging.getLogger(__name__)

CLIP_BEFORE_MS = 5000
CLIP_AFTER_MS = 4000
GAP_WAIT_SECONDS = 45.0
CONFIG_VERSION = "vigil-one-server-v1"
# Same as the desktop app writes: H.264 where Windows can encode it, mp4v otherwise (the review page
# makes an H.264 copy for browsers on demand).
_CLIP_CODECS = (((cv2.CAP_MSMF, "avc1"),) if os.name == "nt" else ()) + ((cv2.CAP_ANY, "mp4v"),)

AnalyzerFactory = Callable[[dict], Any]


def default_analyzer_factory(policy: dict) -> Any:
    """MediaPipe face/iris + object detector, on the video's clock, calibrating from the start."""
    from exam_monitor.engine import GazeAnalyzer

    analyzer = GazeAnalyzer(frame_clock=True, object_every=2)
    analyzer.set_allowed_materials({"book"} if policy.get("allow_materials") else set())
    analyzer.begin_calibration()
    return analyzer


def policy_for_session(db: Session, session: ExamSession) -> dict:
    from storage import online_exam_service as online

    exam = db.get(Exam, session.exam_id) if session.exam_id else None
    return online.policy_for(exam) if exam is not None else dict(online.DEFAULT_POLICY)


@dataclass
class _SessionState:
    analyzer: Any
    detector: EventDetector
    next_seq: int = 0
    frames: int = 0
    analyzed_until_ms: float = 0.0
    pending_clips: list[dict] = field(default_factory=list)
    last_result: Optional[AnalysisResult] = None

    def to_json(self) -> str:
        snapshot = self.analyzer.calibration_snapshot() if hasattr(self.analyzer, "calibration_snapshot") else {}
        return json.dumps({"next_seq": self.next_seq, "frames": self.frames,
                           "analyzed_until_ms": round(self.analyzed_until_ms, 1),
                           "calibration": snapshot, "pending_clips": self.pending_clips}, ensure_ascii=False)


_DIRECTION = {"center": "THẲNG", "left": "TRÁI", "right": "PHẢI", "up": "TRÊN", "down": "DƯỚI", "unknown": "—"}


class OneAnalysisService:
    """Worker threads that analyse SERVER-mode VIGIL One sessions from their uploaded video."""

    def __init__(self, session_factory: Callable[[], Session], *, workers: int = 2,
                 analyzer_factory: Optional[AnalyzerFactory] = None, poll_seconds: float = 3.0,
                 on_events: Optional[Callable[[str, int], None]] = None):
        self.session_factory = session_factory
        self.workers = max(1, int(workers))
        self.analyzer_factory = analyzer_factory or default_analyzer_factory
        self.poll_seconds = poll_seconds
        self.on_events = on_events
        self._queues = [queue.Queue() for _ in range(self.workers)]
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._states: dict[str, _SessionState] = {}
        self._states_lock = threading.Lock()
        self._session_locks: dict[str, threading.Lock] = {}
        self._processed_segments = 0
        self._failed_segments = 0

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._threads:
            return
        self._stop.clear()
        for index in range(self.workers):
            thread = threading.Thread(target=self._loop, args=(index,), name=f"vigil-one-analysis-{index}", daemon=True)
            thread.start()
            self._threads.append(thread)
        logger.info("VIGIL One server analysis: %d worker(s)", self.workers)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        for q in self._queues:
            q.put(None)
        for thread in self._threads:
            thread.join(timeout=timeout)
        self._threads.clear()

    def notify(self, session_id: str) -> None:
        """A segment arrived or the session ended: let its worker look at it now."""
        self._queues[self._shard(session_id)].put(session_id)

    def status(self) -> dict[str, Any]:
        with self._states_lock:
            active = len(self._states)
        return {"workers": self.workers, "running": bool(self._threads), "active_sessions": active,
                "queued": sum(q.qsize() for q in self._queues), "segments_done": self._processed_segments,
                "segments_failed": self._failed_segments}

    def _shard(self, session_id: str) -> int:
        return zlib.crc32(session_id.encode()) % self.workers

    def _loop(self, index: int) -> None:
        while not self._stop.is_set():
            try:
                session_id = self._queues[index].get(timeout=self.poll_seconds)
            except queue.Empty:
                session_id = ""
            if self._stop.is_set():
                return
            ids = [session_id] if session_id else self._pending_session_ids(index)
            for sid in ids:
                db = self.session_factory()
                try:
                    self.process_session(db, sid)
                except Exception:  # one bad session must not stop the worker
                    logger.exception("VIGIL One analysis failed for session %s", sid)
                finally:
                    db.close()

    def _pending_session_ids(self, index: int) -> list[str]:
        db = self.session_factory()
        try:
            rows = (db.query(SessionVideoSegment.session_id)
                    .filter(SessionVideoSegment.analysis_status == "PENDING").distinct().all())
            ids = {row[0] for row in rows}
            # Ended sessions may still owe clips for their last signals
            for session in (db.query(ExamSession)
                            .filter(ExamSession.analysis_mode == "SERVER",
                                    ExamSession.status.in_(("COMPLETED", "INTERRUPTED")),
                                    ExamSession.analysis_state_json.like('%"from_ms"%')).all()):
                ids.add(session.id)
        finally:
            db.close()
        return [sid for sid in ids if self._shard(sid) == index]

    # ------------------------------------------------------------ one session
    def process_session(self, db: Session, session_id: str) -> dict[str, int]:
        """Analyse every segment of the session that can be analysed now (in order). Thread-safe."""
        with self._states_lock:
            lock = self._session_locks.setdefault(session_id, threading.Lock())
        with lock:
            return self._process_locked(db, session_id)

    def _process_locked(self, db: Session, session_id: str) -> dict[str, int]:
        done = {"segments": 0, "events": 0}
        session = db.get(ExamSession, session_id)
        if session is None or session.analysis_mode != "SERVER":
            return done
        policy = policy_for_session(db, session)
        state = self._state(db, session, policy)
        ended = session.status not in ("RUNNING", "STOPPING")
        while True:
            seg = (db.query(SessionVideoSegment)
                   .filter(SessionVideoSegment.session_id == session.id, SessionVideoSegment.analysis_status == "PENDING")
                   .order_by(SessionVideoSegment.seq).first())
            if seg is None:
                break
            if seg.seq < state.next_seq:  # re-sent after the session moved on (e.g. restored state)
                seg.analysis_status = "SKIPPED"
                db.commit()
                continue
            if seg.seq > state.next_seq:
                waited = (datetime.datetime.utcnow() - (seg.created_at or datetime.datetime.utcnow())).total_seconds()
                if not ended and waited < GAP_WAIT_SECONDS:
                    break  # the missing segment may still be on its way (outbox after a network drop)
                logger.info("VIGIL One %s: segments %d..%d missing, continuing", session.id, state.next_seq, seg.seq - 1)
                state.detector.reset()  # behaviour does not run on across missing video
            try:
                done["events"] += self._analyse_segment(db, session, seg, state, policy)
                seg.analysis_status = "DONE"
                seg.analysis_error = None
                self._processed_segments += 1
            except Exception as exc:
                logger.exception("VIGIL One %s: segment %d failed", session.id, seg.seq)
                db.rollback()
                seg = db.get(SessionVideoSegment, seg.id)
                seg.analysis_status = "FAILED"
                seg.analysis_error = f"{exc.__class__.__name__}: {exc}"[:500]
                self._failed_segments += 1
            seg.analyzed_at = datetime.datetime.utcnow()
            state.next_seq = seg.seq + 1
            state.analyzed_until_ms = max(state.analyzed_until_ms, seg.start_ms + seg.duration_s * 1000.0)
            self._flush_clips(db, session, state, state.analyzed_until_ms)
            session.analysis_state_json = state.to_json()
            db.commit()
            done["segments"] += 1
        db.refresh(session)
        still_pending = (db.query(SessionVideoSegment.id)
                         .filter(SessionVideoSegment.session_id == session.id,
                                 SessionVideoSegment.analysis_status == "PENDING").first() is not None)
        if session.status not in ("RUNNING", "STOPPING") and not still_pending:
            self._flush_clips(db, session, state, float("inf"))  # no more video will come
            session.analysis_state_json = state.to_json()
            db.commit()
            with self._states_lock:
                self._states.pop(session.id, None)
        if done["events"] and self.on_events is not None:
            try:
                self.on_events(session.id, done["events"])
            except Exception:
                logger.exception("VIGIL One analysis: event callback failed")
        return done

    def _state(self, db: Session, session: ExamSession, policy: dict) -> _SessionState:
        with self._states_lock:
            state = self._states.get(session.id)
        if state is not None:
            return state
        analyzer = self.analyzer_factory(policy)
        detector = EventDetector()
        for name, seconds in (policy.get("thresholds") or {}).items():
            try:
                detector.set_threshold(EventType(name), float(seconds))
            except (ValueError, KeyError):
                pass
        state = _SessionState(analyzer=analyzer, detector=detector)
        try:
            saved = json.loads(session.analysis_state_json or "{}")
        except (TypeError, json.JSONDecodeError):
            saved = {}
        if saved:  # a restarted server resumes where it stopped, calibration included
            state.next_seq = int(saved.get("next_seq") or 0)
            state.frames = int(saved.get("frames") or 0)
            state.analyzed_until_ms = float(saved.get("analyzed_until_ms") or 0.0)
            state.pending_clips = list(saved.get("pending_clips") or [])
            if hasattr(analyzer, "restore_calibration"):
                analyzer.restore_calibration(saved.get("calibration") or {})
        with self._states_lock:
            self._states[session.id] = state
        return state

    # ------------------------------------------------------------ one segment
    def _analyse_segment(self, db: Session, session: ExamSession, seg: SessionVideoSegment,
                         state: _SessionState, policy: dict) -> int:
        cap = cv2.VideoCapture(seg.file_path)
        if not cap.isOpened():
            raise RuntimeError("cannot open the segment video")
        try:
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            video_fps = float(policy.get("video_fps") or 10)
            frame_ms = (seg.duration_s * 1000.0 / total) if total > 0 else 1000.0 / video_fps
            step = max(1, int(round((1000.0 / frame_ms) / max(1.0, float(policy.get("analysis_fps") or 5)))))
            speech = json.loads(seg.speech_json) if seg.speech_json else None
            audio = speech is not None and bool(policy.get("microphone", True))
            created = analysed = index = 0
            while True:
                ok, frame = cap.read()
                if not ok or frame is None:
                    break
                if index % step:
                    index += 1
                    continue
                t_ms = seg.start_ms + index * frame_ms
                result = state.analyzer.analyze(frame, t_ms / 1000.0)
                if audio:
                    voice = any(start <= t_ms <= end for start, end in speech)
                    result = replace(result, audio_available=True, voice_detected=voice,
                                     is_talking=speech_is_confirmed(result.talking_score, result.is_talking,
                                                                    AudioSpeechState(available=True, voice_active=voice)))
                for event in state.detector.update(result, session.id, now=t_ms / 1000.0):
                    created += self._emit(db, session, state, event, frame, t_ms)
                state.last_result = result
                analysed += 1
                index += 1
        finally:
            cap.release()
        seg.frames_analyzed = analysed
        state.frames += analysed
        self._update_live(db, session, state)
        return created

    def _emit(self, db: Session, session: ExamSession, state: _SessionState, event, frame, t_ms: float) -> int:
        """One signal -> review incident + snapshot now, clip once the next seconds of video are in."""
        start = session.started_at or datetime.datetime.utcnow()
        elapsed_ms = max(0.0, t_ms - float(event.duration_seconds) * 1000.0)
        kind = event.event_type.value
        event_id = f"SV{session.id.replace('-', '')[:10]}{int(t_ms):09d}{kind[:4].upper()}"[:36]
        item = {
            "event_id": event_id, "event_type": kind, "severity": event.severity.value,
            "started_at": (start + datetime.timedelta(milliseconds=elapsed_ms)).isoformat() + "+00:00",
            "ended_at": (start + datetime.timedelta(milliseconds=t_ms)).isoformat() + "+00:00",
            "duration_seconds": round(float(event.duration_seconds), 2), "session_elapsed_ms": round(elapsed_ms, 1),
            "confidence": round(float(event.confidence), 3), "label": event.label, "reason": event.reason,
            "config_version": CONFIG_VERSION, "_server": True,
        }
        counts = one.ingest_events(db, session, [item])
        if not counts["created"]:
            return 0
        row = db.query(DetectionEvent).filter(DetectionEvent.event_id == event_id).first()
        ok, jpeg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
        if ok and row is not None:
            one.store_snapshot(db, row, jpeg.tobytes())
        state.pending_clips.append({"event_id": event_id, "from_ms": max(0.0, t_ms - CLIP_BEFORE_MS),
                                    "to_ms": t_ms + CLIP_AFTER_MS})
        return 1

    def _update_live(self, db: Session, session: ExamSession, state: _SessionState) -> None:
        result = state.last_result
        snapshot = state.analyzer.calibration_snapshot() if hasattr(state.analyzer, "calibration_snapshot") else {}
        started = session.started_at or datetime.datetime.utcnow()
        lag = (datetime.datetime.utcnow() - started).total_seconds() - state.analyzed_until_ms / 1000.0
        values: dict[str, Any] = {"analysis": {"mode": "SERVER", "calibration": snapshot.get("state", "—"),
                                               "lag_s": round(max(0.0, lag), 1), "frames": state.frames}}
        if result is not None:
            values["analysis"]["faces"] = int(result.face_count)  # the candidate's app shows hints from this
            values["calibrated"] = bool(result.calibrated)
            values["people"] = int(result.detected_people_count)
            values["head"] = _DIRECTION.get(result.head_direction, "—") if result.face_count else "—"
            values["eye"] = "—" if not result.face_count else ("ĐANG HỌC" if not result.calibrated else (
                f"NGOÀI • {_DIRECTION.get(result.eye_direction, '—')}" if result.eyes_outside_zone else "TRONG VÙNG"))
        one.update_live_analysis(db, session, values)

    # ------------------------------------------------------------ clips
    def _flush_clips(self, db: Session, session: ExamSession, state: _SessionState, until_ms: float) -> None:
        keep = []
        for clip in state.pending_clips:
            if clip["to_ms"] > until_ms:
                keep.append(clip)
                continue
            try:
                self._write_clip(db, session, clip)
            except Exception:
                logger.exception("VIGIL One %s: clip for %s failed", session.id, clip.get("event_id"))
        state.pending_clips = keep

    def _write_clip(self, db: Session, session: ExamSession, clip: dict) -> None:
        row = db.query(DetectionEvent).filter(DetectionEvent.event_id == clip["event_id"]).first()
        if row is None:
            return
        from_ms, to_ms = float(clip["from_ms"]), float(clip["to_ms"])
        segs = (db.query(SessionVideoSegment)
                .filter(SessionVideoSegment.session_id == session.id)
                .order_by(SessionVideoSegment.start_ms).all())
        segs = [s for s in segs if s.start_ms < to_ms and s.start_ms + s.duration_s * 1000.0 > from_ms]
        if not segs:
            return
        data = cut_clip([(s.file_path, s.start_ms, s.duration_s) for s in segs], from_ms, to_ms)
        if data:
            one.store_clip(db, row, data)


def cut_clip(pieces: list[tuple[str, float, float]], from_ms: float, to_ms: float) -> Optional[bytes]:
    """Frames between from_ms and to_ms (session time) of consecutive segments, as one MP4."""
    writer = None
    fps = 10.0
    tmp = Path(tempfile.mkstemp(prefix="vigil-one-clip-", suffix=".mp4")[1])
    try:
        for path, start_ms, duration_s in pieces:
            cap = cv2.VideoCapture(path)
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            frame_ms = (duration_s * 1000.0 / total) if total > 0 else 100.0
            fps = 1000.0 / frame_ms
            index = 0
            while True:
                ok, frame = cap.read()
                if not ok or frame is None:
                    break
                t_ms = start_ms + index * frame_ms
                index += 1
                if t_ms < from_ms or t_ms > to_ms:
                    continue
                if writer is None:
                    h, w = frame.shape[:2]
                    for backend, code in _CLIP_CODECS:
                        candidate = cv2.VideoWriter(str(tmp), backend, cv2.VideoWriter_fourcc(*code), fps, (w, h))
                        if candidate.isOpened():
                            writer = candidate
                            break
                    if writer is None:
                        return None
                writer.write(frame)
            cap.release()
        if writer is None:
            return None
        writer.release()
        writer = None
        data = tmp.read_bytes()
        return data if data[4:8] == b"ftyp" else None
    finally:
        if writer is not None:
            writer.release()
        tmp.unlink(missing_ok=True)


# ---------------------------------------------------------------- device check before the exam

_check_lock = threading.Lock()
_check_analyzer = None


def check_frame(jpeg: bytes) -> dict[str, Any]:
    """Pre-exam check from one camera picture: is exactly one face visible, is it bright enough."""
    import numpy as np

    global _check_analyzer
    frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("Ảnh không đọc được.")
    with _check_lock:
        if _check_analyzer is None:
            from exam_monitor.engine import GazeAnalyzer

            _check_analyzer = GazeAnalyzer()
        result = _check_analyzer.analyze(frame)
    brightness = float(result.brightness)
    return {"face_count": int(result.face_count), "people": int(result.detected_people_count),
            "brightness": round(brightness, 1), "too_dark": brightness < 55,
            "ok": int(result.face_count) == 1 and brightness >= 55}


# ---------------------------------------------------------------- the server's instance

_service: Optional[OneAnalysisService] = None


def get_service() -> Optional[OneAnalysisService]:
    return _service


def start_service(session_factory: Callable[[], Session], on_events=None) -> Optional[OneAnalysisService]:
    """Start the workers unless VIGIL_ONE_ANALYSIS=0 (VIGIL_ONE_ANALYSIS_WORKERS, default 2)."""
    global _service
    if os.getenv("VIGIL_ONE_ANALYSIS", "1") == "0":
        return None
    if _service is None:
        _service = OneAnalysisService(session_factory, workers=int(os.getenv("VIGIL_ONE_ANALYSIS_WORKERS", "2")),
                                      on_events=on_events)
    _service.start()
    return _service


def stop_service() -> None:
    global _service
    if _service is not None:
        _service.stop()
        _service = None


def notify(session_id: str) -> None:
    if _service is not None:
        _service.notify(session_id)
