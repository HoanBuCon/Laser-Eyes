"""End-to-End Real Video Demonstration & Verification Script for VIGIL AI.

Validates the full production pipeline on demo_video/india_classroom.mp4:
1. Video Ingestion & Frame Timestamping
2. Single-Pass 1280px YOLO-Pose Inference
3. Seat ROI Polygon Mapping & Seat Identity Persistence
4. Standardized Unknown-Safe Behavior Signal Extraction
5. Time-Aware Temporal 0-100 Risk Scoring
6. 4-Tier State Machine Transitions & Silent Cooldown Tracking
7. Event Engine Triggering & Disciplinary Flagging
8. Async Evidence Video Buffering, Snapshot Extraction & SHA-256 Digest
9. Rich Visual Overlays & Annotated Result MP4 Generation
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

# Ensure project root in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from classroom_monitor.async_evidence_writer import AsyncEvidenceWriter, compute_file_sha256
from classroom_monitor.behavior_signals import BehaviorSignal, BehaviorSignalExtractor, SignalType
from classroom_monitor.config import ClassroomConfig, DEFAULT_CONFIG
from classroom_monitor.detector import PoseClassroomDetector
from classroom_monitor.models import ClassroomEvent, Detection
from classroom_monitor.seat_manager import SeatDefinition, SeatManager, SeatState
from classroom_monitor.seat_risk_tracker import RiskState, SeatRiskTracker
from classroom_monitor.video_buffer import EvidenceVideoBuffer
from storage.database import SessionLocal, init_db
from storage.repositories import CameraRepository, RoomRepository, SeatRepository, SiteRepository

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ClassroomDemo")

# Pre-calibrated Seat ROIs for demo_video/india_classroom.mp4 (1280x720)
DEFAULT_CALIBRATED_SEATS = [
    {
        "seat_code": "SEAT-101-01",
        "seat_label": "Bàn 1 Dãy Trái (Hàng 1)",
        "polygon_json": [[92.0, 390.0], [299.0, 390.0], [299.0, 568.0], [92.0, 568.0]],
        "enabled": True,
    },
    {
        "seat_code": "SEAT-101-02",
        "seat_label": "Bàn 1 Dãy Giữa (Hàng 1)",
        "polygon_json": [[484.0, 485.0], [702.0, 485.0], [702.0, 651.0], [484.0, 651.0]],
        "enabled": True,
    },
    {
        "seat_code": "SEAT-101-03",
        "seat_label": "Bàn 1 Dãy Phải (Hàng 1)",
        "polygon_json": [[978.0, 413.0], [1168.0, 413.0], [1168.0, 682.0], [978.0, 682.0]],
        "enabled": True,
    },
    {
        "seat_code": "SEAT-101-04",
        "seat_label": "Bàn 2 Dãy Trái (Hàng 2)",
        "polygon_json": [[190.0, 283.0], [382.0, 283.0], [382.0, 418.0], [190.0, 418.0]],
        "enabled": True,
    },
    {
        "seat_code": "SEAT-101-05",
        "seat_label": "Bàn 2 Dãy Giữa (Hàng 2)",
        "polygon_json": [[470.0, 324.0], [650.0, 324.0], [650.0, 502.0], [470.0, 502.0]],
        "enabled": True,
    },
]


def setup_database_seats(room_id: str, camera_id: str) -> List[SeatDefinition]:
    """Ensure database has exam site, room, camera, and calibrated seats."""
    init_db()
    db = SessionLocal()

    site_repo = SiteRepository(db)
    room_repo = RoomRepository(db)
    cam_repo = CameraRepository(db)
    seat_repo = SeatRepository(db)

    # 1. Ensure Site & Room
    sites = site_repo.list_all()
    site = sites[0] if sites else site_repo.create(name="Trường Đại học Demo", address="Khu Đô thị")

    room = room_repo.get_by_id(room_id) or room_repo.get_by_code(room_id)
    if not room:
        room = room_repo.create(
            name=f"Phòng thi {room_id}",
            site_id=site.id,
            room_code=room_id,
            capacity=30,
        )

    # 2. Ensure Camera
    cam = cam_repo.get_by_id(camera_id)
    if not cam:
        cam = cam_repo.create(
            room_id=room.id,
            name="Camera Trần Góc 45 Độ",
            source_uri="demo_video/india_classroom.mp4",
            position="Ceiling Front-Right",
            resolution="1280x720",
        )

    # 3. Check existing seats
    db_seats = seat_repo.list_by_room(room_id=room.id, enabled_only=True)
    if not db_seats:
        logger.info("Seeding %d calibrated Seat ROIs to database for room %s", len(DEFAULT_CALIBRATED_SEATS), room_id)
        seats_payload = []
        for s in DEFAULT_CALIBRATED_SEATS:
            seats_payload.append({
                "room_id": room.id,
                "camera_id": cam.id,
                "seat_code": s["seat_code"],
                "seat_label": s["seat_label"],
                "polygon_json": s["polygon_json"],
                "enabled": s["enabled"],
            })
        db_seats = seat_repo.bulk_upsert_for_camera(room_id=room.id, camera_id=cam.id, seats_data=seats_payload)

    seat_defs = []
    for s in db_seats:
        seat_defs.append(
            SeatDefinition.from_dict({
                "id": s.id,
                "room_id": s.room_id,
                "seat_code": s.seat_code,
                "seat_label": s.seat_label,
                "polygon_json": s.polygon_json,
                "enabled": s.enabled,
            })
        )

    db.close()
    return seat_defs


def draw_skeletons(image: np.ndarray, keypoints: Optional[np.ndarray], conf_thresh: float = 0.3) -> None:
    """Draw COCO 17 keypoint skeleton bones on image."""
    if keypoints is None or len(keypoints) < 17:
        return

    skeleton_pairs = [
        (0, 1), (0, 2), (1, 3), (2, 4),        # Head
        (5, 6), (5, 7), (7, 9), (6, 8), (8, 10),# Upper body & Arms
        (5, 11), (6, 12), (11, 12),             # Torso
        (11, 13), (13, 15), (12, 14), (14, 16), # Legs
    ]

    for p1, p2 in skeleton_pairs:
        kp1 = keypoints[p1]
        kp2 = keypoints[p2]
        if kp1[2] >= conf_thresh and kp2[2] >= conf_thresh:
            pt1 = (int(kp1[0]), int(kp1[1]))
            pt2 = (int(kp2[0]), int(kp2[1]))
            cv2.line(image, pt1, pt2, (0, 255, 255), 1, cv2.LINE_AA)

    for i, kp in enumerate(keypoints):
        if kp[2] >= conf_thresh:
            cv2.circle(image, (int(kp[0]), int(kp[1])), 3, (0, 165, 255), -1, cv2.LINE_AA)


def run_classroom_demo(
    input_video: str,
    output_video: str,
    room_id: str = "ROOM-101",
    camera_id: str = "CAM-01",
    show_window: bool = False,
    save_events: bool = True,
    save_evidence: bool = True,
    max_frames: Optional[int] = None,
    stride: int = 1,
    debug: bool = False,
    show_pose: bool = False,
) -> Dict[str, Any]:
    """Execute end-to-end VIGIL AI pipeline on input video."""
    video_path = Path(input_video)
    if not video_path.exists():
        raise FileNotFoundError(f"Input video file does not exist: {input_video}")

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video file: {input_video}")

    orig_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    orig_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_video_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration_sec = total_video_frames / fps if fps > 0 else 0.0

    print("================================================================================")
    print("[START] VIGIL AI END-TO-END PRODUCTION PIPELINE DEMO")
    print("================================================================================")
    print(f"Input Video:      {video_path} ({orig_w}x{orig_h} @ {fps:.1f} FPS, {duration_sec:.1f}s, {total_video_frames} frames)")
    print(f"Room & Camera:    Room: {room_id} | Camera: {camera_id}")

    # 1. Setup Production Modules
    # Database & Calibrated Seats
    seat_defs = setup_database_seats(room_id=room_id, camera_id=camera_id)
    seat_mgr = SeatManager(room_id=room_id, camera_id=camera_id)
    seat_mgr.load_seats(seat_defs)
    print(f"SeatManager:      Loaded {len(seat_mgr.seats)} production Seat ROIs from CSDL.")

    # YOLO-Pose Detector
    config = ClassroomConfig(
        pipeline_mode="2stage_pose",
        enable_sahi_tiling=False,
    )
    detector = PoseClassroomDetector(config=config)

    # Behavior Signal Extractor
    signal_extractor = BehaviorSignalExtractor(config=config)

    # Per-Seat Temporal Risk Tracker & State Machine
    risk_tracker = SeatRiskTracker(room_id=room_id)

    # 10s Ring Buffer & Async Evidence Writer
    evidence_dir = Path("data/output_demo/evidence")
    if save_evidence:
        evidence_dir.mkdir(parents=True, exist_ok=True)

    video_buffer = EvidenceVideoBuffer(
        pre_event_seconds=5.0,
        post_event_seconds=5.0,
        fps=fps,
        output_dir=evidence_dir,
    )
    async_writer = AsyncEvidenceWriter(base_evidence_dir=evidence_dir, max_workers=2)

    # Setup Video Output Writer
    out_dir = Path(output_video).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    video_writer = cv2.VideoWriter(str(output_video), fourcc, fps / stride, (orig_w, orig_h))

    # Tracking Statistics
    frame_idx = 0
    inference_count = 0
    inference_times: List[float] = []
    max_persons_detected = 0
    # Signal tracking (active frames, episodes, etc.)
    signal_tracker = {
        SignalType.PROLONGED_HEAD_TURN.value: {"active_frames": 0, "episodes": 0, "max_duration": 0.0, "total_duration": 0.0, "current_start": None},
        SignalType.BODY_LEAN_SIDE.value: {"active_frames": 0, "episodes": 0, "max_duration": 0.0, "total_duration": 0.0, "current_start": None},
        SignalType.SUSPICIOUS_BELOW_DESK_ACTIVITY.value: {"active_frames": 0, "episodes": 0, "max_duration": 0.0, "total_duration": 0.0, "current_start": None},
        SignalType.LOOK_DOWN_LONG.value: {"active_frames": 0, "episodes": 0, "max_duration": 0.0, "total_duration": 0.0, "current_start": None},
        SignalType.LOW_HAND_POSTURE.value: {"active_frames": 0, "episodes": 0, "max_duration": 0.0, "total_duration": 0.0, "current_start": None},
    }

    # Store active signal types per seat to detect episode boundaries
    active_signals_per_seat = {seat_id: set() for seat_id in seat_mgr.seats.keys()}

    all_emitted_events: List[ClassroomEvent] = []
    active_state_transitions: List[Dict[str, Any]] = []
    prev_states: Dict[str, str] = {}

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

            # 2. Add frame into rolling ring buffer for evidence recording
            completed_clips = video_buffer.add_frame(
                frame=frame,
                frame_idx=frame_idx,
                timestamp_ms=video_time_ms,
            )

            # Process completed clips
            for clip_job in completed_clips:
                if clip_job.saved_file_path and save_evidence:
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
                        print(f"[EVIDENCE] Generated package for {clip_job.event_id}: MP4 + SHA-256 ({sha_hash[:12]}...)")
                    except Exception as e_err:
                        logger.warning("Evidence package generation error: %s", e_err)

            # 3. Perception: YOLO-Pose Single-Pass Forward
            inf_start = time.perf_counter()
            detections = detector.detect(frame, frame_index=frame_idx)
            inf_time = (time.perf_counter() - inf_start) * 1000.0
            inference_times.append(inf_time)
            inference_count += 1

            max_persons_detected = max(max_persons_detected, len(detections))

            # 4. Seat ROI Mapping & Identity Persistence
            mapped_seats, unmapped_dets = seat_mgr.map_detections_to_seats(
                detections=detections,
                timestamp_ms=video_time_ms,
                frame_idx=frame_idx,
            )

            # 5. Extract Behavior Signals & Update Risk State Machine
            frame_signals: Dict[str, List[BehaviorSignal]] = {}
            for seat_code, det in mapped_seats.items():
                active_signals: List[BehaviorSignal] = []
                s_def = seat_mgr.seats.get(seat_code)
                eff_desk_y = s_def.get_effective_desk_y() if s_def else None

                if det is not None:
                    kp = det.keypoints if det.keypoints is not None else np.zeros((17, 3), dtype=np.float32)
                    active_signals = signal_extractor.analyze_candidate_keypoints(
                        keypoints=kp,
                        timestamp_ms=video_time_ms,
                        seat_id=seat_code,
                        camera_id=camera_id,
                        desk_y=eff_desk_y,
                    )
                    current_frame_signals = {sig.signal_type for sig in active_signals}
                    prev_frame_signals = active_signals_per_seat[seat_code]
                    
                    for sig_type in current_frame_signals:
                        if sig_type in signal_tracker:
                            signal_tracker[sig_type]["active_frames"] += 1
                            if sig_type not in prev_frame_signals:
                                # New episode started
                                signal_tracker[sig_type]["episodes"] += 1
                                signal_tracker[sig_type]["current_start"] = video_time_ms
                    
                    for sig_type in prev_frame_signals:
                        if sig_type not in current_frame_signals and sig_type in signal_tracker:
                            # Episode ended
                            start_time = signal_tracker[sig_type]["current_start"]
                            if start_time is not None:
                                duration = (video_time_ms - start_time) / 1000.0
                                signal_tracker[sig_type]["total_duration"] += duration
                                signal_tracker[sig_type]["max_duration"] = max(signal_tracker[sig_type]["max_duration"], duration)
                                signal_tracker[sig_type]["current_start"] = None
                    
                    active_signals_per_seat[seat_code] = current_frame_signals
                    
                frame_signals[seat_code] = active_signals

                # Update 0-100 Risk Engine & State Machine
                event = risk_tracker.update_seat(
                    seat_id=seat_code,
                    active_signals=active_signals,
                    timestamp_ms=video_time_ms,
                    detection=det,
                    frame_image=frame,
                )

                profile = risk_tracker.profiles.get(seat_code)
                cur_state = profile.current_state if profile else RiskState.NORMAL.value
                old_state = prev_states.get(seat_code, RiskState.NORMAL.value)

                # Track State Transition
                if cur_state != old_state:
                    trans_info = {
                        "timestamp_ms": round(video_time_ms, 1),
                        "time_str": f"{int(video_time_ms/1000//60):02d}:{int(video_time_ms/1000%60):02d}.{int(video_time_ms%1000):03d}",
                        "seat_id": seat_code,
                        "transition": f"{old_state} -> {cur_state}",
                        "risk_score": profile.risk_score if profile else 0,
                    }
                    active_state_transitions.append(trans_info)
                    prev_states[seat_code] = cur_state
                    print(f"[{trans_info['time_str']}] {seat_code}: {trans_info['transition']} (Risk: {trans_info['risk_score']})")

                # Event Emitted
                if event is not None:
                    all_emitted_events.append(event)
                    print(f"\n[EVENT TRIGGERED] Event ID: {event.event_id} | Seat: {seat_code} | Behavior: {event.behavior} | Conf: {event.confidence_peak:.2f} | Time: {video_time_ms/1000:.1f}s")

                    if save_evidence:
                        # Save Peak Snapshot
                        if event.evidence_frame is not None:
                            snap_path = evidence_dir / f"{event.event_id}_snapshot.jpg"
                            cv2.imwrite(str(snap_path), event.evidence_frame)

                        video_buffer.trigger_clip(
                            event_id=event.event_id,
                            track_id=event.track_id,
                            behavior=event.behavior,
                            frame_idx=frame_idx,
                            timestamp_ms=video_time_ms,
                        )

            # 6. Visualization & Annotations
            annotated = frame.copy()
            overlay = frame.copy()

            # (a) Draw Seat ROI Polygons
            for s_code, s_def in seat_mgr.seats.items():
                profile = risk_tracker.profiles.get(s_code)
                r_score = profile.risk_score if profile else 0.0
                state = profile.current_state if profile else RiskState.NORMAL.value

                if state == RiskState.FLAGGED_FOR_REVIEW.value:
                    poly_color = (0, 0, 235)       # Vivid Red
                    alpha = 0.35
                    border_thick = 2
                elif state == RiskState.SUSPICIOUS.value:
                    poly_color = (0, 140, 255)     # Vibrant Orange
                    alpha = 0.25
                    border_thick = 2
                elif state == RiskState.OBSERVE.value:
                    poly_color = (0, 215, 255)     # Amber / Yellow
                    alpha = 0.15
                    border_thick = 1
                elif state == RiskState.COOLDOWN.value:
                    poly_color = (180, 100, 210)   # Purple / Cooldown State
                    alpha = 0.15
                    border_thick = 1
                else:
                    poly_color = (50, 205, 50)     # Emerald Green
                    alpha = 0.08 if not debug else 0.18
                    border_thick = 1

                poly_pts = np.array(s_def.polygon, dtype=np.int32).reshape((-1, 1, 2))
                cv2.fillPoly(overlay, [poly_pts], poly_color)
                cv2.polylines(annotated, [poly_pts], isClosed=True, color=poly_color, thickness=border_thick)

            cv2.addWeighted(overlay, 0.40, annotated, 0.60, 0, annotated)

            # (b) Draw Persons, Bounding Boxes & Clean Seat HUD
            for s_code, det in mapped_seats.items():
                s_def = seat_mgr.seats.get(s_code)
                profile = risk_tracker.profiles.get(s_code)
                r_score = profile.risk_score if profile else 0.0
                state = profile.current_state if profile else RiskState.NORMAL.value
                sigs = frame_signals.get(s_code, [])

                # Format short human-readable seat code (e.g. S01, S02)
                seat_label = s_code.replace("SEAT-101-0", "S").replace("SEAT-101-", "S").replace("SEAT-", "S")

                if s_def is not None:
                    scx = int(np.mean(s_def.polygon[:, 0]))
                    scy = int(np.mean(s_def.polygon[:, 1]))

                    # Format clean rounded risk score and state badge
                    badge_title = f"{seat_label} | R:{r_score:.0f} | {state}" if not debug else f"{s_code} | Risk: {r_score:.1f} | {state}"
                    
                    # Prioritize suspicious composite signals over context observations
                    active_types = {s.signal_type for s in sigs}
                    sub_title = ""
                    if SignalType.SUSPICIOUS_BELOW_DESK_ACTIVITY.value in active_types:
                        sub_title = "[UNDER_DESK_ACT]" if not debug else "[SUSPICIOUS_BELOW_DESK_ACTIVITY]"
                    elif SignalType.PROLONGED_HEAD_TURN.value in active_types:
                        sub_title = "[HEAD_TURN]" if not debug else "[PROLONGED_HEAD_TURN]"
                    elif SignalType.BODY_LEAN_SIDE.value in active_types:
                        sub_title = "[BODY_LEAN]" if not debug else "[BODY_LEAN_SIDE]"
                    elif debug and sigs:
                        sub_title = f"[{sigs[0].signal_type}]"

                    # State badge colors
                    badge_bg = (15, 23, 42)
                    badge_border = (0, 255, 200) if state == "NORMAL" else (poly_color)

                    (tw, th), _ = cv2.getTextSize(badge_title, cv2.FONT_HERSHEY_SIMPLEX, 0.42, 1)
                    box_h = th + 6 if not sub_title else th + 18
                    cv2.rectangle(annotated, (scx - tw//2 - 5, scy - th - 5), (scx + tw//2 + 5, scy + box_h - th), badge_bg, -1)
                    cv2.rectangle(annotated, (scx - tw//2 - 5, scy - th - 5), (scx + tw//2 + 5, scy + box_h - th), badge_border, 1)
                    cv2.putText(annotated, badge_title, (scx - tw//2, scy), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1, cv2.LINE_AA)

                    if sub_title:
                        (stw, sth), _ = cv2.getTextSize(sub_title, cv2.FONT_HERSHEY_SIMPLEX, 0.35, 1)
                        cv2.putText(annotated, sub_title, (scx - stw//2, scy + 12), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (200, 225, 255), 1, cv2.LINE_AA)

                if det is not None:
                    x1, y1, x2, y2 = [int(v) for v in det.bbox]
                    cv2.rectangle(annotated, (x1, y1), (x2, y2), (255, 180, 0), 2 if debug else 1)

                    # Draw Skeletons if enabled or in debug mode
                    if (show_pose or debug) and det.keypoints is not None:
                        draw_skeletons(annotated, det.keypoints)

                    # Draw Anchor Dot in debug mode only
                    if debug:
                        ac_x = int((x1 + x2) / 2)
                        ac_y = int(y2 - (y2 - y1) * 0.15)
                        cv2.circle(annotated, (ac_x, ac_y), 5, (0, 255, 255), -1)
                        cv2.circle(annotated, (ac_x, ac_y), 7, (0, 0, 0), 1)

            # (c) Draw Unmapped Persons (Explicitly UNMAPPED_PERSON per SRS Scope 04)
            for udet in unmapped_dets:
                ux1, uy1, ux2, uy2 = [int(v) for v in udet.bbox]
                # Subtle slate gray box
                cv2.rectangle(annotated, (ux1, uy1), (ux2, uy2), (100, 116, 139), 1)
                unmapped_label = "UNMAPPED" if not debug else f"UNMAPPED_PERSON (conf={udet.confidence:.2f})"
                (utw, uth), _ = cv2.getTextSize(unmapped_label, cv2.FONT_HERSHEY_SIMPLEX, 0.36, 1)
                cv2.rectangle(annotated, (ux1, max(0, uy1 - uth - 4)), (ux1 + utw + 4, uy1), (30, 41, 59), -1)
                cv2.putText(annotated, unmapped_label, (ux1 + 2, uy1 - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.36, (148, 163, 184), 1, cv2.LINE_AA)

            # (d) Draw Top Telemetry Bar
            cv2.rectangle(annotated, (0, 0), (orig_w, 38), (15, 23, 42), -1)
            cv2.line(annotated, (0, 38), (orig_w, 38), (0, 255, 200), 1)

            time_str = f"{int(video_time_ms/1000//60):02d}:{int(video_time_ms/1000%60):02d}.{int(video_time_ms%1000/100):01d}"
            mapped_count = sum(1 for d in mapped_seats.values() if d is not None)
            hud_text = (
                f"VIGIL AI ENTERPRISE | Room: {room_id} | Time: {time_str} | "
                f"Frame: {frame_idx}/{total_video_frames} | "
                f"Seats: {mapped_count}/{len(seat_mgr.seats)} | "
                f"Unmapped: {len(unmapped_dets)} | "
                f"Events: {len(all_emitted_events)}"
            )
            cv2.putText(annotated, hud_text, (14, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.50, (255, 255, 255), 1, cv2.LINE_AA)

            # Save verification snapshots for manual review (SRS Section 38)
            if 270 <= frame_idx <= 290 and (out_dir / "after_fix_sample.jpg").exists() is False:
                cv2.imwrite(str(out_dir / "after_fix_sample.jpg"), annotated)
                print(f"[OK] Saved After-Fix verification snapshot: {out_dir / 'after_fix_sample.jpg'}")
            
            # Save normal writing sample
            if frame_idx == 350 and (out_dir / "normal_writing_sample.jpg").exists() is False:
                cv2.imwrite(str(out_dir / "normal_writing_sample.jpg"), annotated)
            
            # Save look down context sample
            if frame_idx == 450 and (out_dir / "look_down_sample.jpg").exists() is False:
                cv2.imwrite(str(out_dir / "look_down_sample.jpg"), annotated)

            # Write Frame to Output MP4
            video_writer.write(annotated)
            last_annotated_frame = annotated

            if show_window:
                cv2.imshow("VIGIL AI PRODUCTION DEMO", annotated)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    print("[INFO] User interrupted the demo.")
                    break

            # Progress log every 60 frames
            if frame_idx % 60 == 0:
                print(f"[{time_str}] Processed {frame_idx}/{total_video_frames} frames (Inf: {inf_time:.1f}ms | Persons: {len(detections)} | Events: {len(all_emitted_events)})")

    finally:
        cap.release()
        video_writer.release()
        if show_window:
            cv2.destroyAllWindows()
        
        # Flush any in-flight 10s video evidence clips that are pending post-frames
        if save_evidence:
            print("[SHUTDOWN] Flushing pending evidence video buffer...")
            video_buffer._active_jobs  # To access jobs to get metadata
            # We need to compute hashes for the flushed jobs before they are cleared
            # Actually, flush_all just writes them. Let's process the active jobs manually.
            with video_buffer._lock:
                for clip_job in video_buffer._active_jobs:
                    clip_job.is_completed = True
                    clip_job.saved_file_path = video_buffer._write_clip_to_disk(clip_job)
                    
                    if clip_job.saved_file_path:
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
                            print(f"[SHUTDOWN EVIDENCE] Flushed package for {clip_job.event_id}")
                        except Exception as e_err:
                            logger.warning("Evidence package generation error: %s", e_err)
                video_buffer._active_jobs.clear()
            
            async_writer.shutdown(wait=True)

    # Close active episodes
    for sig_type, stats in signal_tracker.items():
        if stats["current_start"] is not None:
            duration = (video_time_ms - stats["current_start"]) / 1000.0
            stats["total_duration"] += duration
            stats["max_duration"] = max(stats["max_duration"], duration)
            stats["current_start"] = None
        
        # Calculate averages safely
        stats["avg_duration"] = round(stats["total_duration"] / stats["episodes"], 2) if stats["episodes"] > 0 else 0.0
        stats["max_duration"] = round(stats["max_duration"], 2)
        stats["total_duration"] = round(stats["total_duration"], 2)

    total_processing_wall_time = time.time() - start_wall_time
    avg_inf_time = float(np.mean(inference_times)) if inference_times else 0.0
    avg_proc_fps = frame_idx / total_processing_wall_time if total_processing_wall_time > 0 else 0.0

    # Save Representative Annotated Screenshot
    if last_annotated_frame is not None:
        sample_img_path = out_dir / "india_classroom_annotated_sample.jpg"
        cv2.imwrite(str(sample_img_path), last_annotated_frame)
        print(f"[OK] Representative annotated frame saved: {sample_img_path}")

    # Peak Risk Per Seat
    peak_risks = {}
    for s_code, prof in risk_tracker.profiles.items():
        peak_risks[s_code] = {
            "peak_risk_score": prof.peak_risk_score,
            "final_risk_score": prof.risk_score,
            "final_state": prof.current_state,
        }

    # Save events.json
    events_json_path = out_dir / "events.json"
    if save_events:
        events_dicts = [ev.to_dict() for ev in all_emitted_events]
        with open(events_json_path, "w", encoding="utf-8") as f:
            json.dump(events_dicts, f, indent=2)

    # Save demo_summary.json
    summary_data = {
        "input_video": str(video_path),
        "resolution": f"{orig_w}x{orig_h}",
        "video_fps": round(fps, 2),
        "duration_seconds": round(duration_sec, 2),
        "total_frames_read": frame_idx,
        "inference_frames": inference_count,
        "average_inference_time_ms": round(avg_inf_time, 2),
        "average_processing_fps": round(avg_proc_fps, 2),
        "maximum_persons_detected": max_persons_detected,
        "configured_seats_count": len(seat_mgr.seats),
        "signals_detected": signal_tracker,
        "peak_risk_per_seat": peak_risks,
        "events_generated_count": len(all_emitted_events),
        "state_transitions_count": len(active_state_transitions),
        "output_video": str(output_video),
    }

    summary_json_path = out_dir / "demo_summary.json"
    with open(summary_json_path, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=2)

    # Print Final Summary Report
    print("\n============================================================")
    print("VIGIL AI — INDIA CLASSROOM DEMO SUMMARY")
    print("============================================================")
    print(f"Input:                    {video_path}")
    print(f"Resolution:               {orig_w}x{orig_h}")
    print(f"Video FPS:                {fps:.1f}")
    print(f"Duration:                 {duration_sec:.2f}s")
    print()
    print(f"Frames read:              {frame_idx}")
    print(f"Inference frames:         {inference_count}")
    print(f"Average inference time:   {avg_inf_time:.2f} ms")
    print(f"Average processing FPS:   {avg_proc_fps:.2f} FPS")
    print()
    print(f"Maximum persons detected: {max_persons_detected}")
    print(f"Configured seats:         {len(seat_mgr.seats)}")
    print()
    print("Signals detected (Behavioral Episodes):")
    for sig_name, stats in signal_tracker.items():
        print(f"  - {sig_name}: {stats['active_frames']} active frames, {stats['episodes']} episodes")
        print(f"      => Active duration: {stats['total_duration']}s total, max {stats['max_duration']}s, avg {stats['avg_duration']}s/ep")
    print()
    print("Peak risk per seat:")
    for s_code, pdata in peak_risks.items():
        print(f"  - {s_code}: Peak Risk: {pdata['peak_risk_score']:.1f}/100 | Final State: {pdata['final_state']}")
    print()
    print(f"Events generated:         {len(all_emitted_events)}")
    print(f"State transitions:        {len(active_state_transitions)}")
    print(f"Output video:             {output_video}")
    print("============================================================\n")

    return summary_data


def main():
    parser = argparse.ArgumentParser(description="VIGIL AI Classroom End-to-End Demo")
    parser.add_argument("--input", "--video", dest="input_video", type=str, default="demo_video/india_classroom.mp4", help="Path to input demo video")
    parser.add_argument("--output", type=str, default=None, help="Path to output annotated MP4")
    parser.add_argument("--output-dir", type=str, default="data/output_demo", help="Directory for output files")
    parser.add_argument("--room-id", type=str, default="ROOM-101", help="Exam room identifier")
    parser.add_argument("--camera-id", type=str, default="CAM-01", help="Camera feed identifier")
    parser.add_argument("--no-show", action="store_true", help="Disable realtime annotated window (runs headlessly)")
    parser.add_argument("--show", "--preview", dest="show", action="store_true", help="Explicitly enable realtime annotated window")
    parser.add_argument("--debug", action="store_true", help="Enable debug overlay (anchors, keypoint conf, detailed tags)")
    parser.add_argument("--show-pose", action="store_true", help="Render full pose skeleton overlay")
    parser.add_argument("--save-events", action="store_true", default=True, help="Save events.json")
    parser.add_argument("--save-evidence", action="store_true", default=True, help="Generate async evidence packages")
    parser.add_argument("--max-frames", type=int, default=None, help="Maximum frames to process (optional)")
    parser.add_argument("--stride", type=int, default=1, help="Frame subsampling stride (default 1 = every frame)")
    args = parser.parse_args()

    out_video = args.output
    if not out_video:
        out_video = str(Path(args.output_dir) / "india_classroom_result.mp4")

    # If --no-show is set, show_window is False; otherwise True (default GUI on)
    show_win = False if args.no_show else True

    run_classroom_demo(
        input_video=args.input_video,
        output_video=out_video,
        room_id=args.room_id,
        camera_id=args.camera_id,
        show_window=show_win,
        save_events=args.save_events,
        save_evidence=args.save_evidence,
        max_frames=args.max_frames,
        stride=args.stride,
        debug=args.debug,
        show_pose=args.show_pose,
    )


if __name__ == "__main__":
    main()
