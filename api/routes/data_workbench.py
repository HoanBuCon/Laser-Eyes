"""FastAPI Router for VIGIL AI Human Data Operations Workbench.

Endpoints for:
1. Overview & Summary metrics
2. Image Dataset Audit & Rapid Review + Dynamic Actor Crop
3. Video Temporal Episode Annotation & AI vs Human Comparison
4. Spatial Calibration & Geometry Quality Validation
5. AI Event Review Queue & Triage
6. Versioned ML Dataset Curation & Export
7. Staged Exam Recording Sessions & Protocol Checklist
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from storage.data_workbench_service import (
    OBSERVABLE_LABEL_MAP,
    STANDARDIZED_OBSERVABLE_LABELS,
    YOLO_CLASS_NAMES,
    DataWorkbenchService,
)
from storage.database import get_db
from storage.db_models import (
    AuditLog,
    DetectionEvent,
    ExamRoom,
    ImageAnnotationRevision,
    MediaAsset,
    SeatROI,
    TemporalEpisodeAnnotation,
)
from storage.repositories import (
    AuditLogRepository,
    DatasetRepository,
    EventRepository,
    ImageRevisionRepository,
    MediaAssetRepository,
    ReviewRepository,
    SeatRepository,
    StagedSessionRepository,
    TemporalEpisodeRepository,
)

logger = logging.getLogger("DataWorkbenchRouter")

router = APIRouter(prefix="/data", tags=["Human Data Operations Workbench"])


# ==============================================================================
# PYDANTIC SCHEMAS
# ==============================================================================

class BboxRevisionItem(BaseModel):
    bbox_index: int = 0
    original_class: str = "no cheating"
    reviewed_class: str = "NORMAL_WRITING"
    bbox: List[float] = Field(..., description="[x_center, y_center, width, height] normalized")
    is_ambiguous: bool = False
    is_rejected: bool = False
    posture_tags: Optional[List[str]] = None
    audit_notes: Optional[str] = None


class ImageReviewRequest(BaseModel):
    revisions: List[BboxRevisionItem]
    overall_status: str = "AUDITED"  # "AUDITED", "FLAGGED", "REJECTED"
    reviewer_id: str = "annotator"
    notes: Optional[str] = None


class EpisodeCreateRequest(BaseModel):
    episode_type: str = Field(..., description="e.g. HEAD_TURN_LEFT, TORSO_LEAN_RIGHT")
    start_ms: float = Field(..., ge=0.0)
    peak_ms: float = Field(..., ge=0.0)
    end_ms: float = Field(..., ge=0.0)
    seat_id: Optional[str] = None
    seat_code: Optional[str] = None
    target_neighbor_id: Optional[str] = None
    confidence: float = 1.0
    reviewer_id: str = "annotator"
    notes: Optional[str] = None


class EpisodeUpdateRequest(BaseModel):
    episode_type: Optional[str] = None
    start_ms: Optional[float] = None
    peak_ms: Optional[float] = None
    end_ms: Optional[float] = None
    seat_code: Optional[str] = None
    target_neighbor_id: Optional[str] = None
    confidence: Optional[float] = None
    review_status: Optional[str] = None
    notes: Optional[str] = None


class SpatialCalibrationValidateRequest(BaseModel):
    room_id: Optional[str] = None
    seats: Optional[List[Dict[str, Any]]] = None


class EventReviewRequest(BaseModel):
    decision: str = Field(..., description="CONFIRMED, REJECTED, INCONCLUSIVE")
    reason_code: Optional[str] = Field("TRUE_SUSPICIOUS", description="Reason code")
    note: str = ""
    reviewer_id: str = "proctor_lead"


class DatasetExportRequest(BaseModel):
    collection_name: str = "VIGIL_707_ACTOR_CROPS"
    version_tag: str = "v1.0.0"
    task_type: str = "ACTOR_CLASSIFICATION"
    export_format: str = "CLASSIFICATION_CROPS"  # "CLASSIFICATION_CROPS", "EPISODE_JSON"
    train_ratio: float = 0.70
    val_ratio: float = 0.15
    test_ratio: float = 0.15
    target_crop_size: int = 224


class StagedSessionCreateRequest(BaseModel):
    session_code: str
    script_name: str
    room_id: Optional[str] = None
    actor_names: Optional[List[str]] = None
    target_video_path: Optional[str] = None
    notes: Optional[str] = None


class StagedScenarioCreateRequest(BaseModel):
    scenario_code: str
    title: str
    expected_behavior: str
    seat_code: Optional[str] = None
    target_start_ms: float = 0.0
    target_end_ms: float = 0.0
    notes: Optional[str] = None


class StagedScenarioUpdateRequest(BaseModel):
    status: Optional[str] = None  # "PASS", "FAIL", "RE_RECORD"
    actual_start_ms: Optional[float] = None
    actual_end_ms: Optional[float] = None
    notes: Optional[str] = None


# ==============================================================================
# 1. SUMMARY & INDEXING ENDPOINTS
# ==============================================================================

@router.get("/summary", summary="Get overall data operations summary metrics")
def get_workbench_summary(db: Session = Depends(get_db)):
    """Return comprehensive summary of asset audit, video annotations, reviews, and exported datasets."""
    service = DataWorkbenchService(db)
    summary = service.asset_repo.get_summary()

    # Add available standardized labels and YOLO mapping
    summary["standardized_observable_labels"] = STANDARDIZED_OBSERVABLE_LABELS
    summary["yolo_class_names"] = YOLO_CLASS_NAMES
    summary["observable_label_map"] = OBSERVABLE_LABEL_MAP

    # Add pending event count
    event_repo = EventRepository(db)
    pending_events = event_repo.list_all_filtered(review_status="PENDING", limit=1000)
    summary["pending_events_count"] = len(pending_events)

    return summary


@router.post("/index-assets", summary="Index images from dataset/ and videos from demo_video/")
def trigger_index_assets(db: Session = Depends(get_db)):
    """Scan and index all local raw images and demo videos non-destructively."""
    service = DataWorkbenchService(db)
    img_res = service.index_image_dataset("dataset")
    vid_res = service.index_video_assets(["demo_video", "data/output_demo_v2"])
    ep_res = service.seed_baseline_video_episodes()
    stg_res = service.seed_staged_recording_protocol()

    return {
        "status": "success",
        "image_indexing": img_res,
        "video_indexing": vid_res,
        "video_episodes_seeding": ep_res,
        "staged_protocol_seeding": stg_res,
    }


# ==============================================================================
# 2. IMAGE AUDIT & RAPID REVIEW ENDPOINTS
# ==============================================================================

@router.get("/images", summary="List image assets with audit status and split filters")
def list_images(
    split: Optional[str] = Query(None, description="train, val, test"),
    audit_status: Optional[str] = Query(None, description="UNAUDITED, AUDITED, FLAGGED, REJECTED"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """List paginated image assets with their raw and reviewed annotations."""
    service = DataWorkbenchService(db)
    # Ensure indexed
    if service.asset_repo.count(asset_type="IMAGE") == 0:
        service.index_image_dataset("dataset")

    assets = service.asset_repo.list_all(
        asset_type="IMAGE",
        original_split=split,
        audit_status=audit_status,
        limit=limit,
        offset=offset,
    )
    total_count = service.asset_repo.count(
        asset_type="IMAGE",
        original_split=split,
        audit_status=audit_status,
    )

    items = []
    for a in assets:
        meta = json.loads(a.metadata_json) if a.metadata_json else {}
        revisions = service.revision_repo.list_by_asset(a.id)

        items.append({
            "id": a.id,
            "file_name": a.file_name,
            "relative_path": a.relative_path,
            "split": a.original_split,
            "width": a.width,
            "height": a.height,
            "annotations_count": a.annotations_count,
            "audit_status": a.audit_status,
            "raw_bboxes": meta.get("raw_bboxes", []),
            "revisions_count": len(revisions),
            "created_at": a.created_at.isoformat() if a.created_at else None,
        })

    return {
        "total": total_count,
        "limit": limit,
        "offset": offset,
        "items": items,
    }


@router.get("/images/{asset_id}", summary="Get detailed image asset with bounding boxes and revisions")
def get_image_detail(asset_id: str, db: Session = Depends(get_db)):
    """Retrieve full image detail including raw YOLO labels and human reviewed revisions."""
    service = DataWorkbenchService(db)
    asset = service.asset_repo.get_by_id(asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Image asset not found.")

    meta = json.loads(asset.metadata_json) if asset.metadata_json else {}
    revisions = service.revision_repo.list_by_asset(asset.id)

    rev_list = []
    for r in revisions:
        rev_list.append({
            "id": r.id,
            "bbox_index": r.bbox_index,
            "original_class": r.original_class,
            "reviewed_class": r.reviewed_class,
            "bbox": json.loads(r.bbox_json) if r.bbox_json else [],
            "is_ambiguous": r.is_ambiguous,
            "is_rejected": r.is_rejected,
            "posture_tags": json.loads(r.posture_tags_json) if r.posture_tags_json else [],
            "audit_notes": r.audit_notes,
            "reviewer_id": r.reviewer_id,
            "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
        })

    return {
        "id": asset.id,
        "file_name": asset.file_name,
        "relative_path": asset.relative_path,
        "split": asset.original_split,
        "width": asset.width,
        "height": asset.height,
        "audit_status": asset.audit_status,
        "raw_bboxes": meta.get("raw_bboxes", []),
        "revisions": rev_list,
        "created_at": asset.created_at.isoformat() if asset.created_at else None,
    }


@router.post("/images/{asset_id}/review", summary="Submit non-destructive human review for an image asset")
def submit_image_review(asset_id: str, payload: ImageReviewRequest, db: Session = Depends(get_db)):
    """Save non-destructive bounding box revisions and update asset audit status."""
    service = DataWorkbenchService(db)
    asset = service.asset_repo.get_by_id(asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Image asset not found.")

    saved_revisions = []
    for item in payload.revisions:
        rev = service.revision_repo.save_revision(
            asset_id=asset.id,
            bbox_index=item.bbox_index,
            original_class=item.original_class,
            reviewed_class=item.reviewed_class,
            bbox_json=item.bbox,
            is_ambiguous=item.is_ambiguous,
            is_rejected=item.is_rejected,
            posture_tags_json=item.posture_tags,
            audit_notes=item.audit_notes,
            reviewer_id=payload.reviewer_id,
        )
        saved_revisions.append(rev.id)

    # Update overall audit status
    service.asset_repo.update_audit_status(asset.id, payload.overall_status)

    # Log action
    audit_log = AuditLogRepository(db)
    audit_log.log_action(
        actor_id=payload.reviewer_id,
        action="REVIEW_IMAGE_ASSET",
        resource_type="IMAGE_ASSET",
        resource_id=asset.id,
        metadata={"revisions_count": len(saved_revisions), "overall_status": payload.overall_status},
    )

    return {
        "status": "success",
        "asset_id": asset.id,
        "audit_status": payload.overall_status,
        "saved_revisions_count": len(saved_revisions),
    }


@router.get("/images/{asset_id}/file", summary="Stream raw image file")
def get_image_file(asset_id: str, db: Session = Depends(get_db)):
    """Stream raw JPEG image file directly."""
    service = DataWorkbenchService(db)
    asset = service.asset_repo.get_by_id(asset_id)
    if not asset or not os.path.exists(asset.file_path):
        raise HTTPException(status_code=404, detail="Image file not found on disk.")

    return FileResponse(asset.file_path, media_type="image/jpeg")


@router.get("/images/{asset_id}/crop", summary="Dynamic Actor Crop on Bounding Box")
def get_dynamic_actor_crop(
    asset_id: str,
    bbox_index: int = Query(0, ge=0),
    padding_ratio: float = Query(0.10, ge=0.0, le=0.5),
    target_size: Optional[int] = Query(None, ge=32, le=1024),
    db: Session = Depends(get_db),
):
    """Dynamically crop and return actor ROI JPEG on-the-fly."""
    service = DataWorkbenchService(db)
    crop_bytes = service.get_actor_crop(
        asset_id=asset_id,
        bbox_index=bbox_index,
        padding_ratio=padding_ratio,
        target_size=target_size,
    )
    if not crop_bytes:
        raise HTTPException(status_code=404, detail="Could not generate actor crop.")

    return Response(content=crop_bytes, media_type="image/jpeg")


# ==============================================================================
# 3. VIDEO TEMPORAL EPISODE & AI COMPARISON ENDPOINTS
# ==============================================================================

@router.get("/videos", summary="List video assets available for temporal annotation")
def list_videos(db: Session = Depends(get_db)):
    """List indexed video files with duration and episode counts."""
    service = DataWorkbenchService(db)
    # Ensure indexed
    if service.asset_repo.count(asset_type="VIDEO") == 0:
        service.index_video_assets(["demo_video", "data/output_demo_v2"])

    videos = service.asset_repo.list_all(asset_type="VIDEO")
    res = []
    for v in videos:
        eps = service.episode_repo.list_by_asset(v.id)
        human_eps = [e for e in eps if not e.is_ai_proposal]
        ai_eps = [e for e in eps if e.is_ai_proposal]

        res.append({
            "id": v.id,
            "file_name": v.file_name,
            "relative_path": v.relative_path,
            "width": v.width,
            "height": v.height,
            "fps": v.fps,
            "total_frames": v.total_frames,
            "duration_seconds": round(v.duration_seconds, 2),
            "sha256_hash": v.sha256_hash,
            "total_episodes": len(eps),
            "human_episodes_count": len(human_eps),
            "ai_proposals_count": len(ai_eps),
        })

    return {"total": len(res), "items": res}


@router.get("/videos/{asset_id}/file", summary="Stream video asset file")
def get_video_file(asset_id: str, db: Session = Depends(get_db)):
    """Stream video file for timeline player scrubber."""
    service = DataWorkbenchService(db)
    asset = service.asset_repo.get_by_id(asset_id)
    if not asset or not os.path.exists(asset.file_path):
        raise HTTPException(status_code=404, detail="Video file not found on disk.")

    return FileResponse(asset.file_path, media_type="video/mp4")


@router.get("/videos/{asset_id}/episodes", summary="List ground-truth and AI proposal episodes for a video")
def list_video_episodes(
    asset_id: str,
    seat_id: Optional[str] = Query(None),
    is_ai_proposal: Optional[bool] = Query(None),
    db: Session = Depends(get_db),
):
    """List all temporal episodes annotated for a given video."""
    service = DataWorkbenchService(db)
    episodes = service.episode_repo.list_by_asset(
        asset_id=asset_id,
        seat_id=seat_id,
        is_ai_proposal=is_ai_proposal,
    )

    items = []
    for ep in episodes:
        items.append({
            "id": ep.id,
            "asset_id": ep.asset_id,
            "seat_id": ep.seat_id,
            "seat_code": ep.seat_code,
            "episode_type": ep.episode_type,
            "start_ms": ep.start_ms,
            "peak_ms": ep.peak_ms,
            "end_ms": ep.end_ms,
            "duration_ms": ep.duration_ms,
            "target_neighbor_id": ep.target_neighbor_id,
            "confidence": ep.confidence,
            "is_ai_proposal": ep.is_ai_proposal,
            "ai_match_iou": ep.ai_match_iou,
            "reviewer_id": ep.reviewer_id,
            "review_status": ep.review_status,
            "notes": ep.notes,
            "created_at": ep.created_at.isoformat() if ep.created_at else None,
        })

    return {"asset_id": asset_id, "total": len(items), "episodes": items}


@router.post("/videos/{asset_id}/episodes", summary="Create human ground-truth temporal episode")
def create_video_episode(asset_id: str, payload: EpisodeCreateRequest, db: Session = Depends(get_db)):
    """Create a new human ground-truth temporal episode with millisecond precision."""
    service = DataWorkbenchService(db)
    asset = service.asset_repo.get_by_id(asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Video asset not found.")

    if payload.end_ms <= payload.start_ms:
        raise HTTPException(status_code=400, detail="end_ms must be strictly greater than start_ms.")

    ep = service.episode_repo.create(
        asset_id=asset.id,
        episode_type=payload.episode_type,
        start_ms=payload.start_ms,
        peak_ms=payload.peak_ms,
        end_ms=payload.end_ms,
        seat_id=payload.seat_id,
        seat_code=payload.seat_code,
        target_neighbor_id=payload.target_neighbor_id,
        confidence=payload.confidence,
        is_ai_proposal=False,
        reviewer_id=payload.reviewer_id,
        review_status="ACCEPTED",
        notes=payload.notes,
    )

    return {
        "status": "created",
        "episode_id": ep.id,
        "duration_ms": ep.duration_ms,
        "episode_type": ep.episode_type,
    }


@router.put("/videos/{asset_id}/episodes/{episode_id}", summary="Update temporal episode")
def update_video_episode(
    asset_id: str,
    episode_id: str,
    payload: EpisodeUpdateRequest,
    db: Session = Depends(get_db),
):
    """Update an existing temporal episode annotation."""
    service = DataWorkbenchService(db)
    ep = service.episode_repo.get_by_id(episode_id)
    if not ep or ep.asset_id != asset_id:
        raise HTTPException(status_code=404, detail="Episode annotation not found.")

    update_dict = payload.model_dump(exclude_unset=True)
    updated_ep = service.episode_repo.update(episode_id, **update_dict)

    return {"status": "updated", "episode_id": updated_ep.id, "duration_ms": updated_ep.duration_ms}


@router.delete("/videos/{asset_id}/episodes/{episode_id}", summary="Delete temporal episode")
def delete_video_episode(asset_id: str, episode_id: str, db: Session = Depends(get_db)):
    """Delete an existing temporal episode annotation."""
    service = DataWorkbenchService(db)
    success = service.episode_repo.delete(episode_id)
    if not success:
        raise HTTPException(status_code=404, detail="Episode not found.")
    return {"status": "deleted", "episode_id": episode_id}


@router.get("/videos/{asset_id}/compare", summary="Compare Human vs AI Temporal Episodes with IoU")
def compare_video_episodes(
    asset_id: str,
    iou_threshold: float = Query(0.30, ge=0.05, le=0.95),
    db: Session = Depends(get_db),
):
    """Compute temporal IoU matching, precision, recall, F1, and paired timeline diff between Human and AI."""
    service = DataWorkbenchService(db)
    asset = service.asset_repo.get_by_id(asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Video asset not found.")

    # Seed proposals if empty
    service.seed_baseline_video_episodes()

    comparison = service.episode_repo.compare_human_vs_ai(asset.id, iou_threshold=iou_threshold)
    return comparison


# ==============================================================================
# 4. SPATIAL CALIBRATION & GEOMETRY QUALITY VALIDATION ENDPOINTS
# ==============================================================================

@router.get("/seats", summary="List active Seat ROIs for video overlay and selection")
def list_seats_for_workbench(
    room_id: Optional[str] = Query(None),
    enabled_only: bool = Query(False),
    db: Session = Depends(get_db),
):
    """Retrieve seat polygons and labels for video overlay visualization."""
    query = db.query(SeatROI)
    if enabled_only:
        query = query.filter(SeatROI.enabled == True)
    if room_id:
        query = query.filter(SeatROI.room_id == room_id)
    seats = query.order_by(SeatROI.seat_code.asc()).all()

    items = []
    for s in seats:
        poly = json.loads(s.polygon_json) if isinstance(s.polygon_json, str) else (s.polygon_json or [])
        items.append({
            "id": s.id,
            "room_id": s.room_id,
            "camera_id": s.camera_id,
            "seat_code": s.seat_code,
            "seat_label": s.seat_label,
            "polygon": poly,
            "enabled": s.enabled,
        })
    return {"total": len(items), "room_id": room_id, "seats": items}


@router.delete("/seats/{seat_id}", summary="Delete Seat ROI from database")
def delete_seat_from_workbench(seat_id: str, db: Session = Depends(get_db)):
    """Delete a configured Seat ROI by ID or seat_code."""
    seat = db.query(SeatROI).filter(SeatROI.id == seat_id).first()
    if not seat:
        seat = db.query(SeatROI).filter(SeatROI.seat_code == seat_id).first()
    if not seat:
        raise HTTPException(status_code=404, detail="Seat ROI not found.")

    deleted_code = seat.seat_code
    db.delete(seat)
    db.commit()
    return {"status": "deleted", "seat_id": seat_id, "seat_code": deleted_code}


@router.delete("/seats/by-code/{seat_code}", summary="Delete Seat ROI by seat code")
def delete_seat_by_code_from_workbench(seat_code: str, db: Session = Depends(get_db)):
    """Delete all Seat ROIs matching a given seat code."""
    seats = db.query(SeatROI).filter(SeatROI.seat_code == seat_code).all()
    if not seats:
        raise HTTPException(status_code=404, detail=f"No Seat ROI found with code {seat_code}")

    del_count = len(seats)
    for s in seats:
        db.delete(s)
    db.commit()
    return {"status": "deleted", "seat_code": seat_code, "deleted_count": del_count}


@router.post("/calibration/validate", summary="Automated geometric quality check for seat polygons and writing zones")
def validate_calibration_geometry(
    payload: SpatialCalibrationValidateRequest,
    db: Session = Depends(get_db),
):
    """Run automated polygon convexity, boundary, area, and pairwise overlap checks."""
    service = DataWorkbenchService(db)

    seats_to_check = []
    if payload.seats:
        seats_to_check = payload.seats
    elif payload.room_id:
        db_seats = service.seat_repo.list_by_room(payload.room_id)
        for s in db_seats:
            seats_to_check.append({
                "seat_code": s.seat_code,
                "seat_label": s.seat_label,
                "polygon_json": s.polygon_json,
                "enabled": s.enabled,
            })
    else:
        # Check all seats in database
        db_seats = db.query(SeatROI).all()
        for s in db_seats:
            seats_to_check.append({
                "seat_code": s.seat_code,
                "seat_label": s.seat_label,
                "polygon_json": s.polygon_json,
                "enabled": s.enabled,
            })

    report = service.validate_spatial_calibration(seats_to_check)
    return report


# ==============================================================================
# 5. AI EVENT REVIEW QUEUE ENDPOINTS
# ==============================================================================

@router.get("/events/review-queue", summary="List pending AI events for human review triage")
def get_event_review_queue(
    status_filter: Optional[str] = Query("PENDING"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Retrieve prioritized queue of AI detection events for review triage."""
    event_repo = EventRepository(db)
    events = event_repo.list_all_filtered(review_status=status_filter, limit=limit)

    items = []
    for ev in events:
        evidence = ev.evidence
        items.append({
            "id": ev.id,
            "event_id": ev.event_id,
            "session_id": ev.session_id,
            "seat_code": ev.seat.seat_code if ev.seat else f"Track-{ev.track_id}",
            "primary_signal": ev.primary_signal or ev.behavior,
            "primary_pattern": ev.primary_pattern,
            "severity": ev.severity,
            "risk_score": ev.risk_score,
            "confidence_peak": ev.confidence_peak,
            "duration_seconds": ev.duration_seconds,
            "review_status": ev.review_status,
            "reviewer_note": ev.reviewer_note,
            "evidence_snapshot": evidence.snapshot_path if evidence else None,
            "evidence_video": evidence.video_path if evidence else None,
            "video_sha256": evidence.video_sha256 if evidence else None,
            "created_at": ev.created_at.isoformat() if ev.created_at else None,
        })

    return {"total": len(items), "items": items}


@router.post("/events/{event_id}/review", summary="Submit Human-in-the-Loop decision for an AI event")
def review_ai_event(
    event_id: str,
    payload: EventReviewRequest,
    db: Session = Depends(get_db),
):
    """Record human proctor triage decision (CONFIRMED, REJECTED, INCONCLUSIVE) with reason code."""
    event_repo = EventRepository(db)
    review_repo = ReviewRepository(db)
    audit_repo = AuditLogRepository(db)

    ev = event_repo.get_by_id(event_id) or event_repo.get_by_event_id(event_id)
    if not ev:
        raise HTTPException(status_code=404, detail="Detection event not found.")

    review = review_repo.submit_review(
        event_pk=ev.id,
        reviewer_id=payload.reviewer_id,
        decision=payload.decision.upper(),
        reason_code=payload.reason_code,
        note=payload.note,
    )

    audit_repo.log_action(
        actor_id=payload.reviewer_id,
        action="REVIEW_AI_EVENT",
        resource_type="DETECTION_EVENT",
        resource_id=ev.id,
        metadata={"decision": payload.decision, "reason_code": payload.reason_code, "note": payload.note},
    )

    return {
        "status": "success",
        "event_id": ev.event_id,
        "decision": review.decision,
        "reason_code": review.reason_code,
        "reviewed_at": review.reviewed_at.isoformat(),
    }


# ==============================================================================
# 6. DATASET CURATION & EXPORT ENDPOINTS
# ==============================================================================

@router.get("/datasets", summary="List dataset collections and released versions")
def list_datasets(db: Session = Depends(get_db)):
    """List all curated dataset collections and their generated versions."""
    dataset_repo = DatasetRepository(db)
    collections = dataset_repo.list_collections()

    items = []
    for c in collections:
        versions = dataset_repo.list_versions(collection_id=c.id)
        ver_list = []
        for v in versions:
            ver_list.append({
                "id": v.id,
                "version_tag": v.version_tag,
                "split_strategy": v.split_strategy,
                "export_format": v.export_format,
                "export_path": v.export_path,
                "total_items": v.total_items,
                "status": v.status,
                "manifest": json.loads(v.manifest_json) if v.manifest_json else None,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            })
        items.append({
            "id": c.id,
            "name": c.name,
            "task_type": c.task_type,
            "description": c.description,
            "created_by": c.created_by,
            "versions_count": len(ver_list),
            "versions": ver_list,
        })

    return {"total": len(items), "collections": items}


@router.post("/datasets/export", summary="Trigger versioned dataset export with group-safe splits & manifest")
def export_dataset_version(
    payload: DatasetExportRequest,
    db: Session = Depends(get_db),
):
    """Package reviewed data into ML-ready directory structure with manifest.json."""
    service = DataWorkbenchService(db)

    # Validate split ratios
    total_ratio = round(payload.train_ratio + payload.val_ratio + payload.test_ratio, 2)
    if total_ratio != 1.0:
        raise HTTPException(
            status_code=400,
            detail=f"Split ratios must sum to 1.0 (got {total_ratio})",
        )

    export_result = service.export_curated_dataset(
        collection_name=payload.collection_name,
        version_tag=payload.version_tag,
        task_type=payload.task_type,
        export_format=payload.export_format,
        train_ratio=payload.train_ratio,
        val_ratio=payload.val_ratio,
        test_ratio=payload.test_ratio,
        target_crop_size=payload.target_crop_size,
    )

    return export_result


# ==============================================================================
# 7. STAGED EXAM RECORDING SESSIONS & PROTOCOL CHECKLIST ENDPOINTS
# ==============================================================================

@router.get("/staged-sessions", summary="List staged exam recording sessions and scenario checklists")
def list_staged_sessions(db: Session = Depends(get_db)):
    """List structured mock/staged recording sessions with scenario checklist items."""
    service = DataWorkbenchService(db)
    service.seed_staged_recording_protocol()

    sessions = service.staged_repo.list_sessions()
    items = []
    for s in sessions:
        scenarios = service.staged_repo.list_scenarios_by_session(s.id)
        scen_list = []
        for sc in scenarios:
            scen_list.append({
                "id": sc.id,
                "scenario_code": sc.scenario_code,
                "title": sc.title,
                "expected_behavior": sc.expected_behavior,
                "seat_code": sc.seat_code,
                "target_start_ms": sc.target_start_ms,
                "target_end_ms": sc.target_end_ms,
                "actual_start_ms": sc.actual_start_ms,
                "actual_end_ms": sc.actual_end_ms,
                "status": sc.status,
                "notes": sc.notes,
            })
        items.append({
            "id": s.id,
            "session_code": s.session_code,
            "script_name": s.script_name,
            "actors": json.loads(s.actor_names_json) if s.actor_names_json else [],
            "target_video_path": s.target_video_path,
            "status": s.status,
            "notes": s.notes,
            "scenarios_count": len(scen_list),
            "scenarios": scen_list,
        })

    return {"total": len(items), "sessions": items}


@router.post("/staged-sessions", summary="Create a new staged recording session")
def create_staged_session(payload: StagedSessionCreateRequest, db: Session = Depends(get_db)):
    """Create a new staged recording session."""
    staged_repo = StagedSessionRepository(db)
    sess = staged_repo.create_session(
        session_code=payload.session_code,
        script_name=payload.script_name,
        room_id=payload.room_id,
        actor_names_json=payload.actor_names,
        target_video_path=payload.target_video_path,
        notes=payload.notes,
    )
    return {"status": "created", "session_id": sess.id, "session_code": sess.session_code}


@router.post("/staged-sessions/{session_id}/scenarios", summary="Add scenario to staged session")
def add_staged_scenario(
    session_id: str,
    payload: StagedScenarioCreateRequest,
    db: Session = Depends(get_db),
):
    """Add a scenario checklist item to an existing staged session."""
    staged_repo = StagedSessionRepository(db)
    sess = staged_repo.get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Staged session not found.")

    scen = staged_repo.add_scenario(
        session_id=session_id,
        scenario_code=payload.scenario_code,
        title=payload.title,
        expected_behavior=payload.expected_behavior,
        seat_code=payload.seat_code,
        target_start_ms=payload.target_start_ms,
        target_end_ms=payload.target_end_ms,
        notes=payload.notes,
    )
    return {"status": "created", "scenario_id": scen.id, "code": scen.scenario_code}


@router.put("/staged-sessions/{session_id}/scenarios/{checklist_id}", summary="Update staged scenario status")
def update_staged_scenario(
    session_id: str,
    checklist_id: str,
    payload: StagedScenarioUpdateRequest,
    db: Session = Depends(get_db),
):
    """Update execution status, actual timestamps, and notes for a scenario checklist item."""
    staged_repo = StagedSessionRepository(db)
    update_dict = payload.model_dump(exclude_unset=True)
    scen = staged_repo.update_scenario(checklist_id, **update_dict)
    if not scen:
        raise HTTPException(status_code=404, detail="Scenario item not found.")
    return {"status": "updated", "scenario_id": scen.id, "scenario_status": scen.status}
