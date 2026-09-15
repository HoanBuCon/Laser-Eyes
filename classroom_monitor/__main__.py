"""CLI Entry Point for VIGIL AI Classroom Cheating Surveillance.

Usage:
    python -m classroom_monitor --input exam_video.mp4 --output results/annotated.mp4
    python -m classroom_monitor --camera 0
    python -m classroom_monitor --mock-demo
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import cv2

from classroom_monitor.config import ClassroomConfig
from classroom_monitor.video_processor import VideoProcessor

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ClassroomCLI")


def main() -> None:
    parser = argparse.ArgumentParser(description="VIGIL AI Classroom Cheating Detection")
    parser.add_argument("--input", type=str, default="", help="Path to input video file")
    parser.add_argument("--camera", type=int, default=-1, help="Webcam/Camera device index (e.g. 0)")
    parser.add_argument("--output", type=str, default="", help="Path to output annotated video")
    parser.add_argument("--report", type=str, default="results/classroom_report.json", help="Path to output JSON report")
    parser.add_argument("--model", type=str, default="models/classroom_best.pt", help="Path to YOLO weights")
    parser.add_argument("--max-frames", type=int, default=None, help="Stop after N frames (for testing)")
    parser.add_argument("--mock-demo", action="store_true", help="Run simulated demo stream")
    args = parser.parse_args()

    config = ClassroomConfig(model_path=args.model)
    processor = VideoProcessor(model_path=args.model, config=config)

    if args.input:
        logger.info("Starting batch processing on video: %s", args.input)
        res = processor.process_video(
            video_path=args.input,
            output_path=args.output if args.output else None,
            max_frames=args.max_frames,
        )

        out_rep = Path(args.report)
        out_rep.parent.mkdir(parents=True, exist_ok=True)
        with open(out_rep, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=2, ensure_ascii=False)

        print("\n" + "=" * 65)
        print("          CLASSROOM SURVEILLANCE RUN SUMMARY")
        print("=" * 65)
        print(f"Frames Processed : {res['total_frames_processed']:,}")
        print(f"Elapsed Time     : {res['elapsed_seconds']} s")
        print(f"Average FPS      : {res['average_fps']}")
        print(f"Total Violations : {res['total_events']}")
        print(f"Report File      : {out_rep.resolve()}")
        print("=" * 65 + "\n")

    elif args.camera >= 0 or args.mock_demo:
        logger.info("Opening real-time camera preview (Press 'q' or ESC to exit)...")
        cap = cv2.VideoCapture(0 if args.camera < 0 else args.camera)
        frame_idx = 0
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break
            dets = processor.detector.detect(frame, frame_idx)
            events = processor.event_engine.process_frame(dets, frame_idx, frame)
            vis = processor.render_overlay(frame, dets, events, frame_idx)
            cv2.imshow("VIGIL AI Classroom Proctoring", vis)
            if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                break
            frame_idx += 1
        cap.release()
        cv2.destroyAllWindows()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
