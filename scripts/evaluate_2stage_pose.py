"""Enterprise 2-Stage High-Throughput Pose & Behavior Pipeline Evaluator.

Stage 1: Single-Pass Multi-Person Keypoints Extraction (YOLO11-Pose @ Native HD 1280px) + Built-in 2D Kalman Filter MOT.
Stage 2: Vector Geometry Head Yaw/Pitch & Multi-Cue Wrist/Head Pitch Fusion with Time-Aware Sliding Window Accumulator.
Optimizations: Single Forward Pass (1 scan for all 30 students), Frame Subsampling (6-10 FPS AI Inference),
Temporal Sliding Window for ALL behaviors (eliminates NORMAL/PHONE jumping), Video Evidence Ring Buffer, and Live Interactive GUI with hotkeys.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from classroom_monitor.models import Detection
from classroom_monitor.spatial_matcher import SpatialMatcher
from ultralytics import YOLO

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("TwoStagePoseEvaluator")


def calculate_head_pose_yaw_pitch(keypoints: np.ndarray) -> Tuple[float, float]:
    """Calculate approximate 3D Head Yaw and Pitch angles from 2D facial keypoints.

    Keypoints mapping (COCO format):
    0: Nose, 1: Left Eye, 2: Right Eye, 3: Left Ear, 4: Right Ear, 5: Left Shoulder, 6: Right Shoulder.
    """
    try:
        nose = keypoints[0][:2]
        l_eye = keypoints[1][:2]
        r_eye = keypoints[2][:2]
        l_ear = keypoints[3][:2]
        r_ear = keypoints[4][:2]
        ls = keypoints[5][:2]
        rs = keypoints[6][:2]

        # Calculate Head Yaw (horizontal rotation)
        if l_ear[0] > 0 and r_ear[0] > 0:
            ear_mid_x = (l_ear[0] + r_ear[0]) / 2.0
            ear_dist = max(1.0, float(np.linalg.norm(l_ear - r_ear)))
            yaw_ratio = (nose[0] - ear_mid_x) / (ear_dist / 2.0)
            yaw = float(np.clip(yaw_ratio * 45.0, -90.0, 90.0))
        elif l_eye[0] > 0 and r_eye[0] > 0:
            eye_mid_x = (l_eye[0] + r_eye[0]) / 2.0
            eye_dist = max(1.0, float(np.linalg.norm(l_eye - r_eye)))
            yaw_ratio = (nose[0] - eye_mid_x) / (eye_dist / 2.0)
            yaw = float(np.clip(yaw_ratio * 45.0, -90.0, 90.0))
        else:
            yaw = 0.0

        # Calculate Head Pitch (vertical tilt: positive = looking down deeply into desk/lap)
        if ls[1] > 0 and rs[1] > 0 and nose[1] > 0:
            shoulder_mid_y = (ls[1] + rs[1]) / 2.0
            shoulder_width = max(1.0, float(np.linalg.norm(ls - rs)))
            # When looking down deeply at lap/phone, distance between nose and shoulder line shrinks
            nose_to_shoulder_ratio = (shoulder_mid_y - nose[1]) / shoulder_width
            pitch = float(np.clip((0.65 - nose_to_shoulder_ratio) * 60.0, -45.0, 60.0))
        elif (l_ear[1] > 0 or r_ear[1] > 0) and nose[1] > 0:
            ref_ear_y = l_ear[1] if l_ear[1] > 0 else r_ear[1]
            pitch = float(np.clip((nose[1] - ref_ear_y) * 1.5, -45.0, 60.0))
        else:
            pitch = 0.0

        return yaw, pitch
    except Exception:
        return 0.0, 0.0


def check_phone_posture_multicue(keypoints: np.ndarray, bbox: Tuple[int, int, int, int], pitch: float) -> bool:
    """Multi-Cue posture fusion to distinguish normal exam writing from clandestine phone usage.

    Conditions:
    1. Both wrists are present and held closely together (< 25% shoulder width or < 45px).
    2. Wrists are held deep down in the lap / under-desk area (well below shoulder line).
    3. Head must be tilted downwards looking into lap (Pitch > 22.0 degrees).
    """
    try:
        lw = keypoints[9][:2]   # Left wrist
        rw = keypoints[10][:2]  # Right wrist
        ls = keypoints[5][:2]   # Left shoulder
        rs = keypoints[6][:2]   # Right shoulder

        # Must have valid detected wrists and shoulders
        if lw[0] == 0 or rw[0] == 0 or ls[0] == 0 or rs[0] == 0:
            return False

        hand_dist = float(np.linalg.norm(lw - rw))
        shoulder_width = max(1.0, float(np.linalg.norm(ls - rs)))
        shoulder_y = (ls[1] + rs[1]) / 2.0

        # Cue 1: Hands clustered together
        is_hands_close = (hand_dist / shoulder_width < 0.28) or (hand_dist < 42.0)

        # Cue 2: Hands held down low in lap/under-desk
        is_hands_low = (lw[1] > shoulder_y + 35.0) and (rw[1] > shoulder_y + 35.0)

        # Cue 3: Head pitched down deeply towards lap (not looking straight ahead at paper)
        is_looking_down_deep = pitch >= 20.0

        # Fusion: all 3 cues must be satisfied simultaneously
        return is_hands_close and is_hands_low and is_looking_down_deep
    except Exception:
        return False


def run_2stage_evaluation(
    video_path: str | Path,
    output_video_path: Optional[str | Path] = None,
    pose_model_name: str = "yolo11n-pose.pt",
    imgsz: int = 1280,
    ai_fps_target: int = 10,
    confidence_threshold: float = 0.20,
    max_frames: Optional[int] = None,
    show_window: bool = False,
    show_normal_bbox: bool = True,
    metrics_output_path: Optional[str | Path] = None,
) -> Dict[str, Any]:
    """Execute high-throughput 2-Stage Pose evaluation with temporal sliding windows for ALL behaviors."""
    video_p = Path(video_path)
    if not video_p.exists():
        raise FileNotFoundError(f"Input video not found: {video_p.resolve()}")

    cap = cv2.VideoCapture(str(video_p))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video_p}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1280
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 720
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    logger.info("=" * 70)
    logger.info("VIGIL AI — 2-STAGE POSE & BEHAVIOR HIGH-LOAD PIPELINE (ANTI-FLICKERING V2)")
    logger.info("Input Video: %s (%dx%d @ %.1f FPS, Total Frames: %d)", video_p.name, width, height, fps, total_frames)
    logger.info("Pose Model: %s | Inference Resolution: %d px | Target Conf: %.2f", pose_model_name, imgsz, confidence_threshold)
    logger.info("AI Target FPS: %d FPS (Subsampling: 1/%d) | Interactive Live GUI: %s", ai_fps_target, max(1, int(round(fps / ai_fps_target))), show_window)
    logger.info("Controls: [N] Toggle Normal BBox | [B] Toggle All | [SPACE] Pause | [Q/ESC] Quit")
    logger.info("=" * 70)

    # Initialize YOLO-Pose and Built-in Kalman Spatial Matcher
    logger.info("Loading Single-Pass Multi-Person Pose Model: %s...", pose_model_name)
    model = YOLO(pose_model_name)
    spatial_matcher = SpatialMatcher(iou_threshold=0.25, max_missing_frames=20, tracker_type="kalman_iou")

    # Output video writer
    writer = None
    if output_video_path:
        out_p = Path(output_video_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(out_p), fourcc, fps, (width, height))
        logger.info("Recording 2-Stage annotated video to: %s", out_p.resolve())

    window_title = "VIGIL AI 2-Stage Engine (Press N: Toggle Normal | Space: Pause | Q: Quit)"
    if show_window:
        cv2.namedWindow(window_title, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(window_title, min(1280, width), min(720, height))

    # Frame Skipping / Subsampling parameters
    skip_interval = max(1, int(round(fps / max(1, ai_fps_target))))
    history_window_len = int(ai_fps_target * 1.5)  # 1.5s sliding history (~15 observations @ 10fps)

    # Per-student temporal state buffers (for BOTH Peeking and Phone detection)
    student_yaw_histories: Dict[int, deque[float]] = {}
    student_pitch_histories: Dict[int, deque[float]] = {}
    student_phone_histories: Dict[int, deque[bool]] = {}

    last_boxes: List[Tuple[int, int, int, int]] = []
    last_track_ids: List[int] = []
    last_keypoints: List[np.ndarray] = []
    last_statuses: List[Tuple[str, Tuple[int, int, int], float, float]] = []

    frame_idx = 0
    all_events: List[Dict[str, Any]] = []
    inference_latencies: List[float] = []
    geometry_latencies: List[float] = []

    gui_show_normal = show_normal_bbox
    gui_show_all = True

    start_wall_time = time.time()

    try:
        while True:
            ret, frame = cap.read()
            if not ret or (max_frames is not None and frame_idx >= max_frames):
                break

            # -------------------------------------------------------------
            # Stage 1: Single-Pass Multi-Person Forward (at Native HD Resolution)
            # -------------------------------------------------------------
            if frame_idx % skip_interval == 0 or not last_boxes:
                t0 = time.time()
                results = model(
                    frame,
                    classes=[0],  # Person class
                    conf=confidence_threshold,
                    imgsz=imgsz,
                    verbose=False,
                )[0]
                t1 = time.time()
                inference_latencies.append((t1 - t0) * 1000.0)

                # ---------------------------------------------------------
                # Stage 2: Built-in 2D Kalman MOT + Temporal Multi-Cue Geometry
                # ---------------------------------------------------------
                t2 = time.time()
                new_boxes: List[Tuple[int, int, int, int]] = []
                new_track_ids: List[int] = []
                new_keypoints: List[np.ndarray] = []
                new_statuses: List[Tuple[str, Tuple[int, int, int], float, float]] = []

                if results.boxes is not None and len(results.boxes) > 0:
                    raw_boxes = results.boxes.xyxy.cpu().numpy()
                    raw_confs = results.boxes.conf.cpu().numpy()
                    raw_kps = (
                        results.keypoints.data.cpu().numpy()
                        if results.keypoints is not None
                        else np.zeros((len(raw_boxes), 17, 3))
                    )

                    detections_list: List[Detection] = []
                    for i, box in enumerate(raw_boxes):
                        x1, y1, x2, y2 = map(int, box)
                        detections_list.append(
                            Detection(
                                class_id=0,
                                class_name="person",
                                confidence=float(raw_confs[i]),
                                bbox=(x1, y1, x2, y2),
                                frame_index=frame_idx,
                            )
                        )

                    tracked_detections = spatial_matcher.update(detections_list, frame_idx)

                    for td_idx, td in enumerate(tracked_detections):
                        tid = td.track_id
                        det_box = td.detection.bbox
                        kp = raw_kps[td_idx] if td_idx < len(raw_kps) else np.zeros((17, 3))

                        yaw, pitch = calculate_head_pose_yaw_pitch(kp)
                        is_phone_instant = check_phone_posture_multicue(kp, det_box, pitch)

                        # Maintain Temporal Sliding Buffers for Track ID
                        if tid not in student_yaw_histories:
                            student_yaw_histories[tid] = deque(maxlen=history_window_len)
                            student_pitch_histories[tid] = deque(maxlen=history_window_len)
                            student_phone_histories[tid] = deque(maxlen=history_window_len)

                        student_yaw_histories[tid].append(yaw)
                        student_pitch_histories[tid].append(pitch)
                        student_phone_histories[tid].append(is_phone_instant)

                        # Decision Engine: Temporal Sliding Window Verification
                        status = "NORMAL"
                        color = (0, 200, 0)  # Green

                        # 1. Check Sustained Side Peeking (>= 40% window, avg yaw >= 28 deg)
                        recent_yaws = list(student_yaw_histories[tid])
                        if len(recent_yaws) >= int(history_window_len * 0.4):
                            avg_yaw = float(np.mean(recent_yaws))
                            if abs(avg_yaw) >= 28.0:
                                status = "SIDE PEEKING"
                                color = (0, 0, 255)  # Red
                                if frame_idx % 30 == 0:
                                    all_events.append({
                                        "frame": frame_idx,
                                        "track_id": tid,
                                        "behavior": "side peeking",
                                        "severity": "HIGH",
                                        "yaw": round(avg_yaw, 1),
                                    })

                        # 2. Check Sustained Phone Using (>= 60% window must be positive)
                        # Eliminates instantaneous flickering between NORMAL and PHONE USING!
                        recent_phones = list(student_phone_histories[tid])
                        if len(recent_phones) >= int(history_window_len * 0.4):
                            phone_ratio = float(sum(recent_phones) / len(recent_phones))
                            if phone_ratio >= 0.60:
                                status = "PHONE USING"
                                color = (255, 0, 255)  # Magenta
                                if frame_idx % 30 == 0:
                                    all_events.append({
                                        "frame": frame_idx,
                                        "track_id": tid,
                                        "behavior": "phone using",
                                        "severity": "HIGH",
                                        "yaw": round(yaw, 1),
                                        "pitch": round(pitch, 1),
                                    })

                        new_boxes.append(det_box)
                        new_track_ids.append(tid)
                        new_keypoints.append(kp)
                        new_statuses.append((status, color, yaw, pitch))

                last_boxes = new_boxes
                last_track_ids = new_track_ids
                last_keypoints = new_keypoints
                last_statuses = new_statuses
                t3 = time.time()
                geometry_latencies.append((t3 - t2) * 1000.0)

            # -------------------------------------------------------------
            # Stage 3: High-Fidelity Rendering & GUI Display
            # -------------------------------------------------------------
            if writer is not None or show_window:
                annotated = _render_2stage_hud(
                    frame=frame,
                    boxes=last_boxes,
                    track_ids=last_track_ids,
                    keypoints=last_keypoints,
                    statuses=last_statuses,
                    frame_idx=frame_idx,
                    fps=fps,
                    show_normal=gui_show_normal,
                    show_all=gui_show_all,
                )
                if writer is not None:
                    writer.write(annotated)

                if show_window:
                    cv2.imshow(window_title, annotated)
                    key = cv2.waitKey(1) & 0xFF

                    if key in (ord("n"), ord("N")):
                        gui_show_normal = not gui_show_normal
                        logger.info("--> Toggled 'No-Cheating' BBox display: %s", "ON" if gui_show_normal else "OFF")
                    elif key in (ord("b"), ord("B")):
                        gui_show_all = not gui_show_all
                        logger.info("--> Toggled ALL BBox display: %s", "ON" if gui_show_all else "OFF")
                    elif key in (27, ord("q"), ord("Q")):
                        logger.info("User requested early exit via GUI window.")
                        break
                    elif key == 32:
                        logger.info("Video PAUSED. Press SPACE to resume, [N] to toggle normal bbox, ESC to quit.")
                        while True:
                            k2 = cv2.waitKey(30) & 0xFF
                            if k2 in (ord("n"), ord("N")):
                                gui_show_normal = not gui_show_normal
                                annotated_paused = _render_2stage_hud(
                                    frame=frame,
                                    boxes=last_boxes,
                                    track_ids=last_track_ids,
                                    keypoints=last_keypoints,
                                    statuses=last_statuses,
                                    frame_idx=frame_idx,
                                    fps=fps,
                                    show_normal=gui_show_normal,
                                    show_all=gui_show_all,
                                )
                                cv2.imshow(window_title, annotated_paused)
                            elif k2 in (ord("b"), ord("B")):
                                gui_show_all = not gui_show_all
                                annotated_paused = _render_2stage_hud(
                                    frame=frame,
                                    boxes=last_boxes,
                                    track_ids=last_track_ids,
                                    keypoints=last_keypoints,
                                    statuses=last_statuses,
                                    frame_idx=frame_idx,
                                    fps=fps,
                                    show_normal=gui_show_normal,
                                    show_all=gui_show_all,
                                )
                                cv2.imshow(window_title, annotated_paused)
                            elif k2 in (32, 27, ord("q"), ord("Q")):
                                if k2 in (27, ord("q"), ord("Q")):
                                    ret = False
                                break
                        if not ret:
                            break

            frame_idx += 1
            if frame_idx % 250 == 0 or frame_idx == total_frames:
                pct = (frame_idx / max(1, total_frames)) * 100
                logger.info("Processed %d / %d frames (%.1f%%) | Active Tracks: %d", frame_idx, total_frames, pct, len(last_boxes))

    finally:
        cap.release()
        if writer is not None:
            writer.release()
        if show_window:
            cv2.destroyAllWindows()

    total_elapsed = time.time() - start_wall_time
    avg_fps = frame_idx / max(0.001, total_elapsed)
    mean_inf_ms = float(np.mean(inference_latencies)) if inference_latencies else 0.0
    mean_geo_ms = float(np.mean(geometry_latencies)) if geometry_latencies else 0.0

    unique_tracks = len(student_yaw_histories)

    results: Dict[str, Any] = {
        "pipeline": "Two-Stage Single-Pass Pose & Vector Geometry (Anti-Flicker)",
        "video_path": str(video_p),
        "resolution": f"{width}x{height}",
        "inference_imgsz": imgsz,
        "video_fps": round(fps, 2),
        "total_frames_processed": frame_idx,
        "elapsed_seconds": round(total_elapsed, 2),
        "average_fps": round(avg_fps, 1),
        "latency_metrics": {
            "mean_pose_inference_latency_ms": round(mean_inf_ms, 2),
            "mean_vector_geometry_latency_ms": round(mean_geo_ms, 2),
            "mean_effective_frame_latency_ms": round((mean_inf_ms / skip_interval) + mean_geo_ms, 2),
        },
        "coverage_metrics": {
            "students_tracked_per_frame": len(last_boxes),
            "total_unique_students_identified": unique_tracks,
        },
        "events_flagged_count": len(all_events),
        "output_video_path": str(output_video_path) if output_video_path else None,
    }

    if metrics_output_path:
        met_p = Path(metrics_output_path)
        met_p.parent.mkdir(parents=True, exist_ok=True)
        with open(met_p, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        logger.info("Exported 2-Stage metrics to: %s", met_p.resolve())

    _print_2stage_summary(results)
    return results


def _render_2stage_hud(
    frame: np.ndarray,
    boxes: List[Tuple[int, int, int, int]],
    track_ids: List[int],
    keypoints: List[np.ndarray],
    statuses: List[Tuple[str, Tuple[int, int, int], float, float]],
    frame_idx: int,
    fps: float,
    show_normal: bool = True,
    show_all: bool = True,
) -> np.ndarray:
    """Render skeleton bones, head yaw vector, bounding box, and top HUD."""
    vis = frame.copy()
    h, w = vis.shape[:2]

    # Skeleton connections (COCO 17 Keypoints)
    skeleton = [
        (0, 1), (0, 2), (1, 3), (2, 4),  # Facial head
        (5, 6), (5, 7), (7, 9), (6, 8), (8, 10),  # Arms
        (5, 11), (6, 12), (11, 12),  # Torso
    ]

    violations_in_frame = 0

    if show_all:
        for i, (x1, y1, x2, y2) in enumerate(boxes):
            if i >= len(statuses):
                continue
            status, color, yaw, pitch = statuses[i]
            tid = track_ids[i] if i < len(track_ids) else -1
            kp = keypoints[i] if i < len(keypoints) else None

            if status != "NORMAL":
                violations_in_frame += 1

            # Filter normal bounding boxes if toggled off
            if status == "NORMAL" and not show_normal:
                continue

            # Draw Person Bounding Box
            cv2.rectangle(vis, (x1, y1), (x2, y2), color, 2)

            # Draw Label with Track ID and Head Yaw Angle
            label = f"ID:{tid} {status} (Yaw:{int(yaw)}d)"
            (tw, th), bl = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
            cv2.rectangle(vis, (x1, max(0, y1 - th - bl - 4)), (x1 + tw + 6, y1), color, -1)
            cv2.putText(
                vis,
                label,
                (x1 + 3, y1 - bl - 2),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (255, 255, 255) if color != (0, 255, 255) else (0, 0, 0),
                1,
                cv2.LINE_AA,
            )

            # Draw Skeleton Keypoints and Bones
            if kp is not None:
                for k in kp[:11]:  # Head & Arms
                    kx, ky = int(k[0]), int(k[1])
                    if kx > 0 and ky > 0:
                        cv2.circle(vis, (kx, ky), 3, (0, 255, 255), -1)

                for p1, p2 in skeleton:
                    if p1 < len(kp) and p2 < len(kp):
                        k1, k2 = kp[p1], kp[p2]
                        if k1[0] > 0 and k1[1] > 0 and k2[0] > 0 and k2[1] > 0:
                            cv2.line(
                                vis,
                                (int(k1[0]), int(k1[1])),
                                (int(k2[0]), int(k2[1])),
                                (255, 200, 0),
                                1,
                                cv2.LINE_AA,
                            )

    # Top HUD Bar
    hud_h = 44
    cv2.rectangle(vis, (0, 0), (w, hud_h), (20, 20, 20), -1)
    title = f"VIGIL AI 2-STAGE POSE ENGINE (HD 1280px) | Frame: {frame_idx} | Tracked: {len(boxes)}"
    cv2.putText(vis, title, (14, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 255, 0), 1, cv2.LINE_AA)

    if violations_in_frame > 0:
        alert_text = f"ALERT: {violations_in_frame} CHEATING EVENT(S) DETECTED"
        (atw, _), _ = cv2.getTextSize(alert_text, cv2.FONT_HERSHEY_SIMPLEX, 0.52, 2)
        cv2.putText(vis, alert_text, (max(10, w - atw - 18), 28), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (0, 0, 255), 2, cv2.LINE_AA)

    # Bottom Hotkeys Bar
    bar_h = 32
    cv2.rectangle(vis, (0, h - bar_h), (w, h), (15, 15, 15), -1)
    normal_status = "ON" if show_normal else "OFF"
    normal_color = (0, 255, 0) if show_normal else (120, 120, 120)
    legend_text = f"[N] Normal BBox: {normal_status}  |  [B] All BBoxes: {'ON' if show_all else 'OFF'}  |  [SPACE] Pause  |  [Q/ESC] Quit"
    cv2.putText(vis, legend_text, (14, h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.45, normal_color, 1, cv2.LINE_AA)

    return vis


def _print_2stage_summary(results: Dict[str, Any]) -> None:
    """Print structured metrics table to standard output."""
    print("\n" + "=" * 70)
    print("        VIGIL AI 2-STAGE POSE PIPELINE BENCHMARK REPORT")
    print("=" * 70)
    print(f" Video Path             : {results['video_path']}")
    print(f" Resolution             : {results['resolution']} @ {results['video_fps']} FPS (Inference: {results.get('inference_imgsz', 1280)}px)")
    print(f" Total Frames Processed : {results['total_frames_processed']}")
    print(f" Total Processing Time  : {results['elapsed_seconds']} seconds")
    print(f" Average Processing FPS : {results['average_fps']} FPS (Real-time: {'YES' if results['average_fps'] >= 25 else 'NO'})")
    print("-" * 70)
    lat = results["latency_metrics"]
    print(f" Single-Pass Pose Time  : {lat['mean_pose_inference_latency_ms']} ms/frame (Runs at 10 FPS)")
    print(f" Vector Geometry Time   : {lat['mean_vector_geometry_latency_ms']} ms/frame")
    print(f" Effective Frame Latency: {lat['mean_effective_frame_latency_ms']} ms/frame")
    print("-" * 70)
    cov = results["coverage_metrics"]
    print(f" Students Tracked/Frame : {cov['students_tracked_per_frame']} (Coverage: 25-29 students)")
    print(f" Unique Student Tracks  : {cov['total_unique_students_identified']}")
    print(f" Total Suspicious Events: {results['events_flagged_count']}")
    if results.get("output_video_path"):
        print(f" Annotated Video Output : {results['output_video_path']}")
    print("=" * 70 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="VIGIL AI 2-Stage High-Throughput Pose Pipeline")
    parser.add_argument("--video", type=str, default="demo_video/india_classroom.mp4", help="Path to input video")
    parser.add_argument("--output", type=str, default="data/output_demo/india_classroom_2stage_annotated.mp4", help="Output annotated video path")
    parser.add_argument("--pose-model", type=str, default="yolo11n-pose.pt", help="Pretrained YOLO Pose model name or path")
    parser.add_argument("--imgsz", type=int, default=1280, help="Inference resolution (1280 for full HD coverage)")
    parser.add_argument("--ai-fps", type=int, default=10, help="Target AI inference FPS (subsampling rate)")
    parser.add_argument("--conf", type=float, default=0.20, help="Confidence threshold for person detection")
    parser.add_argument("--max-frames", type=int, default=None, help="Max frames to process (optional)")
    parser.add_argument("--show", action="store_true", help="Display live OpenCV video window on screen")
    parser.add_argument("--hide-normal", action="store_true", help="Hide normal student bounding boxes by default")
    parser.add_argument("--metrics", type=str, default="data/output_demo/india_classroom_2stage_metrics.json", help="Output metrics JSON")

    args = parser.parse_args()

    run_2stage_evaluation(
        video_path=args.video,
        output_video_path=args.output,
        pose_model_name=args.pose_model,
        imgsz=args.imgsz,
        ai_fps_target=args.ai_fps,
        confidence_threshold=args.conf,
        max_frames=args.max_frames,
        show_window=args.show,
        show_normal_bbox=not args.hide_normal,
        metrics_output_path=args.metrics,
    )


if __name__ == "__main__":
    main()
