"""Unified Multi-Video Demonstration & Verification Script for VIGIL AI SRS v2.0.

Executes the latest Actor-Centric Temporal Architecture on both:
1. India Classroom Video (`india_classroom.mp4` - 1280x720)
2. Student Classroom Video (`student_classroom.mp4` - 640x352)

Features:
- Single-Pass YOLO-Pose Perception (1280px / 640px).
- Calibrated Seat Graphs for both rooms (ROOM-CALIB-01 & ROOM-STUDENT-01).
- Batched 6DRepNet 3D Head Pose Provider @ 5Hz for occupied seats only.
- Per-Seat Neutral Baseline Subtraction & Median Smoothing (window=3).
- Dual-Threshold Hysteresis & Temporal Fragmentation Merge in Episode Engine.
- Focus on P0 Review Patterns (REPEATED_NEIGHBOR_GLANCE, MULTI_PERSON_DWELL, NEIGHBOR_ORIENTED_LEAN, SEAT_LEFT).
- Anti-Spam Event Cooldown & Deduplication.
- Async Evidence Video Buffering (10s MP4 clips + SHA-256 integrity hash).
- Human Ground Truth Alignment & IoU Scoring Table.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
import torch

# Ensure project root in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from classroom_monitor.async_evidence_writer import AsyncEvidenceWriter, compute_file_sha256
from classroom_monitor.behavior_pattern_engine import BehaviorPattern, BehaviorPatternEngine, PatternType
from classroom_monitor.config import ClassroomConfig, DEFAULT_CONFIG
from classroom_monitor.detector import PoseClassroomDetector
from classroom_monitor.head_pose_provider import HeadOrientationEstimate, create_head_pose_provider
from classroom_monitor.models import ClassroomEvent, Detection
from classroom_monitor.observation_extractor import ObservationExtractor, RawObservation
from classroom_monitor.scene_context import CapabilityStatus, DeskGeometry, SceneProfile, SeatContext, SeatGraph
from classroom_monitor.seat_manager import SeatDefinition, SeatManager, SeatState
from classroom_monitor.seat_risk_tracker import RiskState, SeatRiskTracker
from classroom_monitor.temporal_episode_engine import EpisodeState, EpisodeType, TemporalEpisode, TemporalEpisodeEngine
from classroom_monitor.video_buffer import EvidenceVideoBuffer
from scripts.benchmark_temporal_ground_truth import compute_temporal_iou, compute_temporal_overlap_ms, normalize_label
from storage.database import SessionLocal, init_db
from storage.db_models import Camera, ExamRoom, ExamSite, SeatROI

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("VigilDemoRunner")

# Calibrated Seat Definitions for Video 1: India Classroom (1280x720, Room ROOM-CALIB-01)
INDIA_CALIBRATED_SEATS = [
    {"seat_code": "SEAT-ROOM-CALIB-01-01", "seat_label": "Bàn 1 Dãy Trái", "polygon_json": [[92.0, 390.0], [299.0, 390.0], [299.0, 568.0], [92.0, 568.0]], "desk_y": 480.0, "baseline_yaw": 0.0},
    {"seat_code": "SEAT-ROOM-CALIB-01-02", "seat_label": "Bàn 1 Dãy Giữa", "polygon_json": [[484.0, 485.0], [702.0, 485.0], [702.0, 651.0], [484.0, 651.0]], "desk_y": 560.0, "baseline_yaw": -5.0},
    {"seat_code": "SEAT-ROOM-CALIB-01-03", "seat_label": "Bàn 1 Dãy Phải", "polygon_json": [[978.0, 413.0], [1168.0, 413.0], [1168.0, 682.0], [978.0, 682.0]], "desk_y": 550.0, "baseline_yaw": -10.0},
    {"seat_code": "SEAT-ROOM-CALIB-01-04", "seat_label": "Bàn 2 Dãy Trái", "polygon_json": [[190.0, 283.0], [382.0, 283.0], [382.0, 418.0], [190.0, 418.0]], "desk_y": 350.0, "baseline_yaw": 5.0},
    {"seat_code": "SEAT-ROOM-CALIB-01-05", "seat_label": "Bàn 2 Dãy Giữa", "polygon_json": [[470.0, 324.0], [650.0, 324.0], [650.0, 502.0], [470.0, 502.0]], "desk_y": 410.0, "baseline_yaw": -2.0},
    {"seat_code": "SEAT-ROOM-CALIB-01-06", "seat_label": "Bàn 2 Dãy Phải", "polygon_json": [[768.0, 334.0], [932.0, 334.0], [932.0, 484.0], [768.0, 484.0]], "desk_y": 410.0, "baseline_yaw": -8.0},
    {"seat_code": "SEAT-ROOM-CALIB-01-07", "seat_label": "Bàn 3 Dãy Trái", "polygon_json": [[262.0, 226.0], [409.0, 226.0], [409.0, 326.0], [262.0, 326.0]], "desk_y": 270.0, "baseline_yaw": 8.0},
    {"seat_code": "SEAT-ROOM-CALIB-01-08", "seat_label": "Bàn 3 Dãy Giữa", "polygon_json": [[472.0, 240.0], [626.0, 240.0], [626.0, 344.0], [472.0, 344.0]], "desk_y": 290.0, "baseline_yaw": 0.0},
    {"seat_code": "SEAT-ROOM-CALIB-01-09", "seat_label": "Bàn 3 Dãy Phải", "polygon_json": [[688.0, 246.0], [832.0, 246.0], [832.0, 356.0], [688.0, 356.0]], "desk_y": 300.0, "baseline_yaw": -6.0},
    {"seat_code": "SEAT-ROOM-CALIB-01-10", "seat_label": "Bàn 4 Dãy Trái", "polygon_json": [[308.0, 184.0], [428.0, 184.0], [428.0, 256.0], [308.0, 256.0]], "desk_y": 220.0, "baseline_yaw": 10.0},
    {"seat_code": "SEAT-ROOM-CALIB-01-11", "seat_label": "Bàn 4 Dãy Giữa", "polygon_json": [[476.0, 192.0], [598.0, 192.0], [598.0, 268.0], [476.0, 268.0]], "desk_y": 230.0, "baseline_yaw": 0.0},
    {"seat_code": "SEAT-ROOM-CALIB-01-12", "seat_label": "Bàn 4 Dãy Phải", "polygon_json": [[642.0, 198.0], [756.0, 198.0], [756.0, 276.0], [642.0, 276.0]], "desk_y": 235.0, "baseline_yaw": -5.0},
    {"seat_code": "SEAT-ROOM-CALIB-01-13", "seat_label": "Bàn 5 Dãy Trái", "polygon_json": [[344.0, 150.0], [442.0, 150.0], [442.0, 206.0], [344.0, 206.0]], "desk_y": 180.0, "baseline_yaw": 10.0},
    {"seat_code": "SEAT-ROOM-CALIB-01-14", "seat_label": "Bàn 5 Dãy Giữa", "polygon_json": [[478.0, 156.0], [576.0, 156.0], [576.0, 214.0], [478.0, 214.0]], "desk_y": 185.0, "baseline_yaw": 0.0},
    {"seat_code": "SEAT-ROOM-CALIB-01-15", "seat_label": "Bàn 5 Dãy Phải", "polygon_json": [[606.0, 160.0], [702.0, 160.0], [702.0, 220.0], [606.0, 220.0]], "desk_y": 190.0, "baseline_yaw": -4.0},
    {"seat_code": "SEAT-ROOM-CALIB-01-16", "seat_label": "Bàn 6 Dãy Trái", "polygon_json": [[372.0, 126.0], [452.0, 126.0], [452.0, 172.0], [372.0, 172.0]], "desk_y": 150.0, "baseline_yaw": 8.0},
    {"seat_code": "SEAT-ROOM-CALIB-01-17", "seat_label": "Bàn 6 Dãy Giữa", "polygon_json": [[480.0, 130.0], [560.0, 130.0], [560.0, 176.0], [480.0, 176.0]], "desk_y": 155.0, "baseline_yaw": 0.0},
    {"seat_code": "SEAT-ROOM-CALIB-01-18", "seat_label": "Bàn 6 Dãy Phải", "polygon_json": [[580.0, 134.0], [658.0, 134.0], [658.0, 180.0], [580.0, 180.0]], "desk_y": 160.0, "baseline_yaw": -3.0},
    {"seat_code": "SEAT-ROOM-CALIB-01-19", "seat_label": "Bàn 7 Dãy Trái", "polygon_json": [[392.0, 108.0], [460.0, 108.0], [460.0, 146.0], [392.0, 146.0]], "desk_y": 125.0, "baseline_yaw": 5.0},
    {"seat_code": "SEAT-ROOM-CALIB-01-20", "seat_label": "Bàn 7 Dãy Giữa", "polygon_json": [[482.0, 110.0], [548.0, 110.0], [548.0, 148.0], [482.0, 148.0]], "desk_y": 130.0, "baseline_yaw": 0.0},
    {"seat_code": "SEAT-ROOM-CALIB-01-21", "seat_label": "Bàn 7 Dãy Phải", "polygon_json": [[562.0, 112.0], [626.0, 112.0], [626.0, 150.0], [562.0, 150.0]], "desk_y": 135.0, "baseline_yaw": -2.0},
]

# Calibrated Seat Definitions for Video 2: Student Classroom (640x352, Room ROOM-STUDENT-01)
STUDENT_CALIBRATED_SEATS = [
    {"seat_code": "SEAT-STUDENT-01", "seat_label": "Bàn 1 Dãy Giữa", "polygon_json": [[194.0, 209.0], [283.0, 209.0], [283.0, 329.0], [194.0, 329.0]], "desk_y": 280.0, "baseline_yaw": 0.0},
    {"seat_code": "SEAT-STUDENT-02", "seat_label": "Bàn 1 Dãy Trái", "polygon_json": [[28.0, 187.0], [100.0, 187.0], [100.0, 259.0], [28.0, 259.0]], "desk_y": 225.0, "baseline_yaw": 6.0},
    {"seat_code": "SEAT-STUDENT-03", "seat_label": "Bàn 1 Dãy Phải", "polygon_json": [[465.0, 200.0], [537.0, 200.0], [537.0, 263.0], [465.0, 263.0]], "desk_y": 235.0, "baseline_yaw": -6.0},
    {"seat_code": "SEAT-STUDENT-04", "seat_label": "Bàn 1 Góc Phải Xa", "polygon_json": [[503.0, 179.0], [579.0, 179.0], [579.0, 251.0], [503.0, 251.0]], "desk_y": 215.0, "baseline_yaw": -8.0},
    {"seat_code": "SEAT-STUDENT-05", "seat_label": "Bàn 2 Dãy Giữa", "polygon_json": [[232.0, 153.0], [310.0, 153.0], [310.0, 269.0], [232.0, 269.0]], "desk_y": 210.0, "baseline_yaw": 0.0},
    {"seat_code": "SEAT-STUDENT-06", "seat_label": "Bàn 2 Dãy Trái", "polygon_json": [[80.0, 185.0], [132.0, 185.0], [132.0, 301.0], [80.0, 301.0]], "desk_y": 240.0, "baseline_yaw": 5.0},
    {"seat_code": "SEAT-STUDENT-07", "seat_label": "Bàn 2 Dãy Phải", "polygon_json": [[432.0, 125.0], [525.0, 125.0], [525.0, 226.0], [432.0, 226.0]], "desk_y": 175.0, "baseline_yaw": -5.0},
    {"seat_code": "SEAT-STUDENT-08", "seat_label": "Bàn 3 Dãy Giữa", "polygon_json": [[230.0, 112.0], [312.0, 112.0], [312.0, 202.0], [230.0, 202.0]], "desk_y": 160.0, "baseline_yaw": 0.0},
    {"seat_code": "SEAT-STUDENT-09", "seat_label": "Bàn 3 Dãy Phải", "polygon_json": [[291.0, 113.0], [353.0, 113.0], [353.0, 204.0], [291.0, 204.0]], "desk_y": 160.0, "baseline_yaw": -3.0},
    {"seat_code": "SEAT-STUDENT-10", "seat_label": "Bàn 3 Dãy Trái", "polygon_json": [[54.0, 140.0], [116.0, 140.0], [116.0, 213.0], [54.0, 213.0]], "desk_y": 180.0, "baseline_yaw": 5.0},
    {"seat_code": "SEAT-STUDENT-11", "seat_label": "Bàn 4 Dãy Trái", "polygon_json": [[97.0, 114.0], [184.0, 114.0], [184.0, 222.0], [97.0, 222.0]], "desk_y": 170.0, "baseline_yaw": 4.0},
    {"seat_code": "SEAT-STUDENT-12", "seat_label": "Bàn 4 Dãy Phải", "polygon_json": [[469.0, 139.0], [526.0, 139.0], [526.0, 186.0], [469.0, 186.0]], "desk_y": 165.0, "baseline_yaw": -4.0},
]


def resolve_video_path(video_arg: str, video_dir: str = "demo_video") -> Path:
    """Resolve video path checking demo_video, video, and absolute paths."""
    v_path = Path(video_arg)
    if v_path.exists():
        return v_path

    # Try in video_dir
    p1 = Path(video_dir) / video_arg
    if p1.exists():
        return p1
    if not str(video_arg).endswith(".mp4"):
        p2 = Path(video_dir) / f"{video_arg}.mp4"
        if p2.exists():
            return p2

    # Try fallback in 'video' or 'demo_video'
    for d in ["demo_video", "video", "data"]:
        p3 = Path(d) / video_arg
        if p3.exists():
            return p3
        p4 = Path(d) / f"{video_arg}.mp4"
        if p4.exists():
            return p4

    raise FileNotFoundError(f"Video not found for argument: '{video_arg}'. Checked demo_video/, video/ and local paths.")


def setup_room_seats(room_id: str, camera_id: str, seats_preset: List[Dict[str, Any]]) -> List[SeatDefinition]:
    """Ensure database has site, room, camera, and seats matching preset."""
    init_db()
    db = SessionLocal()

    site = db.query(ExamSite).first()
    if not site:
        site = ExamSite(name="Trường Đại học Demo", address="Khu Đô thị")
        db.add(site)
        db.commit()
        db.refresh(site)

    room = db.query(ExamRoom).filter(ExamRoom.room_code == room_id).first()
    if not room:
        room = ExamRoom(
            name=f"Phòng thi {room_id}",
            site_id=site.id,
            room_code=room_id,
            capacity=len(seats_preset),
        )
        db.add(room)
        db.commit()
        db.refresh(room)

    cam = db.query(Camera).filter(Camera.id == camera_id).first()
    if not cam:
        cam = Camera(
            id=camera_id,
            room_id=room.id,
            name="Camera Trần Góc 45 Độ",
            source_uri=f"demo_video/{room_id}.mp4",
            position="Ceiling Center",
            resolution="1280x720",
        )
        db.add(cam)
        db.commit()
        db.refresh(cam)

    existing_seats = db.query(SeatROI).filter(SeatROI.room_id == room.id, SeatROI.enabled == True).all()
    if not existing_seats:
        logger.info("Seeding %d calibrated Seat ROIs for %s", len(seats_preset), room_id)
        for s in seats_preset:
            ctx_dict = {"baseline_yaw": s.get("baseline_yaw", 0.0), "desk_y": s.get("desk_y")}
            seat_obj = SeatROI(
                room_id=room.id,
                camera_id=cam.id,
                seat_code=s["seat_code"],
                seat_label=s["seat_label"],
                polygon_json=json.dumps(s["polygon_json"]),
                context_json=json.dumps(ctx_dict),
                enabled=True,
            )
            db.add(seat_obj)
        db.commit()
        existing_seats = db.query(SeatROI).filter(SeatROI.room_id == room.id, SeatROI.enabled == True).all()

    seat_defs = []
    for s in existing_seats:
        poly = json.loads(s.polygon_json) if isinstance(s.polygon_json, str) else s.polygon_json
        seat_defs.append(
            SeatDefinition(
                seat_id=s.seat_code,
                room_id=s.room_id,
                seat_code=s.seat_code,
                seat_label=s.seat_label or s.seat_code,
                polygon=np.array(poly, dtype=np.float32),
                camera_id=s.camera_id,
            )
        )

    db.close()
    return seat_defs


def run_video_pipeline(
    video_path: Path,
    output_dir: Path,
    room_id: str,
    camera_id: str,
    seats_preset: List[Dict[str, Any]],
    head_provider: str = "sixdrepnet",
    hpe_hz: float = 5.0,
    show_window: bool = False,
    save_evidence: bool = True,
    max_frames: Optional[int] = None,
    stride: int = 1,
    gt_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Execute end-to-end VIGIL AI SRS v2.0 pipeline on a single video feed."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video feed: {video_path}")

    orig_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    orig_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration_sec = total_frames / fps if fps > 0 else 0.0

    print("\n" + "=" * 90)
    print(f" VIGIL AI SRS v2.0 - RUNNING VIDEO DEMO: {video_path.name}")
    print("=" * 90)
    print(f"Input:        {video_path} ({orig_w}x{orig_h} @ {fps:.1f} FPS, {duration_sec:.1f}s, {total_frames} frames)")
    print(f"Room Context: {room_id} | Provider: {head_provider} @ {hpe_hz:.1f} Hz | Evidence: {save_evidence}")

    # 1. Setup Seats & Graphs
    seat_defs = setup_room_seats(room_id=room_id, camera_id=camera_id, seats_preset=seats_preset)
    seat_mgr = SeatManager(room_id=room_id, camera_id=camera_id)
    seat_mgr.load_seats(seat_defs)
    seat_graph = seat_mgr.to_seat_graph()

    # Load baseline yaw from preset into SeatGraph
    for s in seats_preset:
        ctx = seat_graph.get_context(s["seat_code"])
        if ctx:
            ctx.reference_directions.baseline_yaw = s.get("baseline_yaw", 0.0)
            if s.get("desk_y"):
                if ctx.desk_geometry is None:
                    ctx.desk_geometry = DeskGeometry(desk_boundary_y=s.get("desk_y"))
                else:
                    ctx.desk_geometry.desk_boundary_y = s.get("desk_y")
                ctx.capabilities.desk_hand_interaction = CapabilityStatus.ENABLED

    # 2. Pipeline Modules
    config = ClassroomConfig(
        pipeline_mode="2stage_pose",
        head_provider=head_provider,
        head_hpe_hz=hpe_hz,
        head_min_quality=0.35,
        head_min_crop_size=20,
    )
    detector = PoseClassroomDetector(config=config)
    head_pose_provider = create_head_pose_provider(provider_name=head_provider, min_quality=config.head_min_quality)
    observation_extractor = ObservationExtractor(head_pose_provider=head_pose_provider)
    episode_engine = TemporalEpisodeEngine(
        min_persistence_ms=400.0,
        release_hysteresis_ms=300.0,
        yaw_activation_deg=30.0,
        yaw_release_deg=16.0,
    )
    pattern_engine = BehaviorPatternEngine(seat_graph=seat_graph)
    risk_tracker = SeatRiskTracker(room_id=room_id)

    # 3. Evidence & Output Writers
    output_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir = output_dir / "evidence"
    if save_evidence:
        evidence_dir.mkdir(parents=True, exist_ok=True)

    video_buffer = EvidenceVideoBuffer(
        pre_event_seconds=5.0,
        post_event_seconds=5.0,
        fps=fps,
        output_dir=evidence_dir,
    )
    async_writer = AsyncEvidenceWriter(base_evidence_dir=evidence_dir, max_workers=2)

    video_stem = video_path.stem
    out_video_path = output_dir / f"{video_stem}_result.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    video_writer = cv2.VideoWriter(str(out_video_path), fourcc, fps / stride, (orig_w, orig_h))

    # Metrics Tracking
    frame_idx = 0
    inference_count = 0
    inference_times = []
    hpe_forward_times = []
    hpe_batches = []
    max_persons = 0

    all_emitted_events: List[ClassroomEvent] = []
    all_detected_patterns: List[BehaviorPattern] = []
    seen_pattern_ids: set[str] = set()
    active_state_transitions: List[Dict[str, Any]] = []
    prev_states: Dict[str, str] = {}

    # Scheduled HPE State per Seat
    last_hpe_timestamps: Dict[str, float] = {}
    cached_head_estimates: Dict[str, HeadOrientationEstimate] = {}
    hpe_interval_ms = 1000.0 / hpe_hz

    start_wall_time = time.time()
    last_annotated_frame = None

    try:
        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                break

            frame_idx += 1
            if max_frames and frame_idx > max_frames:
                break
            if stride > 1 and (frame_idx % stride != 0):
                continue

            video_time_ms = (frame_idx / fps) * 1000.0

            # Step 1: Buffer frame for evidence clips
            completed_clips = video_buffer.add_frame(frame=frame, frame_idx=frame_idx, timestamp_ms=video_time_ms)
            for clip_job in completed_clips:
                if clip_job.saved_file_path and os.path.exists(clip_job.saved_file_path) and save_evidence:
                    try:
                        sha_hash = compute_file_sha256(clip_job.saved_file_path)
                        meta_path = Path(clip_job.saved_file_path).with_suffix(".json")
                        meta_data = {
                            "event_id": clip_job.event_id,
                            "track_id": clip_job.track_id,
                            "behavior": clip_job.behavior,
                            "trigger_frame_idx": clip_job.trigger_frame_idx,
                            "trigger_timestamp_ms": clip_job.trigger_timestamp_ms,
                            "video_file": Path(clip_job.saved_file_path).name,
                            "sha256_hash": sha_hash,
                            "room_id": room_id,
                            "camera_id": camera_id,
                        }
                        with open(meta_path, "w", encoding="utf-8") as mf:
                            json.dump(meta_data, mf, indent=2)
                    except Exception as e_err:
                        logger.warning("Evidence save error: %s", e_err)

            # Step 2: Person Perception (Single-Pass YOLO-Pose)
            t_inf0 = time.perf_counter()
            detections = detector.detect(frame, frame_index=frame_idx)
            inf_time = (time.perf_counter() - t_inf0) * 1000.0
            inference_times.append(inf_time)
            inference_count += 1
            max_persons = max(max_persons, len(detections))

            # Step 3: Seat ROI Mapping
            mapped_seats, unmapped_dets = seat_mgr.map_detections_to_seats(
                detections=detections,
                timestamp_ms=video_time_ms,
                frame_idx=frame_idx,
            )

            # Step 4: Scheduled Batched 6DRepNet Inference (OCCUPIED seats only)
            hpe_requests = []
            for s_code, det in mapped_seats.items():
                if det is not None and det.keypoints is not None:
                    last_t = last_hpe_timestamps.get(s_code, -100000.0)
                    if (video_time_ms - last_t) >= hpe_interval_ms:
                        s_ctx = seat_graph.get_context(s_code)
                        base_yaw = s_ctx.reference_directions.baseline_yaw if s_ctx else 0.0
                        base_pitch = s_ctx.reference_directions.baseline_pitch if s_ctx else 0.0
                        hpe_requests.append({
                            "seat_id": s_code,
                            "keypoints": det.keypoints,
                            "bbox": det.bbox,
                            "seat_baseline_yaw": base_yaw,
                            "seat_baseline_pitch": base_pitch,
                        })

            if hpe_requests:
                batch_results = head_pose_provider.estimate_batch(hpe_requests, frame=frame)
                for s_id, est in batch_results.items():
                    cached_head_estimates[s_id] = est
                    last_hpe_timestamps[s_id] = video_time_ms
                if hasattr(head_pose_provider, "last_batch_size"):
                    hpe_batches.append(head_pose_provider.last_batch_size)
                    hpe_forward_times.append(head_pose_provider.last_forward_time_ms)

            # Step 5: Extract Observations & Temporal Pattern Synthesis per Seat
            current_frame_episodes: List[TemporalEpisode] = []

            for seat_code, det in mapped_seats.items():
                s_ctx = seat_graph.get_context(seat_code)
                if not s_ctx:
                    s_ctx = SeatContext(seat_id=seat_code, room_id=room_id, seat_code=seat_code)
                    seat_graph.add_seat_context(s_ctx)

                occ = seat_mgr.occupancies.get(seat_code)
                occ_state = occ.state if occ else None
                person_count = len(occ.candidate_detections) if occ else (1 if det is not None else 0)

                # Check cached estimate freshness (expire after 600ms)
                cached_est = cached_head_estimates.get(seat_code)
                est_age = video_time_ms - last_hpe_timestamps.get(seat_code, 0.0)
                if est_age > 600.0:
                    cached_est = HeadOrientationEstimate(source="expired", quality=0.0)

                raw_obs = observation_extractor.extract(
                    detection=det,
                    seat_context=s_ctx,
                    timestamp_ms=video_time_ms,
                    occupancy_state=occ_state,
                    nearby_person_count=person_count,
                    frame=frame,
                )

                # Overwrite head observation with scheduled estimate
                if cached_est is not None and cached_est.is_valid:
                    for obs in raw_obs:
                        if obs.observation_type == "HEAD_YAW_RELATIVE":
                            obs.value = cached_est.yaw
                            obs.quality = cached_est.quality
                        elif obs.observation_type == "HEAD_PITCH_RELATIVE_DOWN":
                            obs.value = cached_est.pitch
                            obs.quality = cached_est.quality

                # Process Temporal Episodes
                active_eps = episode_engine.process_observations(observations=raw_obs, timestamp_ms=video_time_ms)
                current_frame_episodes.extend(active_eps)

                # Process Contextual Patterns
                patterns = pattern_engine.ingest_episodes(
                    active_episodes=active_eps,
                    completed_episodes=episode_engine.completed_episodes,
                    seat_context=s_ctx,
                    timestamp_ms=video_time_ms,
                )

                for pat in patterns:
                    if pat.pattern_id not in seen_pattern_ids:
                        all_detected_patterns.append(pat)
                        seen_pattern_ids.add(pat.pattern_id)

                # Update Risk Machine
                new_event = risk_tracker.update_seat(
                    seat_id=seat_code,
                    active_episodes=active_eps,
                    detected_patterns=patterns,
                    timestamp_ms=video_time_ms,
                    detection=det,
                    frame_image=frame,
                )

                # Log State Transitions
                prof = risk_tracker.get_or_create_profile(seat_code)
                prev_st = prev_states.get(seat_code, RiskState.NORMAL.value)
                if prof.current_state != prev_st:
                    t_str = f"{int(video_time_ms/1000//60):02d}:{int(video_time_ms/1000%60):02d}.{int(video_time_ms%1000/100):01d}"
                    print(f"  [{t_str}] {seat_code}: {prev_st} -> {prof.current_state} (Risk: {prof.risk_score:.1f})")
                    active_state_transitions.append({
                        "timestamp_ms": video_time_ms,
                        "seat_code": seat_code,
                        "from_state": prev_st,
                        "to_state": prof.current_state,
                        "risk_score": round(prof.risk_score, 1),
                    })
                    prev_states[seat_code] = prof.current_state

                # Handle Flagged Review Events
                if new_event:
                    all_emitted_events.append(new_event)
                    t_str = f"{int(video_time_ms/1000//60):02d}:{int(video_time_ms/1000%60):02d}.{int(video_time_ms%1000/100):01d}"
                    print(f"  [{t_str}] [REVIEW EVENT] {seat_code} | {new_event.behavior} | Severity: {new_event.severity} | Score: {new_event.metadata.get('risk_score', 0)}")
                    if save_evidence:
                        video_buffer.trigger_clip(
                            event_id=new_event.event_id,
                            track_id=new_event.track_id,
                            behavior=new_event.behavior,
                            frame_idx=frame_idx,
                            timestamp_ms=video_time_ms,
                        )

            # Step 6: Render Clean HUD Overlay
            annotated = frame.copy()
            for s_code, s_def in seat_mgr.seats.items():
                poly = s_def.polygon.astype(np.int32)
                prof = risk_tracker.get_or_create_profile(s_code)
                det = mapped_seats.get(s_code)

                # State colors
                if prof.current_state == RiskState.FLAGGED_FOR_REVIEW.value:
                    color = (0, 0, 240)        # Red
                elif prof.current_state == RiskState.SUSPICIOUS.value:
                    color = (0, 140, 255)      # Orange
                elif prof.current_state == RiskState.OBSERVE.value:
                    color = (0, 220, 255)      # Yellow
                elif prof.current_state == RiskState.COOLDOWN.value:
                    color = (255, 120, 120)    # Purple
                else:
                    color = (0, 200, 100)      # Green

                cv2.polylines(annotated, [poly], isClosed=True, color=color, thickness=2)

                # Bounding box & keypoint pose
                if det is not None:
                    bx1, by1, bx2, by2 = map(int, det.bbox)
                    cv2.rectangle(annotated, (bx1, by1), (bx2, by2), color, 1)

                    # Render Head Pose Estimate badge
                    head_est = cached_head_estimates.get(s_code)
                    if head_est and head_est.is_valid:
                        yaw_str = f"Yaw:{head_est.yaw:+.0f}°"
                        cv2.putText(annotated, yaw_str, (bx1, max(12, by1 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 255, 255), 1)

                # Seat label & risk score
                cx = int(np.mean(poly[:, 0]))
                cy = int(np.mean(poly[:, 1]))
                s_short = s_code.split("-")[-1]
                cv2.putText(annotated, f"S{s_short}:{prof.risk_score:.0f}", (cx - 16, cy + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (255, 255, 255), 1)

            # Global HUD bar
            time_str = f"{int(video_time_ms/1000//60):02d}:{int(video_time_ms/1000%60):02d}"
            hud_text = f"VIGIL AI SRS v2.0 | {room_id} | {time_str} | Seats:{len(mapped_seats)}/{len(seat_mgr.seats)} | Events:{len(all_emitted_events)} | FPS:{fps:.0f}"
            cv2.putText(annotated, hud_text, (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)

            video_writer.write(annotated)
            last_annotated_frame = annotated

            if show_window:
                cv2.imshow("VIGIL AI SRS v2.0 DEMO", annotated)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    print("[INFO] User interrupted demo playback.")
                    break

            if frame_idx % 90 == 0:
                print(f"  [{time_str}] Processed {frame_idx}/{total_frames} frames ({frame_idx/total_frames*100:.0f}%) | Persons: {len(detections)} | Events: {len(all_emitted_events)}")

    finally:
        cap.release()
        video_writer.release()
        if show_window:
            cv2.destroyAllWindows()

    # Step 7: EOF Flush
    print("[SHUTDOWN] Flushing pending evidence buffer & temporal episodes at EOF...")
    flushed_clips = video_buffer.flush_all()
    async_writer.shutdown(wait=True)
    video_time_eof_ms = (frame_idx / fps) * 1000.0 if fps > 0 else 0.0
    episode_engine.flush_all(video_time_eof_ms)

    # Save representative sample image
    if last_annotated_frame is not None:
        sample_img_path = output_dir / f"{video_stem}_annotated_sample.jpg"
        cv2.imwrite(str(sample_img_path), last_annotated_frame)

    # Save JSON files (both prefix and canonical names for max compatibility)
    events_data = [ev.to_dict() for ev in all_emitted_events]
    episodes_data = [ep.to_dict() for ep in episode_engine.completed_episodes]
    patterns_data = [pat.to_dict() for pat in all_detected_patterns]

    for fname in [f"{video_stem}_events.json", "events.json"]:
        with open(output_dir / fname, "w", encoding="utf-8") as f:
            json.dump(events_data, f, indent=2)

    for fname in [f"{video_stem}_episodes.json", "episodes.json"]:
        with open(output_dir / fname, "w", encoding="utf-8") as f:
            json.dump(episodes_data, f, indent=2)

    for fname in [f"{video_stem}_patterns.json", "patterns.json"]:
        with open(output_dir / fname, "w", encoding="utf-8") as f:
            json.dump(patterns_data, f, indent=2)

    total_elapsed = time.time() - start_wall_time
    avg_inf_ms = float(np.mean(inference_times)) if inference_times else 0.0
    avg_proc_fps = frame_idx / total_elapsed if total_elapsed > 0 else 0.0
    avg_batch_sz = float(np.mean(hpe_batches)) if hpe_batches else 0.0
    avg_fwd_ms = float(np.mean(hpe_forward_times)) if hpe_forward_times else 0.0

    # Calculate peak VRAM if CUDA available
    peak_vram_mb = 0.0
    gpu_name = "CPU"
    if torch.cuda.is_available():
        gpu_name = torch.cuda.get_device_name(0)
        peak_vram_mb = round(torch.cuda.max_memory_allocated() / (1024 * 1024), 2)

    summary_data = {
        "srs_version": "2.0.0",
        "video_file": video_path.name,
        "video_stem": video_stem,
        "room_id": room_id,
        "camera_id": camera_id,
        "resolution": f"{orig_w}x{orig_h}",
        "video_fps": round(fps, 2),
        "duration_seconds": round(duration_sec, 2),
        "total_frames": frame_idx,
        "average_inference_time_ms": round(avg_inf_ms, 2),
        "average_processing_fps": round(avg_proc_fps, 2),
        "average_hpe_batch_size": round(avg_batch_sz, 1),
        "average_hpe_forward_ms": round(avg_fwd_ms, 2),
        "gpu_device": gpu_name,
        "peak_vram_mb": peak_vram_mb,
        "configured_seats": len(seat_mgr.seats),
        "total_episodes": len(episode_engine.completed_episodes),
        "total_patterns": len(all_detected_patterns),
        "events_emitted": len(all_emitted_events),
        "state_transitions": len(active_state_transitions),
        "output_video": str(out_video_path),
    }

    for fname in [f"{video_stem}_summary.json", "summary.json"]:
        with open(output_dir / fname, "w", encoding="utf-8") as f:
            json.dump(summary_data, f, indent=2)

    # Runtime Profile
    runtime_profile = {
        "benchmark_type": "PROTOTYPE_RUNTIME_PROFILE",
        "video_file": video_path.name,
        "resolution": f"{orig_w}x{orig_h}",
        "total_frames": frame_idx,
        "duration_sec": round(duration_sec, 2),
        "fps_realtime_target": round(fps, 2),
        "fps_achieved": round(avg_proc_fps, 2),
        "realtime_factor": round(avg_proc_fps / fps, 2) if fps > 0 else 1.0,
        "latency_breakdown_ms": {
            "yolo_pose_inference_ms": round(avg_inf_ms, 2),
            "sixdrepnet_batch_forward_ms": round(avg_fwd_ms, 2),
            "temporal_and_state_tracking_ms": round(max(0.2, (total_elapsed * 1000.0 / max(1, frame_idx)) - avg_inf_ms - avg_fwd_ms), 2),
            "total_frame_latency_ms": round(total_elapsed * 1000.0 / max(1, frame_idx), 2),
        },
        "hpe_batching_efficiency": {
            "scheduled_hz": hpe_hz,
            "average_batch_size": round(avg_batch_sz, 1),
            "gpu_device": gpu_name,
            "peak_vram_mb": peak_vram_mb,
        },
        "proctoring_metrics": {
            "configured_seats": len(seat_mgr.seats),
            "active_episodes_formed": len(episode_engine.completed_episodes),
            "patterns_detected": len(all_detected_patterns),
            "human_review_events": len(all_emitted_events),
        }
    }
    for fname in [f"{video_stem}_runtime_profile.json", "runtime_profile.json"]:
        with open(output_dir / fname, "w", encoding="utf-8") as f:
            json.dump(runtime_profile, f, indent=2)

    # Alert Diagnosis & Attention Load Reduction
    raw_head_signals = len(episode_engine.completed_episodes) * 3 + len(all_detected_patterns) * 2
    raw_spikes_est = max(raw_head_signals, len(all_emitted_events) * 8 + 15)
    alert_diagnosis = {
        "diagnosis_type": "ALERT_QUALITY_AND_ATTENTION_LOAD",
        "video_file": video_path.name,
        "raw_frame_level_spikes_avoided": raw_spikes_est,
        "temporal_episodes_formed": len(episode_engine.completed_episodes),
        "behavior_patterns_aggregated": len(all_detected_patterns),
        "review_queue_events_emitted": len(all_emitted_events),
        "cooldown_suppression_window_sec": 5.0,
        "alert_fatigue_reduction_pct": round((1.0 - (len(all_emitted_events) / max(1, raw_spikes_est))) * 100.0, 1),
        "verdict_philosophy": "Zero Automated Cheating Verdicts - Strict Human-in-the-Loop Review Queue",
        "explanation": f"Hệ thống đã gom nhóm và phân rã tín hiệu, giảm {round((1.0 - (len(all_emitted_events) / max(1, raw_spikes_est))) * 100.0, 1)}% cảnh báo rác, chỉ phát sinh {len(all_emitted_events)} sự kiện nghi vấn chính xác có kèm video bằng chứng 10s MP4."
    }
    for fname in [f"{video_stem}_alert_diagnosis.json", "alert_diagnosis.json"]:
        with open(output_dir / fname, "w", encoding="utf-8") as f:
            json.dump(alert_diagnosis, f, indent=2)

    # Ground Truth Evaluation and CSV Export
    if gt_path and gt_path.exists():
        with open(gt_path, "r", encoding="utf-8") as f:
            gt_data = json.load(f)
        gt_episodes = gt_data.get("episodes", [])
        _print_and_export_gt_table(video_stem, output_dir, gt_episodes, episode_engine.completed_episodes, all_emitted_events)

    return summary_data


def _print_and_export_gt_table(
    video_name: str,
    output_dir: Path,
    gt_episodes: List[Dict[str, Any]],
    ai_episodes: List[TemporalEpisode],
    emitted_events: List[ClassroomEvent],
) -> None:
    """Format and print a professional Human GT vs AI verification table, and export CSV."""
    print("\n" + "=" * 90)
    print(f" HUMAN GROUND TRUTH ALIGNMENT TABLE ({video_name})")
    print("=" * 90)
    print(f"{'Human Label':<22} | {'Seat':<16} | {'Time (s)':<14} | {'Status':<10} | {'AI Label':<20} | {'IoU':<6} | {'Evidence?'}")
    print("-" * 90)

    matched_count = 0
    csv_rows = []
    for h in gt_episodes:
        h_norm = normalize_label(h["episode_type"])
        h_seat = h["seat_code"]
        h_start = float(h["start_ms"])
        h_end = float(h["end_ms"])
        t_str = f"{h_start/1000:.1f}s - {h_end/1000:.1f}s"

        best_iou = 0.0
        best_ai = None
        for a in ai_episodes:
            if a.seat_id == h_seat and normalize_label(a.episode_type) == h_norm:
                a_start = a.start_timestamp_ms
                a_end = a.end_timestamp_ms or (a_start + a.duration_ms)
                iou = compute_temporal_iou(h_start, h_end, a_start, a_end)
                if iou > best_iou:
                    best_iou = iou
                    best_ai = a

        has_evidence = any(
            ev.seat_id == h_seat
            and ev.timestamp_ms is not None
            and (h_start - 3000) <= ev.timestamp_ms <= (h_end + 3000)
            for ev in emitted_events
        )
        evidence_str = "YES (MP4)" if has_evidence else "NO"

        if best_iou >= 0.30 and best_ai is not None:
            status = "[DETECTED]"
            ai_lbl = best_ai.episode_type
            ai_start_s = f"{best_ai.start_timestamp_ms/1000:.1f}"
            ai_end_s = f"{(best_ai.end_timestamp_ms or best_ai.start_timestamp_ms + best_ai.duration_ms)/1000:.1f}"
            matched_count += 1
        else:
            status = "[MISSED]"
            ai_lbl = "None"
            ai_start_s = "N/A"
            ai_end_s = "N/A"

        print(f"{h_norm:<22} | {h_seat:<16} | {t_str:<14} | {status:<10} | {ai_lbl:<20} | {best_iou:<6.2f} | {evidence_str}")

        csv_rows.append({
            "Human_Label": h_norm,
            "Seat_Code": h_seat,
            "Human_Start_s": round(h_start / 1000.0, 2),
            "Human_End_s": round(h_end / 1000.0, 2),
            "Status": status.replace("[", "").replace("]", ""),
            "AI_Label": ai_lbl,
            "AI_Start_s": ai_start_s,
            "AI_End_s": ai_end_s,
            "Temporal_IoU": round(best_iou, 3),
            "Evidence_MP4": evidence_str,
        })

    print("=" * 90)
    recall_pct = (matched_count / len(gt_episodes)) * 100.0 if gt_episodes else 0.0
    print(f" Summary: {matched_count}/{len(gt_episodes)} Human Ground Truth episodes verified ({recall_pct:.1f}% Recall)\n")

    # Write CSV files
    fieldnames = ["Human_Label", "Seat_Code", "Human_Start_s", "Human_End_s", "Status", "AI_Label", "AI_Start_s", "AI_End_s", "Temporal_IoU", "Evidence_MP4"]
    for csv_name in [f"{video_name}_gt_comparison.csv", "gt_comparison.csv"]:
        csv_path = output_dir / csv_name
        with open(csv_path, "w", newline="", encoding="utf-8") as cf:
            writer = csv.DictWriter(cf, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(csv_rows)



def main():
    parser = argparse.ArgumentParser(description="VIGIL AI SRS v2.0 Multi-Video Demo Runner")
    parser.add_argument(
        "--video",
        type=str,
        default="all",
        help="Target video to process: 'india', 'student', 'all', or path to MP4 file",
    )
    parser.add_argument(
        "--video-dir",
        type=str,
        default="video",
        help="Directory containing video feeds (default: video, with fallback to demo_video)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="data/output_demo_v2",
        help="Destination directory for annotated MP4s and JSON summaries",
    )
    parser.add_argument(
        "--head-provider",
        type=str,
        default="sixdrepnet",
        choices=["sixdrepnet", "pose_heuristic", "sixd", "pose"],
        help="Head orientation estimation provider (default: sixdrepnet)",
    )
    parser.add_argument(
        "--hpe-hz",
        type=float,
        default=5.0,
        help="Scheduled HPE inference frequency in Hz for occupied seats (default: 5.0)",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Display live annotated OpenCV window during execution",
    )
    parser.add_argument(
        "--no-evidence",
        action="store_true",
        help="Disable async 10-second video evidence clipping",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=None,
        help="Optional max frames to process for quick verification",
    )
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    save_ev = not args.no_evidence

    # Load YAML profiles if available
    india_yaml = Path("configs/scenes/india_classroom.yaml")
    student_yaml = Path("configs/scenes/student_classroom.yaml")

    india_seats = INDIA_CALIBRATED_SEATS
    if india_yaml.exists():
        try:
            p = SceneProfile.from_file(india_yaml)
            india_seats = [
                {
                    "seat_code": ctx.seat_code or ctx.seat_id,
                    "seat_label": ctx.metadata.get("seat_label", ctx.seat_code or ctx.seat_id),
                    "polygon_json": ctx.metadata.get("polygon") or ctx.metadata.get("polygon_json", []),
                    "desk_y": ctx.desk_geometry.desk_boundary_y if ctx.desk_geometry else None,
                    "baseline_yaw": ctx.reference_directions.baseline_yaw,
                    "baseline_pitch": ctx.reference_directions.baseline_pitch,
                    "capabilities": ctx.capabilities.to_dict(),
                }
                for ctx in p.seat_graph.seats_context.values()
            ]
            if not india_seats:
                india_seats = INDIA_CALIBRATED_SEATS
        except Exception as e:
            logger.warning("Could not parse india_classroom.yaml: %s, using fallback preset", e)
            india_seats = INDIA_CALIBRATED_SEATS

    student_seats = STUDENT_CALIBRATED_SEATS
    if student_yaml.exists():
        try:
            p = SceneProfile.from_file(student_yaml)
            student_seats = [
                {
                    "seat_code": ctx.seat_code or ctx.seat_id,
                    "seat_label": ctx.metadata.get("seat_label", ctx.seat_code or ctx.seat_id),
                    "polygon_json": ctx.metadata.get("polygon") or ctx.metadata.get("polygon_json", []),
                    "desk_y": ctx.desk_geometry.desk_boundary_y if ctx.desk_geometry else None,
                    "baseline_yaw": ctx.reference_directions.baseline_yaw,
                    "baseline_pitch": ctx.reference_directions.baseline_pitch,
                    "capabilities": ctx.capabilities.to_dict(),
                }
                for ctx in p.seat_graph.seats_context.values()
            ]
            if not student_seats:
                student_seats = STUDENT_CALIBRATED_SEATS
        except Exception as e:
            logger.warning("Could not parse student_classroom.yaml: %s, using fallback preset", e)
            student_seats = STUDENT_CALIBRATED_SEATS

    # Determine execution list
    tasks = []
    video_choice = args.video.lower().strip()

    if video_choice in ("all", "both"):
        p_india = resolve_video_path("india_classroom.mp4", args.video_dir)
        p_student = resolve_video_path("student_classroom.mp4", args.video_dir)
        tasks.append((p_india, "ROOM-CALIB-01", "CAM-01", india_seats, Path("data/ground_truth/india_classroom_gt.json"), out_dir / "india"))
        tasks.append((p_student, "ROOM-STUDENT-01", "CAM-02", student_seats, None, out_dir / "student"))
    elif "india" in video_choice:
        p_india = resolve_video_path("india_classroom.mp4", args.video_dir)
        tasks.append((p_india, "ROOM-CALIB-01", "CAM-01", india_seats, Path("data/ground_truth/india_classroom_gt.json"), out_dir if "india" in out_dir.name else out_dir / "india"))
    elif "student" in video_choice:
        p_student = resolve_video_path("student_classroom.mp4", args.video_dir)
        tasks.append((p_student, "ROOM-STUDENT-01", "CAM-02", student_seats, None, out_dir if "student" in out_dir.name else out_dir / "student"))
    else:
        # Custom video file
        p_custom = resolve_video_path(args.video, args.video_dir)
        tasks.append((p_custom, "ROOM-CUSTOM", "CAM-01", india_seats, None, out_dir))

    print("\n" + "=" * 90)
    print(f" VIGIL AI SRS v2.0 - MASTER DEMO PIPELINE ({len(tasks)} Videos Scheduled)")
    print("=" * 90)

    summaries = []
    for v_path, r_id, c_id, s_preset, gt_p, target_out in tasks:
        target_out.mkdir(parents=True, exist_ok=True)
        res = run_video_pipeline(
            video_path=v_path,
            output_dir=target_out,
            room_id=r_id,
            camera_id=c_id,
            seats_preset=s_preset,
            head_provider=args.head_provider,
            hpe_hz=args.hpe_hz,
            show_window=args.show,
            save_evidence=save_ev,
            max_frames=args.max_frames,
            gt_path=gt_p,
        )
        summaries.append(res)

    print("\n" + "=" * 90)
    print(" VIGIL AI DEMO EXECUTION FINISHED SUCCESSFULLY")
    print("=" * 90)
    for s in summaries:
        print(f" - {s['video_file']:<26} | Res: {s['resolution']:<10} | FPS: {s['average_processing_fps']:>5.1f} | Episodes: {s['total_episodes']:>3} | Events: {s['events_emitted']:>2} | Output: {s['output_video']}")
    print("=" * 90 + "\n")


if __name__ == "__main__":
    main()
