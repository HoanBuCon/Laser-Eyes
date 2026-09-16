"""Automated Evaluation and Demo Video Generator for VIGIL AI Classroom Proctoring.

Processes a real classroom surveillance video (e.g., demo_video/india_classroom.mp4),
runs full YOLOv12s detection, 2D Kalman Filter Tracking, Time-Aware Score Accumulation,
Crowd Context Intelligence, renders high-fidelity HUD overlays, saves 10s video evidence clips,
shows an interactive live GUI window with real-time hotkeys (Toggle No-Cheating BBoxes, Pause, Quit),
and generates a comprehensive JSON/Console evaluation report.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from classroom_monitor.config import ClassroomConfig
from classroom_monitor.detector import ClassroomDetector
from classroom_monitor.event_engine import EventEngine
from classroom_monitor.models import ClassroomEvent, Detection, EventStatus, SeverityLevel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("DemoEvaluator")


def run_evaluation(
    video_path: str | Path,
    output_video_path: Optional[str | Path] = None,
    confidence_threshold: float = 0.15,
    max_frames: Optional[int] = None,
    enable_sahi: bool = False,
    save_evidence: bool = True,
    show_window: bool = False,
    show_normal_bbox: bool = True,
    evidence_dir: str = "data/evidence",
    evidence_video_dir: str = "data/evidence_clips",
    metrics_output_path: Optional[str | Path] = None,
) -> Dict[str, Any]:
    """Execute end-to-end evaluation pipeline on video file with interactive hotkeys."""
    video_p = Path(video_path)
    if not video_p.exists():
        raise FileNotFoundError(f"Input video not found: {video_p.resolve()}")

    cap = cv2.VideoCapture(str(video_p))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video file: {video_p}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1280
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 720
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    logger.info("=" * 70)
    logger.info("VIGIL AI — CLASSROOM SURVEILLANCE DEMO EVALUATION")
    logger.info("Input Video: %s (%dx%d @ %.1f FPS, Total Frames: %d)", video_p.name, width, height, fps, total_frames)
    logger.info("Confidence Threshold: %.2f | SAHI Tiling: %s | Live GUI: %s", confidence_threshold, enable_sahi, show_window)
    logger.info("Interactive Controls: [N] Toggle No-Cheating BBox | [B] Toggle All BBoxes | [SPACE] Pause | [Q/ESC] Quit")
    logger.info("=" * 70)

    # Configure Classroom Engine
    config = ClassroomConfig(
        confidence_threshold=confidence_threshold,
        enable_sahi_tiling=enable_sahi,
        evidence_dir=evidence_dir,
        evidence_video_dir=evidence_video_dir,
        enable_video_evidence=save_evidence,
    )

    detector = ClassroomDetector(config=config)
    event_engine = EventEngine(fps=fps, config=config)

    # Initialize Video Writer if output path specified
    writer = None
    if output_video_path:
        out_p = Path(output_video_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(out_p), fourcc, fps, (width, height))
        logger.info("Rendering annotated output video to: %s", out_p.resolve())

    evidence_path_dir = Path(evidence_dir)
    if save_evidence:
        evidence_path_dir.mkdir(parents=True, exist_ok=True)

    window_title = "VIGIL AI -- Classroom Proctoring (Press N: Toggle No-Cheating | Space: Pause | ESC/Q: Quit)"
    if show_window:
        cv2.namedWindow(window_title, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(window_title, min(1280, width), min(720, height))

    frame_idx = 0
    all_events: List[ClassroomEvent] = []
    inference_latencies: List[float] = []
    tracking_latencies: List[float] = []

    # Dynamic GUI display states
    gui_show_normal = show_normal_bbox
    gui_show_all = True

    start_wall_time = time.time()

    try:
        while True:
            ret, frame = cap.read()
            if not ret or (max_frames is not None and frame_idx >= max_frames):
                break

            timestamp_ms = (frame_idx / fps) * 1000.0

            # 1. Perception
            t0 = time.time()
            detections = detector.detect(frame, frame_index=frame_idx)
            t1 = time.time()
            inference_latencies.append((t1 - t0) * 1000.0)

            # 2. Decision & Event Generation (Always tracks all detections under the hood)
            events = event_engine.process_frame(
                detections, frame_idx, frame, timestamp_ms=timestamp_ms
            )
            t2 = time.time()
            tracking_latencies.append((t2 - t1) * 1000.0)

            # Handle newly emitted events
            for evt in events:
                all_events.append(evt)
                if save_evidence and evt.evidence_frame is not None:
                    ev_file = evidence_path_dir / f"{evt.event_id}.jpg"
                    cv2.imwrite(str(ev_file), evt.evidence_frame)
                    evt.evidence_path = str(ev_file)

            # 3. Visualization & Live GUI Display
            if writer is not None or show_window:
                annotated = _render_hud(
                    frame=frame,
                    detections=detections,
                    active_events=events,
                    frame_idx=frame_idx,
                    fps=fps,
                    config=config,
                    event_engine=event_engine,
                    show_normal_bbox=gui_show_normal,
                    show_all_bbox=gui_show_all,
                )
                if writer is not None:
                    writer.write(annotated)

                if show_window:
                    cv2.imshow(window_title, annotated)
                    key = cv2.waitKey(1) & 0xFF

                    # Key Handling:
                    # 'N' or 'n': Toggle No-Cheating BBoxes
                    if key in (ord("n"), ord("N")):
                        gui_show_normal = not gui_show_normal
                        logger.info("--> Toggled 'No-Cheating' BBox display: %s", "ON" if gui_show_normal else "OFF")

                    # 'B' or 'b': Toggle All BBoxes
                    elif key in (ord("b"), ord("B")):
                        gui_show_all = not gui_show_all
                        logger.info("--> Toggled ALL BBox display: %s", "ON" if gui_show_all else "OFF")

                    # 'ESC' or 'Q' or 'q': Early Exit
                    elif key in (27, ord("q"), ord("Q")):
                        logger.info("User requested early exit via GUI window.")
                        break

                    # 'SPACE': Pause / Resume
                    elif key == 32:
                        logger.info("Video PAUSED. Press SPACE to resume, [N] to toggle normal bbox, ESC to quit.")
                        while True:
                            k2 = cv2.waitKey(30) & 0xFF
                            if k2 in (ord("n"), ord("N")):
                                gui_show_normal = not gui_show_normal
                                logger.info("--> Toggled 'No-Cheating' BBox display: %s", "ON" if gui_show_normal else "OFF")
                                # Re-render paused frame
                                annotated_paused = _render_hud(
                                    frame=frame,
                                    detections=detections,
                                    active_events=events,
                                    frame_idx=frame_idx,
                                    fps=fps,
                                    config=config,
                                    event_engine=event_engine,
                                    show_normal_bbox=gui_show_normal,
                                    show_all_bbox=gui_show_all,
                                )
                                cv2.imshow(window_title, annotated_paused)
                            elif k2 in (ord("b"), ord("B")):
                                gui_show_all = not gui_show_all
                                annotated_paused = _render_hud(
                                    frame=frame,
                                    detections=detections,
                                    active_events=events,
                                    frame_idx=frame_idx,
                                    fps=fps,
                                    config=config,
                                    event_engine=event_engine,
                                    show_normal_bbox=gui_show_normal,
                                    show_all_bbox=gui_show_all,
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
                logger.info("Processed %d / %d frames (%.1f%%) | Active Events: %d", frame_idx, total_frames, pct, len(all_events))

    finally:
        cap.release()
        if writer is not None:
            writer.release()
        if show_window:
            cv2.destroyAllWindows()

    # Flush in-flight 10s video evidence clips
    saved_clips: List[str] = []
    if config.enable_video_evidence:
        saved_clips = event_engine.video_buffer.flush_all()

    total_elapsed = time.time() - start_wall_time
    avg_fps = frame_idx / max(0.001, total_elapsed)
    mean_inf_ms = float(np.mean(inference_latencies)) if inference_latencies else 0.0
    mean_trk_ms = float(np.mean(tracking_latencies)) if tracking_latencies else 0.0

    # Aggregate Metrics
    behavior_counts: Dict[str, int] = {}
    severity_counts: Dict[str, int] = {}
    for ev in all_events:
        behavior_counts[ev.behavior] = behavior_counts.get(ev.behavior, 0) + 1
        severity_counts[ev.severity] = severity_counts.get(ev.severity, 0) + 1

    unique_tracks = len(event_engine.matcher.tracks)

    results: Dict[str, Any] = {
        "video_path": str(video_p),
        "resolution": f"{width}x{height}",
        "video_fps": round(fps, 2),
        "total_frames_processed": frame_idx,
        "elapsed_seconds": round(total_elapsed, 2),
        "average_fps": round(avg_fps, 1),
        "latency_metrics": {
            "mean_inference_latency_ms": round(mean_inf_ms, 2),
            "mean_tracking_latency_ms": round(mean_trk_ms, 2),
            "mean_total_frame_latency_ms": round(mean_inf_ms + mean_trk_ms, 2),
        },
        "tracking_metrics": {
            "total_unique_tracks": unique_tracks,
            "tracker_type": config.tracker_type,
        },
        "event_metrics": {
            "total_events_detected": len(all_events),
            "events_by_behavior": behavior_counts,
            "events_by_severity": severity_counts,
            "saved_evidence_snapshots_count": len([e for e in all_events if e.evidence_path]),
            "saved_evidence_video_clips_count": len(saved_clips),
        },
        "output_video_path": str(output_video_path) if output_video_path else None,
        "events": [e.to_dict() for e in all_events],
    }

    if metrics_output_path:
        met_p = Path(metrics_output_path)
        met_p.parent.mkdir(parents=True, exist_ok=True)
        with open(met_p, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        logger.info("Exported metrics report to: %s", met_p.resolve())

    # Print Summary Report
    _print_summary(results)
    return results


def _render_hud(
    frame: np.ndarray,
    detections: List[Detection],
    active_events: List[ClassroomEvent],
    frame_idx: int,
    fps: float,
    config: ClassroomConfig,
    event_engine: EventEngine,
    show_normal_bbox: bool = True,
    show_all_bbox: bool = True,
) -> np.ndarray:
    """Render top HUD, bottom hotkey bar, bounding boxes, labels, and event alert banners."""
    vis = frame.copy()
    h, w = vis.shape[:2]

    color_normal = (0, 200, 0)
    color_phone = (255, 0, 255)
    color_peek = (0, 0, 255)

    # 1. Draw Detections
    if show_all_bbox:
        for det in detections:
            is_normal = det.class_name in config.normal_classes
            # Check toggle filter for normal bounding boxes
            if is_normal and not show_normal_bbox:
                continue

            x1, y1, x2, y2 = det.bbox
            is_cheating = det.class_name in config.cheating_classes

            if det.class_name == "phone using":
                color = color_phone
            elif is_cheating:
                color = color_peek
            else:
                color = color_normal

            cv2.rectangle(vis, (x1, y1), (x2, y2), color, 2)
            label = f"{det.class_name} ({det.confidence:.2f})"
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

    # 2. Draw Top HUD Bar
    hud_h = 44
    cv2.rectangle(vis, (0, 0), (w, hud_h), (20, 20, 20), -1)
    title = (
        f"VIGIL AI ENTERPRISE PROCTORING | Frame: {frame_idx} | Tracks: {event_engine.get_active_tracks_count()}"
    )
    cv2.putText(vis, title, (14, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 255, 0), 1, cv2.LINE_AA)

    # 3. Active Alert Badge
    if active_events:
        alert_text = f"ALERT: {len(active_events)} VIOLATION(S) FLAGGED"
        (atw, _), _ = cv2.getTextSize(alert_text, cv2.FONT_HERSHEY_SIMPLEX, 0.52, 2)
        cv2.putText(vis, alert_text, (max(10, w - atw - 18), 28), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (0, 0, 255), 2, cv2.LINE_AA)

    # 4. Draw Bottom Hotkeys Legend Bar
    bar_h = 32
    cv2.rectangle(vis, (0, h - bar_h), (w, h), (15, 15, 15), -1)
    normal_status = "ON" if show_normal_bbox else "OFF"
    normal_color = (0, 255, 0) if show_normal_bbox else (120, 120, 120)
    
    legend_text = f"[N] No-Cheating BBox: {normal_status}  |  [B] All BBoxes: {'ON' if show_all_bbox else 'OFF'}  |  [SPACE] Pause  |  [Q/ESC] Quit"
    cv2.putText(vis, legend_text, (14, h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.45, normal_color, 1, cv2.LINE_AA)

    return vis


def _print_summary(results: Dict[str, Any]) -> None:
    """Print structured metrics table to standard output."""
    print("\n" + "=" * 70)
    print("                 VIGIL AI DEMO EVALUATION SUMMARY REPORT")
    print("=" * 70)
    print(f" Video Path             : {results['video_path']}")
    print(f" Resolution             : {results['resolution']} @ {results['video_fps']} FPS")
    print(f" Total Frames Processed : {results['total_frames_processed']}")
    print(f" Processing Time        : {results['elapsed_seconds']} seconds")
    print(f" Average Processing FPS : {results['average_fps']} FPS (Real-time: {'YES' if results['average_fps'] >= 25 else 'NO'})")
    print("-" * 70)
    lat = results["latency_metrics"]
    print(f" Inference Latency      : {lat['mean_inference_latency_ms']} ms/frame")
    print(f" Tracking & MOT Latency : {lat['mean_tracking_latency_ms']} ms/frame")
    print(f" Total Frame Latency    : {lat['mean_total_frame_latency_ms']} ms/frame")
    print("-" * 70)
    evt = results["event_metrics"]
    print(f" Unique Student Tracks  : {results['tracking_metrics']['total_unique_tracks']}")
    print(f" Total Events Flagged   : {evt['total_events_detected']}")
    print(f" Events by Behavior     : {evt['events_by_behavior']}")
    print(f" Events by Severity     : {evt['events_by_severity']}")
    print(f" Evidence Snapshots     : {evt['saved_evidence_snapshots_count']}")
    print(f" 10s Video Clips Saved  : {evt['saved_evidence_video_clips_count']}")
    if results.get("output_video_path"):
        print(f" Annotated Video Output : {results['output_video_path']}")
    print("=" * 70 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="VIGIL AI Classroom Surveillance Demo Evaluator")
    parser.add_argument("--video", type=str, default="demo_video/india_classroom.mp4", help="Path to input video")
    parser.add_argument("--output", type=str, default="data/output_demo/india_classroom_annotated.mp4", help="Annotated video output path")
    parser.add_argument("--conf", type=float, default=0.15, help="Confidence threshold for YOLO detector")
    parser.add_argument("--max-frames", type=int, default=None, help="Max frames to process (optional)")
    parser.add_argument("--show", action="store_true", help="Display live OpenCV video window on screen")
    parser.add_argument("--hide-normal", action="store_true", help="Hide 'no cheating' bounding boxes by default")
    parser.add_argument("--sahi", action="store_true", help="Enable SAHI high-resolution dynamic slicing")
    parser.add_argument("--no-evidence", action="store_true", help="Disable saving snapshots and video clips")
    parser.add_argument("--metrics", type=str, default="data/output_demo/india_classroom_metrics.json", help="Path to output metrics JSON")

    args = parser.parse_args()

    run_evaluation(
        video_path=args.video,
        output_video_path=args.output,
        confidence_threshold=args.conf,
        max_frames=args.max_frames,
        enable_sahi=args.sahi,
        save_evidence=not args.no_evidence,
        show_window=args.show,
        show_normal_bbox=not args.hide_normal,
        metrics_output_path=args.metrics,
    )


if __name__ == "__main__":
    main()
