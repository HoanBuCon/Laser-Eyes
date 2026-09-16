"""End-to-End Real Video Demonstration & Verification Script for VIGIL AI SRS v2.0.

Implements the complete 7-Layer Actor-Centric Temporal Architecture:
1. Video Ingestion & Timestamping (ms timestamps, frame-rate independent).
2. Single-Pass 1280px YOLO-Pose Perception.
3. Seat ROI Polygon Mapping & Seat Identity Persistence.
4. SceneContext, SeatGraph & Capability Gating.
5. Raw Observation Extraction (Head Yaw/Pitch Relative, Torso Lean, Writing Zone Suppression, Unknown-Safe).
6. Temporal Episode Engine (Stateful Hysteresis, Durations in ms).
7. Contextual & Relational Pattern Engine (P0 Patterns: REPEATED_NEIGHBOR_GLANCE, NEIGHBOR_ORIENTED_LEAN, SEAT_LEFT, MULTI_PERSON_DWELL_NEAR_SEAT; P1 BELOW_DESK_INTERACTION).
8. Risk Prioritization & Canonical State Machine (Exponential Decay, Correlation Bonus, Diminishing Returns, Canonical States, NO 'CHEATING').
9. Async Evidence Buffering, Snapshot Extraction, EOF Flush & SHA-256 Digest.
10. Compact, Non-Polluting HUD Overlay & Full Multi-Metric Export (data/output_demo_v2/).
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
from classroom_monitor.behavior_pattern_engine import BehaviorPattern, BehaviorPatternEngine, PatternType
from classroom_monitor.config import ClassroomConfig, DEFAULT_CONFIG
from classroom_monitor.detector import PoseClassroomDetector
from classroom_monitor.models import ClassroomEvent, Detection
from classroom_monitor.observation_extractor import ObservationExtractor, RawObservation
from classroom_monitor.scene_context import CapabilityStatus, DeskGeometry, SeatContext, SeatGraph
from classroom_monitor.seat_manager import SeatDefinition, SeatManager, SeatState
from classroom_monitor.seat_risk_tracker import RiskState, SeatRiskTracker
from classroom_monitor.temporal_episode_engine import EpisodeState, EpisodeType, TemporalEpisode, TemporalEpisodeEngine
from classroom_monitor.video_buffer import EvidenceVideoBuffer
from storage.database import SessionLocal, init_db
from storage.repositories import CameraRepository, RoomRepository, SeatRepository, SiteRepository

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ClassroomDemoV2")

# Pre-calibrated Seat ROIs for demo_video/india_classroom.mp4 (1280x720)
DEFAULT_CALIBRATED_SEATS = [
    {
        "seat_code": "SEAT-101-01",
        "seat_label": "Bàn 1 Dãy Trái (Hàng 1)",
        "polygon_json": [[92.0, 390.0], [299.0, 390.0], [299.0, 568.0], [92.0, 568.0]],
        "desk_y": 480.0,
        "enabled": True,
    },
    {
        "seat_code": "SEAT-101-02",
        "seat_label": "Bàn 1 Dãy Giữa (Hàng 1)",
        "polygon_json": [[484.0, 485.0], [702.0, 485.0], [702.0, 651.0], [484.0, 651.0]],
        "desk_y": 560.0,
        "enabled": True,
    },
    {
        "seat_code": "SEAT-101-03",
        "seat_label": "Bàn 1 Dãy Phải (Hàng 1)",
        "polygon_json": [[978.0, 413.0], [1168.0, 413.0], [1168.0, 682.0], [978.0, 682.0]],
        "desk_y": 550.0,
        "enabled": True,
    },
    {
        "seat_code": "SEAT-101-04",
        "seat_label": "Bàn 2 Dãy Trái (Hàng 2)",
        "polygon_json": [[190.0, 283.0], [382.0, 283.0], [382.0, 418.0], [190.0, 418.0]],
        "desk_y": 350.0,
        "enabled": True,
    },
    {
        "seat_code": "SEAT-101-05",
        "seat_label": "Bàn 2 Dãy Giữa (Hàng 2)",
        "polygon_json": [[470.0, 324.0], [650.0, 324.0], [650.0, 502.0], [470.0, 502.0]],
        "desk_y": 410.0,
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
    """Execute end-to-end VIGIL AI SRS v2.0 pipeline on input video."""
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
    print("[START] VIGIL AI SRS v2.0 ACTOR-CENTRIC TEMPORAL PIPELINE DEMO")
    print("================================================================================")
    print(f"Input Video:      {video_path} ({orig_w}x{orig_h} @ {fps:.1f} FPS, {duration_sec:.1f}s, {total_video_frames} frames)")
    print(f"Room & Camera:    Room: {room_id} | Camera: {camera_id}")

    # 1. Setup Production Modules
    seat_defs = setup_database_seats(room_id=room_id, camera_id=camera_id)
    seat_mgr = SeatManager(room_id=room_id, camera_id=camera_id)
    seat_mgr.load_seats(seat_defs)
    seat_graph = seat_mgr.to_seat_graph()
    print(f"SeatManager:      Loaded {len(seat_mgr.seats)} production Seat ROIs into SeatGraph.")

    # 2. Perception & 7-Layer Pipeline Modules
    config = ClassroomConfig(pipeline_mode="2stage_pose", enable_sahi_tiling=False)
    detector = PoseClassroomDetector(config=config)
    observation_extractor = ObservationExtractor()
    episode_engine = TemporalEpisodeEngine()
    pattern_engine = BehaviorPatternEngine(seat_graph=seat_graph)
    risk_tracker = SeatRiskTracker(room_id=room_id)

    # 3. Evidence Subsystem
    out_dir = Path(output_video).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir = out_dir / "evidence"
    if save_evidence:
        evidence_dir.mkdir(parents=True, exist_ok=True)

    video_buffer = EvidenceVideoBuffer(
        pre_event_seconds=5.0,
        post_event_seconds=5.0,
        fps=fps,
        output_dir=evidence_dir,
    )
    async_writer = AsyncEvidenceWriter(base_evidence_dir=evidence_dir, max_workers=2)

    # Video Writer
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    video_writer = cv2.VideoWriter(str(output_video), fourcc, fps / stride, (orig_w, orig_h))

    # Metric & Telemetry Tracking
    frame_idx = 0
    inference_count = 0
    inference_times: List[float] = []
    max_persons_detected = 0

    all_emitted_events: List[ClassroomEvent] = []
    all_active_episodes: List[TemporalEpisode] = []
    all_detected_patterns: List[BehaviorPattern] = []
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

            # Step 1: Add frame to Evidence Video Buffer
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

            # Step 2: Person / Pose Perception (Single-Pass)
            inf_start = time.perf_counter()
            detections = detector.detect(frame, frame_index=frame_idx)
            inf_time = (time.perf_counter() - inf_start) * 1000.0
            inference_times.append(inf_time)
            inference_count += 1
            max_persons_detected = max(max_persons_detected, len(detections))

            # Step 3: Seat ROI Mapping & Identity Persistence
            mapped_seats, unmapped_dets = seat_mgr.map_detections_to_seats(
                detections=detections,
                timestamp_ms=video_time_ms,
                frame_idx=frame_idx,
            )

            # Step 4: Extract Raw Observations & Temporal Episodes per Seat
            current_frame_episodes: List[TemporalEpisode] = []
            current_frame_patterns: List[BehaviorPattern] = []

            for seat_code, det in mapped_seats.items():
                s_ctx = seat_graph.get_context(seat_code)
                if not s_ctx:
                    s_ctx = SeatContext(seat_id=seat_code, room_id=room_id, seat_code=seat_code)
                    seat_graph.add_seat_context(s_ctx)

                # Extract Raw Observations
                raw_obs = observation_extractor.extract(
                    detection=det,
                    seat_context=s_ctx,
                    timestamp_ms=video_time_ms,
                    nearby_person_count=1 if det is not None else 0,
                )

                # Temporal Episode Engine
                active_eps = episode_engine.process_observations(
                    observations=raw_obs,
                    timestamp_ms=video_time_ms,
                )
                current_frame_episodes.extend(active_eps)

                # Contextual & Relational Pattern Engine
                patterns = pattern_engine.ingest_episodes(
                    active_episodes=active_eps,
                    completed_episodes=episode_engine.completed_episodes,
                    seat_context=s_ctx,
                    timestamp_ms=video_time_ms,
                )
                current_frame_patterns.extend(patterns)

                # Risk Prioritization & State Machine Update
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
                    print(f"[{t_str}] {seat_code}: {prev_st} -> {prof.current_state} (Risk: {prof.risk_score:.1f})")
                    active_state_transitions.append({
                        "timestamp_ms": video_time_ms,
                        "seat_code": seat_code,
                        "from_state": prev_st,
                        "to_state": prof.current_state,
                        "risk_score": round(prof.risk_score, 1),
                    })
                    prev_states[seat_code] = prof.current_state

                # Handle New Review Events
                if new_event:
                    all_emitted_events.append(new_event)
                    t_str = f"{int(video_time_ms/1000//60):02d}:{int(video_time_ms/1000%60):02d}.{int(video_time_ms%1000/100):01d}"
                    print(f"[{t_str}] [EVENT FLAGGED] {seat_code} | Pattern: {new_event.behavior} | Severity: {new_event.severity} | Score: {new_event.metadata.get('risk_score', 0)}")

                    # Trigger Video Clip Recording
                    if save_evidence:
                        video_buffer.trigger_clip(
                            event_id=new_event.event_id,
                            track_id=new_event.track_id,
                            behavior=new_event.behavior,
                            frame_idx=frame_idx,
                            timestamp_ms=video_time_ms,
                        )

            # Store cumulative episodes & patterns for export
            for ep in current_frame_episodes:
                if ep not in all_active_episodes:
                    all_active_episodes.append(ep)
            for pat in current_frame_patterns:
                if pat not in all_detected_patterns:
                    all_detected_patterns.append(pat)

            # Step 5: Render Clean, Compact SRS v2 HUD
            annotated = frame.copy()

            # (a) Draw Calibrated Seat Polygons & Status Badges
            for s_code, s_def in seat_mgr.seats.items():
                poly = s_def.polygon.astype(np.int32)
                prof = risk_tracker.get_or_create_profile(s_code)
                det = mapped_seats.get(s_code)

                # State color palette
                if prof.current_state == RiskState.FLAGGED_FOR_REVIEW.value:
                    color = (0, 0, 240)        # Red
                    fill_color = (0, 0, 180)
                elif prof.current_state == RiskState.SUSPICIOUS.value:
                    color = (0, 140, 255)      # Orange
                    fill_color = (0, 100, 200)
                elif prof.current_state == RiskState.OBSERVE.value:
                    color = (0, 220, 255)      # Amber / Yellow
                    fill_color = (0, 160, 200)
                elif prof.current_state == RiskState.COOLDOWN.value:
                    color = (255, 100, 100)    # Blue-violet Cooldown
                    fill_color = (180, 70, 70)
                else:
                    color = (0, 200, 100)      # Green Normal
                    fill_color = (0, 120, 50)

                # Draw Seat Boundary
                cv2.polylines(annotated, [poly], isClosed=True, color=color, thickness=2)

                # Draw Desk Boundary if calibrated
                if s_def.desk_y is not None:
                    min_x = int(np.min(poly[:, 0]))
                    max_x = int(np.max(poly[:, 0]))
                    dy = int(s_def.desk_y)
                    cv2.line(annotated, (min_x, dy), (max_x, dy), (0, 255, 255), 1, cv2.LINE_AA)

                # Centroid for Badge
                scx = int(np.mean(poly[:, 0]))
                scy = int(np.mean(poly[:, 1]))

                # Main Badge Text: Compact "SEAT | STATE | RISK"
                badge_text = f"{s_code} | {prof.current_state} | {prof.risk_score:.0f}"
                (tw, th), _ = cv2.getTextSize(badge_text, cv2.FONT_HERSHEY_SIMPLEX, 0.42, 1)
                bx = max(5, scx - tw // 2)
                by = max(40, scy - 12)

                cv2.rectangle(annotated, (bx - 4, by - th - 4), (bx + tw + 4, by + 4), fill_color, -1)
                cv2.rectangle(annotated, (bx - 4, by - th - 4), (bx + tw + 4, by + 4), (255, 255, 255), 1)
                cv2.putText(annotated, badge_text, (bx, by), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1, cv2.LINE_AA)

                # Secondary Subtitle: Active Primary Pattern (if any)
                active_seat_pats = [p for p in current_frame_patterns if p.seat_id == s_code]
                if active_seat_pats:
                    sub_pat = active_seat_pats[0].pattern_type
                    (spw, sph), _ = cv2.getTextSize(sub_pat, cv2.FONT_HERSHEY_SIMPLEX, 0.35, 1)
                    cv2.rectangle(annotated, (scx - spw//2 - 3, scy + 4), (scx + spw//2 + 3, scy + sph + 8), (15, 23, 42), -1)
                    cv2.putText(annotated, sub_pat, (scx - spw//2, scy + sph + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 255), 1, cv2.LINE_AA)

                if det is not None:
                    x1, y1, x2, y2 = [int(v) for v in det.bbox]
                    cv2.rectangle(annotated, (x1, y1), (x2, y2), (255, 180, 0), 2 if debug else 1)
                    if (show_pose or debug) and det.keypoints is not None:
                        draw_skeletons(annotated, det.keypoints)

            # (b) Draw Unmapped Persons (Explicitly tagged UNMAPPED_PERSON, never PROCTOR)
            for udet in unmapped_dets:
                ux1, uy1, ux2, uy2 = [int(v) for v in udet.bbox]
                cv2.rectangle(annotated, (ux1, uy1), (ux2, uy2), (100, 116, 139), 1)
                unmapped_label = "UNMAPPED_PERSON"
                (utw, uth), _ = cv2.getTextSize(unmapped_label, cv2.FONT_HERSHEY_SIMPLEX, 0.36, 1)
                cv2.rectangle(annotated, (ux1, max(0, uy1 - uth - 4)), (ux1 + utw + 4, uy1), (30, 41, 59), -1)
                cv2.putText(annotated, unmapped_label, (ux1 + 2, uy1 - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.36, (148, 163, 184), 1, cv2.LINE_AA)

            # (c) Top Telemetry Header
            cv2.rectangle(annotated, (0, 0), (orig_w, 38), (15, 23, 42), -1)
            cv2.line(annotated, (0, 38), (orig_w, 38), (0, 255, 200), 1)

            time_str = f"{int(video_time_ms/1000//60):02d}:{int(video_time_ms/1000%60):02d}.{int(video_time_ms%1000/100):01d}"
            mapped_count = sum(1 for d in mapped_seats.values() if d is not None)
            hud_text = (
                f"VIGIL AI SRS v2.0 | Room: {room_id} | Time: {time_str} | "
                f"Frame: {frame_idx}/{total_video_frames} | "
                f"Seats: {mapped_count}/{len(seat_mgr.seats)} | "
                f"Unmapped: {len(unmapped_dets)} | "
                f"Events: {len(all_emitted_events)}"
            )
            cv2.putText(annotated, hud_text, (14, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.50, (255, 255, 255), 1, cv2.LINE_AA)

            # Save representative verification frames
            if frame_idx == 350 and not (out_dir / "normal_writing_sample.jpg").exists():
                cv2.imwrite(str(out_dir / "normal_writing_sample.jpg"), annotated)
            if frame_idx == 280 and not (out_dir / "repeated_glance_sample.jpg").exists():
                cv2.imwrite(str(out_dir / "repeated_glance_sample.jpg"), annotated)
            if frame_idx == 520 and not (out_dir / "body_lean_sample.jpg").exists():
                cv2.imwrite(str(out_dir / "body_lean_sample.jpg"), annotated)
            if len(unmapped_dets) > 0 and not (out_dir / "unmapped_person_sample.jpg").exists():
                cv2.imwrite(str(out_dir / "unmapped_person_sample.jpg"), annotated)

            # Write Frame to Video
            video_writer.write(annotated)
            last_annotated_frame = annotated

            if show_window:
                cv2.imshow("VIGIL AI SRS v2.0 DEMO", annotated)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    print("[INFO] User interrupted the demo.")
                    break

            if frame_idx % 60 == 0:
                print(f"[{time_str}] Processed {frame_idx}/{total_video_frames} frames (Inf: {inf_time:.1f}ms | Persons: {len(detections)} | Events: {len(all_emitted_events)})")

    finally:
        cap.release()
        video_writer.release()
        if show_window:
            cv2.destroyAllWindows()

    # Step 6: Flush all pending evidence buffer jobs (EOF Protection)
    print("[SHUTDOWN] Flushing pending evidence video buffer at EOF...")
    flushed_clips = video_buffer.flush_all()
    async_writer.shutdown(wait=True)
    print(f"[SHUTDOWN] Flushed {len(flushed_clips)} pending evidence clips successfully.")

    # Save last annotated frame
    if last_annotated_frame is not None:
        sample_img_path = out_dir / "india_classroom_annotated_sample.jpg"
        cv2.imwrite(str(sample_img_path), last_annotated_frame)
        print(f"[OK] Representative annotated frame saved: {sample_img_path}")

    # Compute Peak Risks
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

    # Save episodes.json
    episodes_json_path = out_dir / "episodes.json"
    episodes_dicts = [ep.to_dict() for ep in (episode_engine.completed_episodes + all_active_episodes)]
    with open(episodes_json_path, "w", encoding="utf-8") as f:
        json.dump(episodes_dicts, f, indent=2)

    # Save patterns.json
    patterns_json_path = out_dir / "patterns.json"
    patterns_dicts = [pat.to_dict() for pat in all_detected_patterns]
    with open(patterns_json_path, "w", encoding="utf-8") as f:
        json.dump(patterns_dicts, f, indent=2)

    # Save demo_summary.json
    total_elapsed = time.time() - start_wall_time
    avg_inf_time = float(np.mean(inference_times)) if inference_times else 0.0
    avg_proc_fps = frame_idx / total_elapsed if total_elapsed > 0 else 0.0

    summary_data = {
        "srs_version": "2.0.0",
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
        "total_episodes_extracted": len(episodes_dicts),
        "total_patterns_detected": len(patterns_dicts),
        "events_generated_count": len(all_emitted_events),
        "state_transitions_count": len(active_state_transitions),
        "peak_risk_per_seat": peak_risks,
        "output_video": str(output_video),
    }

    summary_json_path = out_dir / "demo_summary.json"
    with open(summary_json_path, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=2)

    # Print Final Summary Table
    print("\n============================================================")
    print("VIGIL AI SRS v2.0 — INDIA CLASSROOM DEMO SUMMARY")
    print("============================================================")
    print(f"Input:                    {video_path}")
    print(f"Resolution:               {orig_w}x{orig_h}")
    print(f"Video FPS:                {fps:.1f}")
    print(f"Duration:                 {duration_sec:.2f}s")
    print(f"Total Frames:             {frame_idx}")
    print(f"Average Inference Time:   {avg_inf_time:.2f} ms")
    print(f"Average Processing FPS:   {avg_proc_fps:.2f} FPS")
    print(f"Configured Seats:         {len(seat_mgr.seats)}")
    print(f"Total Episodes:           {len(episodes_dicts)}")
    print(f"Total Patterns:           {len(patterns_dicts)}")
    print(f"Events Emitted:           {len(all_emitted_events)}")
    print(f"State Transitions:        {len(active_state_transitions)}")
    print(f"Output Video:             {output_video}")
    print("============================================================\n")

    return summary_data


def main():
    parser = argparse.ArgumentParser(description="VIGIL AI SRS v2.0 Classroom End-to-End Demo")
    parser.add_argument("--input", "--video", dest="input_video", type=str, default="demo_video/india_classroom.mp4", help="Path to input demo video")
    parser.add_argument("--output", type=str, default=None, help="Path to output annotated MP4")
    parser.add_argument("--output-dir", type=str, default="data/output_demo_v2", help="Directory for output files")
    parser.add_argument("--room-id", type=str, default="ROOM-101", help="Exam room identifier")
    parser.add_argument("--camera-id", type=str, default="CAM-01", help="Camera feed identifier")
    parser.add_argument("--no-show", action="store_true", help="Disable realtime annotated window (runs headlessly)")
    parser.add_argument("--show", "--preview", dest="show", action="store_true", help="Explicitly enable realtime annotated window")
    parser.add_argument("--debug", action="store_true", help="Enable debug overlay")
    parser.add_argument("--show-pose", action="store_true", help="Render full pose skeleton overlay")
    parser.add_argument("--save-events", action="store_true", default=True, help="Save events.json")
    parser.add_argument("--save-evidence", action="store_true", default=True, help="Generate async evidence packages")
    parser.add_argument("--max-frames", type=int, default=None, help="Maximum frames to process (optional)")
    parser.add_argument("--stride", type=int, default=1, help="Frame subsampling stride (default 1 = every frame)")
    args = parser.parse_args()

    out_video = args.output
    if not out_video:
        out_video = str(Path(args.output_dir) / "india_classroom_result.mp4")

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
