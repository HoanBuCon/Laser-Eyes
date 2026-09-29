"""Durable storage and review operations for human-created frame bookmarks."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import secrets
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from sqlalchemy.orm import Session

import cv2
import numpy as np

from proctor_support.contracts import EvidenceState, REVIEW_DECISIONS, ReviewDecision
from proctor_support.frame_store import CapturedFrame
from storage.db_models import AuditLog, ExamSession, ProctorBookmark, ProctorBookmarkReview, SeatROI


class BookmarkError(RuntimeError):
    pass


class BookmarkConflict(BookmarkError):
    pass


class BookmarkNotFound(BookmarkError):
    pass


def evidence_root() -> Path:
    return Path(os.getenv("VIGIL_PROCTOR_EVIDENCE_ROOT", "data/proctor_bookmarks")).resolve()


def _contained_snapshot(path_value: str | None) -> Optional[Path]:
    if not path_value:
        return None
    root = evidence_root()
    candidate = Path(path_value).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise BookmarkConflict("Bookmark snapshot is outside the configured evidence root") from exc
    return candidate


def serialize_bookmark(bookmark: ProctorBookmark, *, include_reviews: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        "bookmark_id": bookmark.id,
        "queue_id": f"MANUAL:{bookmark.id}",
        "source_type": "MANUAL_BOOKMARK",
        "request_id": bookmark.request_id,
        "session_id": bookmark.session_id,
        "run_id": bookmark.run_id,
        "source_product": bookmark.source_product,
        "source_kind": bookmark.source_kind,
        "source_ref": bookmark.source_ref,
        "frame_id": bookmark.frame_id,
        "source_timestamp_ms": bookmark.source_timestamp_ms,
        "session_elapsed_ms": bookmark.session_elapsed_ms,
        "captured_at": bookmark.captured_at.isoformat() if bookmark.captured_at else None,
        "subject_ref": bookmark.subject_ref,
        "created_by": bookmark.created_by,
        "note": bookmark.note or "",
        "review_decision": bookmark.review_decision,
        "review_status": bookmark.review_decision,
        "linked_ai_incident_id": bookmark.linked_ai_incident_id,
        "evidence_status": bookmark.evidence_status,
        "snapshot_url": f"/api/v1/proctor/bookmarks/{bookmark.id}/snapshot"
        if bookmark.evidence_status == EvidenceState.READY.value
        else None,
        "crop_url": f"/api/v1/proctor/bookmarks/{bookmark.id}/snapshot?view=crop"
        if bookmark.evidence_status == EvidenceState.READY.value and bookmark.roi_snapshot_json
        else None,
        "sha256": bookmark.sha256,
        "file_size_bytes": bookmark.file_size_bytes or 0,
        "error_message": bookmark.error_message,
        "coordinate_space": json.loads(bookmark.coordinate_space_json)
        if bookmark.coordinate_space_json
        else None,
        "roi_snapshot": json.loads(bookmark.roi_snapshot_json) if bookmark.roi_snapshot_json else None,
        "simulation": bool(bookmark.simulation),
        "created_at": bookmark.created_at.isoformat() if bookmark.created_at else None,
        "updated_at": bookmark.updated_at.isoformat() if bookmark.updated_at else None,
    }
    if include_reviews:
        result["reviews"] = [
            {
                "review_id": review.id,
                "reviewer_id": review.reviewer_id,
                "previous_decision": review.previous_decision,
                "previous_note": review.previous_note or "",
                "decision": review.decision,
                "reason_code": review.reason_code,
                "note": review.note or "",
                "reviewed_at": review.reviewed_at.isoformat() if review.reviewed_at else None,
            }
            for review in sorted(bookmark.reviews, key=lambda item: item.reviewed_at)
        ]
    return result


def create_bookmark(
    db: Session,
    *,
    captured: CapturedFrame,
    request_id: str,
    subject_ref: Optional[str],
    note: str,
    created_by: str,
    linked_ai_incident_id: Optional[str] = None,
    roi_snapshot: Optional[dict[str, Any]] = None,
    simulation: bool = False,
) -> tuple[ProctorBookmark, bool]:
    existing = db.query(ProctorBookmark).filter(ProctorBookmark.request_id == request_id).first()
    if existing is not None:
        return existing, False
    session_row = db.get(ExamSession, captured.session_id)
    if session_row is None:
        raise BookmarkConflict("The captured frame does not belong to a durable exam session")
    if roi_snapshot is None and subject_ref:
        seat = (
            db.query(SeatROI)
            .filter(SeatROI.room_id == session_row.room_id, SeatROI.seat_code == subject_ref)
            .first()
        )
        if seat is not None:
            try:
                polygon = json.loads(seat.polygon_json)
                roi_snapshot = {"seat_id": seat.id, "seat_code": seat.seat_code, "polygon": polygon}
            except (TypeError, json.JSONDecodeError):
                roi_snapshot = None

    bookmark = ProctorBookmark(
        request_id=request_id,
        session_id=captured.session_id,
        run_id=captured.run_id,
        source_product=captured.source_product,
        source_kind=captured.source_kind,
        source_ref=captured.source_ref,
        frame_id=captured.frame_id,
        source_timestamp_ms=captured.source_timestamp_ms,
        session_elapsed_ms=captured.source_timestamp_ms,
        captured_at=datetime.utcfromtimestamp(captured.captured_at_epoch),
        subject_ref=(subject_ref or None),
        created_by=created_by,
        note=note or "",
        linked_ai_incident_id=linked_ai_incident_id,
        evidence_status=EvidenceState.PENDING.value,
        coordinate_space_json=json.dumps(captured.coordinate_space, ensure_ascii=False),
        roi_snapshot_json=json.dumps(roi_snapshot, ensure_ascii=False) if roi_snapshot else None,
        simulation=bool(simulation),
    )
    db.add(bookmark)
    db.commit()
    db.refresh(bookmark)

    root = evidence_root()
    session_dir = root / captured.session_id
    final_path = session_dir / f"{bookmark.id}.jpg"
    temp_path = session_dir / f".{bookmark.id}.{uuid.uuid4().hex}.tmp"
    try:
        session_dir.mkdir(parents=True, exist_ok=True)
        temp_path.write_bytes(captured.jpeg_bytes)
        if temp_path.stat().st_size <= 0:
            raise OSError("Captured snapshot is empty")
        os.replace(temp_path, final_path)
        digest = hashlib.sha256(captured.jpeg_bytes).hexdigest()
        bookmark.snapshot_path = str(final_path)
        bookmark.sha256 = digest
        bookmark.file_size_bytes = len(captured.jpeg_bytes)
        bookmark.evidence_status = EvidenceState.READY.value
        bookmark.error_message = None
        db.add(
            AuditLog(
                actor_id=created_by,
                action="CREATE_PROCTOR_BOOKMARK",
                resource_type="PROCTOR_BOOKMARK",
                resource_id=bookmark.id,
                metadata_json=json.dumps(
                    {"session_id": captured.session_id, "frame_id": captured.frame_id},
                    ensure_ascii=False,
                ),
            )
        )
        db.commit()
        db.refresh(bookmark)
        return bookmark, True
    except Exception as exc:
        if temp_path.exists():
            temp_path.unlink()
        bookmark.evidence_status = EvidenceState.FAILED.value
        bookmark.error_message = str(exc)
        db.add(
            AuditLog(
                actor_id=created_by,
                action="CREATE_PROCTOR_BOOKMARK_FAILED",
                resource_type="PROCTOR_BOOKMARK",
                resource_id=bookmark.id,
                metadata_json=json.dumps({"error": str(exc)}, ensure_ascii=False),
            )
        )
        db.commit()
        db.refresh(bookmark)
        return bookmark, True


def get_bookmark(db: Session, bookmark_id: str) -> ProctorBookmark:
    bookmark = db.get(ProctorBookmark, bookmark_id)
    if bookmark is None:
        raise BookmarkNotFound(f"Bookmark '{bookmark_id}' was not found")
    return bookmark


def get_bookmark_by_request_id(db: Session, request_id: str) -> Optional[ProctorBookmark]:
    return db.query(ProctorBookmark).filter(ProctorBookmark.request_id == request_id).first()


def bookmark_snapshot_path(bookmark: ProctorBookmark) -> Path:
    if bookmark.evidence_status != EvidenceState.READY.value:
        raise BookmarkConflict(f"Bookmark evidence is {bookmark.evidence_status}")
    path = _contained_snapshot(bookmark.snapshot_path)
    if path is None or not path.is_file() or path.stat().st_size <= 0:
        raise BookmarkConflict("Bookmark snapshot is unavailable")
    return path


def bookmark_crop_bytes(bookmark: ProctorBookmark) -> bytes:
    path = bookmark_snapshot_path(bookmark)
    if not bookmark.roi_snapshot_json:
        raise BookmarkConflict("No seat ROI snapshot is attached to this bookmark")
    try:
        roi = json.loads(bookmark.roi_snapshot_json)
        points = np.asarray(roi.get("polygon") or [], dtype=np.float32)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BookmarkConflict("Seat ROI snapshot is malformed") from exc
    if points.ndim != 2 or points.shape[0] < 3 or points.shape[1] != 2:
        raise BookmarkConflict("Seat ROI snapshot has fewer than three valid points")
    image = cv2.imread(str(path))
    if image is None:
        raise BookmarkConflict("Bookmark snapshot cannot be decoded")
    height, width = image.shape[:2]
    if float(np.max(points)) <= 1.5:
        points[:, 0] *= width
        points[:, 1] *= height
    points[:, 0] = np.clip(points[:, 0], 0, width - 1)
    points[:, 1] = np.clip(points[:, 1], 0, height - 1)
    x, y, crop_width, crop_height = cv2.boundingRect(points.astype(np.int32))
    crop = image[y : y + crop_height, x : x + crop_width]
    if crop.size == 0:
        raise BookmarkConflict("Seat ROI crop is empty")
    ok, encoded = cv2.imencode(".jpg", crop, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    if not ok:
        raise BookmarkConflict("Seat ROI crop cannot be encoded")
    return encoded.tobytes()


def verify_bookmark_integrity(bookmark: ProctorBookmark) -> dict[str, Any]:
    if not bookmark.sha256:
        return {"hash_status": "HASH_NOT_AVAILABLE", "sha256": None}
    try:
        path = bookmark_snapshot_path(bookmark)
    except BookmarkConflict as exc:
        return {"hash_status": "HASH_MISMATCH", "sha256": bookmark.sha256, "error": str(exc)}
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "hash_status": "HASH_VERIFIED"
        if secrets.compare_digest(actual.lower(), bookmark.sha256.lower())
        else "HASH_MISMATCH",
        "sha256": bookmark.sha256,
    }


def list_bookmarks(
    db: Session,
    *,
    session_id: Optional[str] = None,
    decision: Optional[str] = None,
    limit: int = 200,
) -> list[ProctorBookmark]:
    query = db.query(ProctorBookmark)
    if session_id:
        query = query.filter(ProctorBookmark.session_id == session_id)
    if decision:
        query = query.filter(ProctorBookmark.review_decision == decision)
    return query.order_by(ProctorBookmark.captured_at.desc()).limit(max(1, min(limit, 1000))).all()


def review_bookmark(
    db: Session,
    *,
    bookmark_id: str,
    reviewer_id: str,
    decision: str,
    reason_code: Optional[str],
    note: str,
) -> ProctorBookmark:
    normalized = decision.upper().strip()
    if normalized not in REVIEW_DECISIONS or normalized == ReviewDecision.PENDING.value:
        raise BookmarkConflict("Decision must be CONFIRMED, REJECTED, or INCONCLUSIVE")
    bookmark = get_bookmark(db, bookmark_id)
    previous = bookmark.review_decision
    review = ProctorBookmarkReview(
        bookmark_id=bookmark.id,
        reviewer_id=reviewer_id,
        previous_decision=previous,
        previous_note=bookmark.note or "",
        decision=normalized,
        reason_code=reason_code,
        note=note or "",
    )
    bookmark.review_decision = normalized
    if note:
        bookmark.note = note
    db.add(review)
    db.add(
        AuditLog(
            actor_id=reviewer_id,
            action="REVIEW_PROCTOR_BOOKMARK",
            resource_type="PROCTOR_BOOKMARK",
            resource_id=bookmark.id,
            metadata_json=json.dumps(
                {"previous_decision": previous, "decision": normalized, "reason_code": reason_code},
                ensure_ascii=False,
            ),
        )
    )
    db.commit()
    db.refresh(bookmark)
    return bookmark


def bookmarks_to_csv(bookmarks: Iterable[ProctorBookmark]) -> str:
    output = io.StringIO(newline="")
    fields = [
        "bookmark_id",
        "session_id",
        "run_id",
        "frame_id",
        "source_timestamp_ms",
        "captured_at",
        "subject_ref",
        "created_by",
        "note",
        "review_decision",
        "evidence_status",
        "sha256",
    ]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for item in bookmarks:
        row = serialize_bookmark(item)
        writer.writerow({field: row.get(field) for field in fields})
    return output.getvalue()
