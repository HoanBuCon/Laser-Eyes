"""Core Pipeline Orchestrator for VIGIL AI SRS v2.0 Classroom Demonstration.

Executes the complete 7-Layer Actor-Centric Temporal Architecture:
1. Frame-rate Independent Ingestion & Millisecond Precision Timestamping.
2. Single-Pass HD YOLO-Pose Perception.
3. Seat ROI Polygon Mapping & Occlusion Coasting (4.0s).
4. Isolated Roaming Actor / Invigilator Filtering.
5. Batched 6DRepNet Head Orientation @ ~5Hz for Occupied Seats Only.
6. Per-Seat Baseline Subtraction & Median Smoothing Window (w=3).
7. Observation Extraction with Desk Geometry (Writing Zone vs Under-Desk).
8. Temporal Episode Engine with Dual Hysteresis (28°/16°), 400ms Min Persistence, 1200ms Grace.
9. SceneContext / SeatGraph Relational Pattern Engine.
10. Seat Risk Prioritization with Exponential Decay (1.8 pts/s) & Diminishing Returns.
11. High-Level Review Event Triggering with Strict Canonical Statuses (FLAGGED_FOR_REVIEW).
12. Async Evidence Video Buffering (10s MP4 + Peak Frame Snapshot + SHA-256 Digest).
13. HUD Telemetry Rendering & Standardized Multi-Artifact JSON Export.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import cv2
import numpy as np
import torch

from classroom_monitor.async_evidence_writer import AsyncEvidenceWriter, compute_file_sha256
from classroom_monitor.behavior_pattern_engine import BehaviorPattern, BehaviorPatternEngine, PatternType
from classroom_monitor.config import ClassroomConfig, DEFAULT_CONFIG, resolve_runtime_config
from classroom_monitor.demo.config import (
    DEMO_PRESETS,
    DemoVideoConfig,
    INDIA_CALIBRATED_SEATS,
    get_demo_config,
    resolve_video_path,
)
from classroom_monitor.demo.exporter import export_demo_artifacts
from classroom_monitor.demo.renderer import DemoHUDOverlayRenderer
from classroom_monitor.detector import PoseClassroomDetector
from classroom_monitor.head_pose_provider import (
    HeadCropExtractor,
    HeadOrientationEstimate,
    SixDRepNetHeadOrientationProvider,
    create_head_pose_provider,
)
from classroom_monitor.models import ClassroomEvent, Detection, EventStatus, SeverityLevel
from classroom_monitor.observation_extractor import ObservationExtractor, RawObservation
from classroom_monitor.scene_context import CapabilityStatus, DeskGeometry, SceneProfile, SeatContext, SeatGraph
from classroom_monitor.seat_manager import SeatDefinition, SeatManager, SeatState
from classroom_monitor.seat_risk_tracker import RiskState, SeatRiskTracker
from classroom_monitor.temporal_episode_engine import EpisodeState, EpisodeType, TemporalEpisode, TemporalEpisodeEngine
from classroom_monitor.video_buffer import EvidenceVideoBuffer
from storage.database import SessionLocal, init_db
from storage.db_models import Camera, ExamRoom, ExamSite, SeatROI

logger = logging.getLogger("VigilDemoRunner")


def setup_room_seats(
    room_id: str,
    camera_id: str,
    seats_preset: List[Dict[str, Any]],
    site_name: str = "Trường Đại học Demo",
) -> List[SeatDefinition]:
    """Ensure database has site, room, camera, and calibrated seats matching preset."""
    init_db()
    db = SessionLocal()

    site = db.query(ExamSite).first()
    if not site:
        site = ExamSite(name=site_name, address="Khu Đô thị")
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
        logger.info("Seeding %d calibrated Seat ROIs for %s into database", len(seats_preset), room_id)
        for s in seats_preset:
            ctx_dict = {"baseline_yaw": s.get("baseline_yaw", 0.0), "desk_y": s.get("desk_y")}
            poly_data = s.get("polygon_json") or s.get("polygon")
            seat_obj = SeatROI(
                room_id=room.id,
                camera_id=cam.id,
                seat_code=s["seat_code"],
                seat_label=s.get("seat_label") or s["seat_code"],
                polygon_json=json.dumps(poly_data) if not isinstance(poly_data, str) else poly_data,
                context_json=json.dumps(ctx_dict),
                enabled=True,
            )
            db.add(seat_obj)
        db.commit()
        existing_seats = db.query(SeatROI).filter(SeatROI.room_id == room.id, SeatROI.enabled == True).all()

    seat_defs: List[SeatDefinition] = []
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
                desk_y=json.loads(s.context_json).get("desk_y") if s.context_json else None,
            )
        )

    db.close()
    return seat_defs


def setup_database_seats(room_id: str = "ROOM-CALIB-01", camera_id: str = "CAM-CALIB-01") -> List[SeatDefinition]:
    """Compatibility helper for existing tests and diagnostic scripts."""
    return setup_room_seats(room_id=room_id, camera_id=camera_id, seats_preset=INDIA_CALIBRATED_SEATS)


def run_demo_pipeline(config: DemoVideoConfig) -> Dict[str, Any]:
    """Execute complete end-to-end VIGIL AI SRS v2.0 pipeline on a single video feed."""
    cap = cv2.VideoCapture(str(config.video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video stream: {config.video_path}")

    orig_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    orig_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration_sec = total_frames / fps if fps > 0 else 0.0

    out_p = Path(config.output_dir)
    out_p.mkdir(parents=True, exist_ok=True)
    evidence_dir = out_p / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    # Clean output isolation: purge stale generated demo artifacts
    for stale_pattern in ("*.json", "*.mp4", "*.csv"):
        for stale_file in out_p.glob(stale_pattern):
            try:
                stale_file.unlink()
            except OSError:
                pass
    if evidence_dir.exists():
        for stale_ev in evidence_dir.glob("*"):
            if stale_ev.is_file():
                try:
                    stale_ev.unlink()
                except OSError:
                    pass

    print("\n" + "=" * 80)
    print(f" VIGIL AI SRS v2.0 - EXECUTING DEMO PIPELINE: {config.name.upper()}")
    print("=" * 80)
    print(f" Input Video:     {config.video_path} ({orig_w}x{orig_h} @ {fps:.1f} FPS, {duration_sec:.1f}s, {total_frames} frames)")
    print(f" Room Context:    {config.room_code} | Cam: {config.camera_id}")
    print(f" Head Provider:   {config.head_provider} @ {config.hpe_hz:.1f} Hz | Imgsz: {config.pose_imgsz}")
    print(f" Output Target:   {out_p.resolve()}")
    print(f" Save Evidence:   {config.save_evidence} | Show Window: {config.show_window}")

    # 1. Load SceneProfile or fallback Preset
    scene_profile: Optional[SceneProfile] = None
    seats_preset = config.seats_preset or []

    if config.scene_config_path and Path(config.scene_config_path).exists():
        logger.info("Loading Scene Profile from YAML: %s", config.scene_config_path)
        scene_profile = SceneProfile.from_file(config.scene_config_path)
        seat_graph = scene_profile.seat_graph
        if not seats_preset:
            seats_preset = []
            for s_code, ctx in seat_graph.seats_context.items():
                poly = ctx.metadata.get("polygon")
                if poly is None and config.scene_config_path:
                    for raw_s in scene_profile.raw_config.get("seats", []):
                        if raw_s.get("seat_code") == s_code:
                            poly = raw_s.get("polygon")
                            break
                seats_preset.append({
                    "seat_code": s_code,
                    "seat_label": ctx.metadata.get("seat_label") or s_code,
                    "polygon_json": poly or [],
                    "desk_y": ctx.desk_geometry.desk_boundary_y if ctx.desk_geometry else None,
                    "baseline_yaw": ctx.reference_directions.baseline_yaw,
                })
    else:
        logger.info("Using built-in Seat Preset for %s (%d seats)", config.room_code, len(seats_preset))
        seat_graph = SeatGraph(room_id=config.room_code)

    # 2. Setup Database Seats & SeatManager
    seat_defs = setup_room_seats(room_id=config.room_code, camera_id=config.camera_id, seats_preset=seats_preset)
    seat_mgr = SeatManager(room_id=config.room_code, camera_id=config.camera_id)
    seat_mgr.load_seats(seat_defs)

    if scene_profile is None:
        seat_graph = seat_mgr.to_seat_graph()
        for s in seats_preset:
            ctx = seat_graph.get_context(s["seat_code"])
            if ctx:
                ctx.reference_directions.baseline_yaw = s.get("baseline_yaw", 0.0)
                if s.get("desk_y"):
                    if ctx.desk_geometry is None:
                        ctx.desk_geometry = DeskGeometry(desk_boundary_y=s.get("desk_y"))
                    else:
                        ctx.desk_geometry.desk_boundary_y = s.get("desk_y")

    # 3. Resolve Effective Runtime Configuration
    runtime_cfg = resolve_runtime_config(scene_profile=scene_profile, demo_config=config)
    eff_cfg_path = out_p / "effective_runtime_config.json"
    with open(eff_cfg_path, "w", encoding="utf-8") as f:
        json.dump(runtime_cfg, f, indent=2)

    # 4. Initialize Pipeline Components
    detector = PoseClassroomDetector(
        confidence_threshold=config.pose_conf,
    )

    head_provider_inst = create_head_pose_provider(config.head_provider)
    obs_extractor = ObservationExtractor(head_pose_provider=head_provider_inst)
    episode_engine = TemporalEpisodeEngine(**runtime_cfg["temporal"])
    pattern_engine = BehaviorPatternEngine(seat_graph=seat_graph, **runtime_cfg["patterns"])
    risk_tracker = SeatRiskTracker(room_id=config.room_code, camera_id=config.camera_id, **runtime_cfg["risk"])

    evidence_buffer = EvidenceVideoBuffer(
        pre_event_seconds=5.0,
        post_event_seconds=5.0,
        fps=fps,
        output_dir=evidence_dir,
        async_write=True,
    )
    renderer = DemoHUDOverlayRenderer(debug_overlay=config.debug_overlay)

    # Video Writer for annotated result.mp4
    result_video_path = out_p / "result.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out_writer = cv2.VideoWriter(str(result_video_path), fourcc, fps / config.stride, (orig_w, orig_h))

    all_episodes: List[TemporalEpisode] = []
    all_events: List[ClassroomEvent] = []
    all_patterns: List[BehaviorPattern] = []

    # Head Pose Estimation Scheduler & Per-Seat Cache
    hpe_interval_ms = (1000.0 / config.hpe_hz) if config.hpe_hz > 0 else 200.0
    hpe_max_age_ms = runtime_cfg["head_pose"]["cache_max_age_ms"]
    scheduled_hpe_cycles = 0
    seat_hpe_cache: Dict[str, Tuple[HeadOrientationEstimate, float]] = {}
    last_hpe_time: Dict[str, float] = {}

    # Timing metrics accumulators
    t_det_list: List[float] = []
    t_hpe_list: List[float] = []
    t_temp_list: List[float] = []
    t_pat_list: List[float] = []
    t_risk_list: List[float] = []
    t_ren_list: List[float] = []
    t_write_list: List[float] = []

    frame_idx = 0
    processed_count = 0
    start_wall_time = time.time()

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            frame_idx += 1
            if config.max_frames and frame_idx > config.max_frames:
                break

            if config.stride > 1 and (frame_idx % config.stride != 0):
                continue

            processed_count += 1
            timestamp_ms = (frame_idx - 1) * (1000.0 / fps)

            # Buffer raw frame
            evidence_buffer.add_frame(frame, frame_idx=frame_idx, timestamp_ms=timestamp_ms)

            # Stage 1: YOLO-Pose Detection
            t0 = time.perf_counter()
            detections = detector.detect(frame, frame_index=frame_idx)
            t_det = (time.perf_counter() - t0) * 1000.0
            t_det_list.append(t_det)

            # Stage 2: Seat ROI Mapping & Roaming Person Isolation
            mapped_seats, unmapped_dets = seat_mgr.map_detections_to_seats(
                detections=detections,
                timestamp_ms=timestamp_ms,
                frame_idx=frame_idx,
            )

            # Stage 3: Scheduled Batched Head Orientation Estimation (GPU Tensor Batch)
            t0 = time.perf_counter()
            hpe_batch_requests: List[Dict[str, Any]] = []
            for seat_code, occ in seat_mgr.occupancies.items():
                if occ.state == SeatState.OCCUPIED and occ.assigned_detection is not None:
                    s_ctx = seat_graph.get_context(seat_code)
                    if s_ctx and s_ctx.capabilities.head_orientation != CapabilityStatus.DISABLED:
                        if (timestamp_ms - last_hpe_time.get(seat_code, -100000.0)) >= hpe_interval_ms:
                            hpe_batch_requests.append({
                                "seat_id": seat_code,
                                "keypoints": occ.assigned_detection.keypoints,
                                "bbox": occ.assigned_detection.bbox,
                                "seat_baseline_yaw": s_ctx.reference_directions.baseline_yaw,
                                "seat_baseline_pitch": s_ctx.reference_directions.baseline_pitch,
                            })
                            last_hpe_time[seat_code] = timestamp_ms

            if hpe_batch_requests:
                scheduled_hpe_cycles += 1
                batch_estimates = head_provider_inst.estimate_batch(requests=hpe_batch_requests, frame=frame)
                for s_id, est in batch_estimates.items():
                    seat_hpe_cache[s_id] = (est, timestamp_ms)

            # Stage 4: Observation Extraction for each Seat Context (Consuming Cached HPE)
            seat_observations: Dict[str, List[RawObservation]] = {}
            for seat_code, occ in seat_mgr.occupancies.items():
                s_ctx = seat_graph.get_context(seat_code)
                if s_ctx is None:
                    continue

                det = occ.assigned_detection
                precomputed_est: Optional[HeadOrientationEstimate] = None
                if seat_code in seat_hpe_cache:
                    cached_est, cached_ts = seat_hpe_cache[seat_code]
                    if (timestamp_ms - cached_ts) <= hpe_max_age_ms:
                        precomputed_est = cached_est
                    else:
                        precomputed_est = HeadOrientationEstimate(source="cache_expired", quality=0.0, yaw=None, pitch=None)
                else:
                    precomputed_est = HeadOrientationEstimate(source="not_scheduled", quality=0.0, yaw=None, pitch=None)

                obs_list = obs_extractor.extract(
                    detection=det,
                    seat_context=s_ctx,
                    timestamp_ms=timestamp_ms,
                    occupancy_state=occ.state,
                    nearby_person_count=len(occ.candidate_detections),
                    precomputed_head_estimate=precomputed_est,
                )
                seat_observations[seat_code] = obs_list
            t_hpe = (time.perf_counter() - t0) * 1000.0
            t_hpe_list.append(t_hpe)

            # Stage 5: Temporal Episode Engine Update
            t0 = time.perf_counter()
            active_this_frame: List[TemporalEpisode] = []
            for seat_code, obs_list in seat_observations.items():
                act_eps = episode_engine.process_observations(obs_list, timestamp_ms=timestamp_ms)
                active_this_frame.extend(act_eps)
            t_temp = (time.perf_counter() - t0) * 1000.0
            t_temp_list.append(t_temp)

            # Stage 6: Behavior Pattern Engine Update
            t0 = time.perf_counter()
            new_patterns: List[BehaviorPattern] = []
            for seat_code in seat_mgr.occupancies.keys():
                s_ctx = seat_graph.get_context(seat_code)
                if s_ctx:
                    pats = pattern_engine.ingest_episodes(
                        active_episodes=active_this_frame,
                        completed_episodes=episode_engine.completed_episodes,
                        seat_context=s_ctx,
                        timestamp_ms=timestamp_ms,
                    )
                    new_patterns.extend(pats)
            all_patterns.extend(new_patterns)
            t_pat = (time.perf_counter() - t0) * 1000.0
            t_pat_list.append(t_pat)

            # Stage 7: Seat Risk Prioritization Tracker & Incident Event Generation
            t0 = time.perf_counter()
            for seat_code, occ in seat_mgr.occupancies.items():
                s_ctx = seat_graph.get_context(seat_code)
                evt = risk_tracker.update_seat(
                    seat_id=seat_code,
                    active_episodes=active_this_frame,
                    detected_patterns=new_patterns,
                    timestamp_ms=timestamp_ms,
                    detection=occ.assigned_detection,
                    frame_image=frame,
                    camera_id=config.camera_id,
                    seat_context=s_ctx,
                )
                if evt is not None:
                    all_events.append(evt)
                    if config.save_evidence:
                        evidence_buffer.trigger_clip(
                            event_id=evt.event_id,
                            track_id=evt.track_id,
                            behavior=evt.behavior,
                            frame_idx=frame_idx,
                            timestamp_ms=timestamp_ms,
                        )
                        snap_p = evidence_dir / f"{evt.event_id}_snapshot.jpg"
                        cv2.imwrite(str(snap_p), frame)
                        evt.evidence_path = str(snap_p)
            t_risk = (time.perf_counter() - t0) * 1000.0
            t_risk_list.append(t_risk)

            # Stage 8: HUD Rendering
            t0 = time.perf_counter()
            current_fps = processed_count / max(0.001, (time.time() - start_wall_time))
            runtime_metrics = {
                "lat_perception_ms": t_det,
                "lat_6drepnet_ms": t_hpe,
                "lat_temporal_ms": t_temp,
                "lat_pat_ms": t_pat,
                "lat_risk_ms": t_risk,
                "lat_render_ms": t_ren_list[-1] if t_ren_list else 0.0,
                "lat_total_ms": t_det + t_hpe + t_temp + t_pat + t_risk,
            }

            annotated_frame = renderer.render_frame(
                frame=frame,
                frame_idx=frame_idx,
                timestamp_ms=timestamp_ms,
                fps=current_fps,
                room_code=config.room_code,
                camera_id=config.camera_id,
                seat_mgr=seat_mgr,
                seat_graph=seat_graph,
                risk_tracker=risk_tracker,
                active_episodes=active_this_frame,
                recent_events=all_events,
                raw_observations=seat_observations,
                detections=detections,
                roaming_detections=unmapped_dets,
                runtime_metrics=runtime_metrics,
            )
            t_ren = (time.perf_counter() - t0) * 1000.0
            t_ren_list.append(t_ren)

            # Stage 9: Video Writer Output & Display Window
            t0 = time.perf_counter()
            out_writer.write(annotated_frame)
            t_write = (time.perf_counter() - t0) * 1000.0
            t_write_list.append(t_write)

            if config.show_window:
                cv2.imshow("VIGIL AI SRS v2.0 Demonstration", annotated_frame)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q") or key == 27:
                    logger.info("Demo interrupted by user keypress 'q'")
                    break
                elif key == ord("d") or key == ord("D"):
                    renderer.debug_overlay = not renderer.debug_overlay
                    logger.info("Toggled debug overlay: %s", renderer.debug_overlay)
                elif key == ord(" "):
                    logger.info("Demo paused (press Space to resume)...")
                    while True:
                        k2 = cv2.waitKey(50) & 0xFF
                        if k2 == ord(" ") or k2 == ord("q") or k2 == 27:
                            break

            if processed_count % 100 == 0 or processed_count == total_frames:
                pct = (processed_count / max(1, total_frames)) * 100.0
                elapsed_w = time.time() - start_wall_time
                logger.info(
                    "Frame %d/%d (%.1f%%) | Time: %.1fs | FPS: %.1f | Completed EPs: %d | Events: %d",
                    frame_idx,
                    total_frames,
                    pct,
                    elapsed_w,
                    current_fps,
                    len(episode_engine.completed_episodes),
                    len(all_events),
                )

    finally:
        cap.release()
        out_writer.release()
        if config.show_window:
            cv2.destroyAllWindows()

    final_timestamp_ms = (frame_idx) * (1000.0 / fps)

    # 4. EOF Flush: Flush remaining active episodes, patterns, and evidence buffers
    logger.info("Flushing final active episodes and async evidence writers at EOF (ts=%.1fms)...", final_timestamp_ms)
    flushed_eps = episode_engine.flush_all(timestamp_ms=final_timestamp_ms)
    all_episodes = list(episode_engine.completed_episodes)
    evidence_buffer.flush_all()

    # Compute SHA-256 for saved clips
    for evt in all_events:
        safe_behavior = evt.behavior.replace(" ", "_")
        expected_clip = evidence_dir / f"{evt.event_id}_T{evt.track_id}_{safe_behavior}.mp4"
        if expected_clip.exists():
            evt.evidence_video_path = str(expected_clip)
            evt.metadata["sha256_hash"] = compute_file_sha256(expected_clip)

    total_wall_time = time.time() - start_wall_time
    overall_fps = processed_count / max(0.001, total_wall_time)
    realtime_factor = (duration_sec / max(0.001, total_wall_time))

    # 5. Strict Canonical Ground Truth Benchmark Comparison (Actor-Centric)
    benchmark_summary: Optional[Dict[str, Any]] = None
    if config.gt_path and Path(config.gt_path).exists():
        try:
            import csv
            from classroom_monitor.evaluation.temporal_matcher import match_temporal_episodes
            with open(config.gt_path, "r", encoding="utf-8") as f:
                gt_data = json.load(f)

            gt_episodes = gt_data.get("episodes", [])
            eval_res = match_temporal_episodes(
                gt_episodes=gt_episodes,
                ai_episodes=all_episodes,
                iou_threshold=0.30,
                require_same_seat=True,
                require_same_label=True,
                exclude_malformed_gt=True,
                filter_label_substring="HEAD_TURN",
            )
            benchmark_summary = eval_res.to_dict()

            # Export gt_comparison_strict.csv
            gt_csv_p = out_p / "gt_comparison_strict.csv"
            with open(gt_csv_p, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "gt_id", "gt_seat", "gt_label", "gt_start_ms", "gt_end_ms",
                    "ai_episode_id", "ai_seat", "ai_label", "ai_start_ms", "ai_end_ms",
                    "temporal_iou", "match_status"
                ])
                for pair in eval_res.matched_pairs:
                    writer.writerow([
                        pair["gt_id"], pair["gt_seat"], pair["gt_label"], pair["gt_start_ms"], pair["gt_end_ms"],
                        pair["ai_id"], pair["ai_seat"], pair["ai_label"], pair["ai_start_ms"], pair["ai_end_ms"],
                        pair["temporal_iou"], "TP"
                    ])
                for un_gt in eval_res.unmatched_gt:
                    writer.writerow([
                        un_gt["id"], un_gt["seat_code"], un_gt["label"], un_gt["start_ms"], un_gt["end_ms"],
                        "", "", "", "", "",
                        0.0, "FN"
                    ])

            # Export ai_unmatched_head_episodes.csv
            ai_fp_p = out_p / "ai_unmatched_head_episodes.csv"
            with open(ai_fp_p, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["ai_episode_id", "ai_seat", "ai_label", "ai_start_ms", "ai_end_ms", "match_status"])
                for un_ai in eval_res.unmatched_ai:
                    writer.writerow([
                        un_ai["id"], un_ai["seat_code"], un_ai["label"], un_ai["start_ms"], un_ai["end_ms"], "FP"
                    ])

            logger.info(
                "Ground Truth Benchmark [%s] (Strict: Same Seat + Label + IoU>=0.30): Valid GT=%d, AI=%d, TP=%d, FP=%d, FN=%d, Prec=%.1f%%, Rec=%.1f%%, Avg IoU=%.3f",
                config.name,
                eval_res.valid_gt_episodes,
                eval_res.ai_episodes_evaluated,
                eval_res.tp,
                eval_res.fp,
                eval_res.fn,
                eval_res.precision,
                eval_res.recall,
                eval_res.avg_tp_iou,
            )
        except Exception as e:
            logger.warning("Failed to evaluate ground truth benchmark: %s", e, exc_info=True)

    # 6. Collect HPE Telemetry
    hpe_telemetry = head_provider_inst.get_telemetry()
    hpe_telemetry["target_hz"] = config.hpe_hz
    hpe_telemetry["scheduled_cycles"] = scheduled_hpe_cycles
    hpe_telemetry["effective_hpe_hz"] = round(hpe_telemetry["estimate_batch_calls"] / max(0.01, duration_sec), 2)
    occ_seats_count = max(1, sum(1 for occ in seat_mgr.occupancies.values() if occ.state == SeatState.OCCUPIED))
    hpe_telemetry["effective_hpe_hz_per_seat"] = round((hpe_telemetry["valid_head_crops"] / occ_seats_count) / max(0.01, duration_sec), 2)

    # 7. Export All Standardized Demo Artifacts
    scene_meta = {
        "scene_id": config.name,
        "room_code": config.room_code,
        "camera_id": config.camera_id,
        "video_file": str(config.video_path),
        "duration_sec": duration_sec,
        "fps": fps,
        "total_seats": len(seat_mgr.seats),
        "occupied_seats": sum(1 for occ in seat_mgr.occupancies.values() if occ.state == SeatState.OCCUPIED),
    }

    runtime_stats = {
        "total_frames": processed_count,
        "wall_time_sec": total_wall_time,
        "overall_fps": overall_fps,
        "realtime_factor": realtime_factor,
        "head_pose_runtime": hpe_telemetry,
        "lat_perception_avg_ms": float(np.mean(t_det_list)) if t_det_list else 0.0,
        "lat_6drepnet_avg_ms": float(np.mean(t_hpe_list)) if t_hpe_list else 0.0,
        "lat_temporal_avg_ms": float(np.mean(t_temp_list)) if t_temp_list else 0.0,
        "lat_pattern_avg_ms": float(np.mean(t_pat_list)) if t_pat_list else 0.0,
        "lat_risk_avg_ms": float(np.mean(t_risk_list)) if t_risk_list else 0.0,
        "lat_render_avg_ms": float(np.mean(t_ren_list)) if t_ren_list else 0.0,
        "lat_writer_avg_ms": float(np.mean(t_write_list)) if t_write_list else 0.0,
    }

    exported_files = export_demo_artifacts(
        output_dir=out_p,
        episodes=all_episodes,
        events=all_events,
        patterns=all_patterns,
        risk_tracker=risk_tracker,
        scene_metadata=scene_meta,
        runtime_stats=runtime_stats,
        benchmark_summary=benchmark_summary,
    )

    print("\n" + "=" * 80)
    print(f" DEMO RUN COMPLETE: {config.name.upper()} in {total_wall_time:.1f}s ({overall_fps:.1f} FPS, {realtime_factor:.2f}x Realtime)")
    print(f" Output Video:    {result_video_path}")
    print(f" Canonical EPs:   {exported_files['episodes']} ({len(all_episodes)} episodes)")
    print(f" Review Events:   {exported_files['events']} ({len(all_events)} events)")
    print(f" Demo Summary:    {exported_files['summary']}")
    print(f" Runtime Profile: {exported_files['runtime']}")
    print("=" * 80 + "\n")

    return {
        "name": config.name,
        "video_path": str(config.video_path),
        "result_video": str(result_video_path),
        "total_frames": processed_count,
        "duration_sec": duration_sec,
        "wall_time_sec": total_wall_time,
        "overall_fps": overall_fps,
        "realtime_factor": realtime_factor,
        "total_episodes": len(all_episodes),
        "total_events": len(all_events),
        "total_patterns": len(all_patterns),
        "exported_files": {k: str(v) for k, v in exported_files.items()},
        "benchmark": benchmark_summary,
    }


def run_classroom_demo(
    video_path: str = "demo_video/india_classroom.mp4",
    output_video_path: Optional[str] = None,
    output_dir: str = "data/demo_final/india",
    room_id: str = "ROOM-CALIB-01",
    camera_id: str = "CAM-CALIB-01",
    head_provider: str = "sixdrepnet",
    hpe_hz: float = 5.0,
    show_window: bool = False,
    debug_overlay: bool = False,
    save_evidence: bool = True,
    max_frames: Optional[int] = None,
    stride: int = 1,
    seats_preset: Optional[List[Dict[str, Any]]] = None,
    gt_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Compatibility function matching legacy signature while routing to modular pipeline."""
    cfg = get_demo_config(
        name_or_path=video_path,
        output_dir=Path(output_dir),
        room_code=room_id,
        camera_id=camera_id,
        head_provider=head_provider,
        hpe_hz=hpe_hz,
        show_window=show_window,
        debug_overlay=debug_overlay,
        save_evidence=save_evidence,
        max_frames=max_frames,
        stride=stride,
        seats_preset=seats_preset,
        gt_path=Path(gt_path) if gt_path else None,
    )
    return run_demo_pipeline(cfg)
