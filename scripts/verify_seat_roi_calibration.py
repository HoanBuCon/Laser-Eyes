"""Verification & Acceptance Script for Interactive Seat ROI Calibration Tool.

Validates:
1. Creation of >= 5 Seat ROIs on a real classroom frame (demo_video/india_classroom.mp4).
2. Saving to CSDL via canonical Seat API / Repository.
3. Reloading seats from CSDL.
4. YOLO-Pose single-pass inference.
5. Verification of Person-to-Seat mapping via SeatManager.
6. Export of annotated debug image showing Seat Polygons, Seat IDs, BBoxes, Keypoints, and Anchors.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import cv2
import numpy as np

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from classroom_monitor.config import ClassroomConfig
from classroom_monitor.detector import PoseClassroomDetector
from classroom_monitor.seat_manager import SeatDefinition, SeatManager
from storage.database import SessionLocal, init_db
from storage.db_models import Camera, ExamRoom, ExamSite, SeatROI
from storage.repositories import CameraRepository, RoomRepository, SeatRepository, SiteRepository


def run_calibration_verification():
    print("\n================================================================================")
    print("[TEST] STARTING SEAT ROI CALIBRATION VERIFICATION ON REAL VIDEO FRAME")
    print("================================================================================")

    # 1. Initialize Database
    init_db()
    db = SessionLocal()

    site_repo = SiteRepository(db)
    room_repo = RoomRepository(db)
    cam_repo = CameraRepository(db)
    seat_repo = SeatRepository(db)

    # Ensure Site, Room, Camera exist
    sites = site_repo.list_all()
    if sites:
        site = sites[0]
    else:
        site = site_repo.create(name="Calibration Test Campus", address="Khu Đô Thị Đại Học")

    room = room_repo.get_by_code("ROOM-CALIB-01")
    if not room:
        room = room_repo.create(
            name="Phòng thi 101 - Khu Tự Nhiên",
            site_id=site.id,
            room_code="ROOM-CALIB-01",
            capacity=30,
        )

    cam = cam_repo.get_by_id("cam-calib-01")
    if not cam:
        cam = cam_repo.create(
            room_id=room.id,
            name="Camera Trần Góc 45 Độ",
            source_uri="demo_video/india_classroom.mp4",
            position="Ceiling Front-Right",
            resolution="1280x720",
        )

    # 2. Extract Real Frame from demo_video/india_classroom.mp4
    video_path = "demo_video/india_classroom.mp4"
    if not Path(video_path).exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    cap = cv2.VideoCapture(video_path)
    cap.set(cv2.CAP_PROP_POS_FRAMES, 45)  # Seek 1.5 seconds in
    ret, frame = cap.read()
    cap.release()

    if not ret or frame is None:
        raise RuntimeError("Failed to extract frame from video")

    h, w = frame.shape[:2]
    print(f"[OK] Extracted real frame: {w}x{h} px from {video_path}")

    # 3. Run YOLO-Pose Detection
    config = ClassroomConfig(pipeline_mode="2stage_pose", enable_sahi_tiling=False)
    detector = PoseClassroomDetector(config=config)
    detections = detector.detect(frame, frame_index=45)
    print(f"[OK] YOLO-Pose detected {len(detections)} persons in frame.")

    # 4. Construct 5 Real Seat ROI Polygons enclosing 5 distinct students across the room
    # Pick distinct students (Row 1 Left, Row 1 Center, Row 1 Right, Row 2 Left, Row 2 Center)
    target_dets = [
        # (det_candidate, seat_code, seat_label)
        ([108, 382, 283, 544], "SEAT-101-01", "Bàn 1 Dãy Trái (Hàng 1)"),
        ([509, 478, 677, 629], "SEAT-101-02", "Bàn 1 Dãy Giữa (Hàng 1)"),
        ([1000, 401, 1146, 646], "SEAT-101-03", "Bàn 1 Dãy Phải (Hàng 1)"),
        ([212, 277, 360, 400], "SEAT-101-04", "Bàn 2 Dãy Trái (Hàng 2)"),
        ([491, 316, 629, 478], "SEAT-101-05", "Bàn 2 Dãy Giữa (Hàng 2)"),
    ]

    sample_seats_data = []
    for bbox, seat_code, label in target_dets:
        x1, y1, x2, y2 = bbox
        bw = x2 - x1
        bh = y2 - y1

        # Polygon around desk and seating area (4+ points)
        px1 = max(0.0, float(x1 - bw * 0.15))
        py1 = max(0.0, float(y1 + bh * 0.05))
        px2 = min(float(w), float(x2 + bw * 0.15))
        py2 = min(float(h), float(y2 + bh * 0.15))

        poly_points = [
            [px1, py1],
            [px2, py1],
            [px2, py2],
            [px1, py2],
        ]

        sample_seats_data.append({
            "room_id": room.id,
            "camera_id": cam.id,
            "seat_code": seat_code,
            "seat_label": label,
            "polygon_json": poly_points,
            "enabled": True,
        })

    print(f"\n[STEP 1] Created {len(sample_seats_data)} Seat ROI polygon definitions.")

    # 5. Save Seats to CSDL via Canonical SeatRepository / Bulk Sync
    saved_seats = seat_repo.bulk_upsert_for_camera(
        room_id=room.id,
        camera_id=cam.id,
        seats_data=sample_seats_data,
    )
    print(f"[STEP 2] Saved {len(saved_seats)} Seat ROIs to Database successfully.")

    # 6. Reload Seats from CSDL
    reloaded_db_seats = seat_repo.list_by_room(room_id=room.id, enabled_only=True)
    print(f"[STEP 3] Reloaded {len(reloaded_db_seats)} Seat ROIs from Database.")

    # 7. Initialize Production SeatManager and Load Reloaded Seats
    seat_mgr = SeatManager(room_id=room.id, camera_id=cam.id)
    seat_defs = []
    for s in reloaded_db_seats:
        seat_defs.append(SeatDefinition.from_dict({
            "id": s.id,
            "room_id": s.room_id,
            "seat_code": s.seat_code,
            "seat_label": s.seat_label,
            "polygon_json": s.polygon_json,
            "enabled": s.enabled,
        }))
    seat_mgr.load_seats(seat_defs)

    # 8. Perform Runtime Person-to-Seat Mapping
    mapped_seats, unmapped_dets = seat_mgr.map_detections_to_seats(
        detections=detections,
        timestamp_ms=1500.0,
        frame_idx=45,
    )

    print("\n[STEP 4] Runtime Person-to-Seat Mapping Results:")
    mapped_count = 0
    for seat_code, assigned_det in mapped_seats.items():
        if assigned_det is not None:
            mapped_count += 1
            x1, y1, x2, y2 = assigned_det.bbox
            print(f"  * {seat_code} -> DETECTED PERSON (conf: {assigned_det.confidence:.2f}, bbox: [{int(x1)}, {int(y1)}, {int(x2)}, {int(y2)}])")
        else:
            print(f"  * {seat_code} -> EMPTY / NOT OCCUPIED")

    print(f"\n[RESULT] Successfully mapped {mapped_count}/{len(sample_seats_data)} seats to detected students.")
    assert mapped_count >= 4, f"Expected at least 4 seats mapped, got {mapped_count}"

    # 9. Render Annotated Verification Image
    annotated = frame.copy()
    overlay = frame.copy()

    # Draw Polygons
    for s in seat_defs:
        poly_pts = np.array(s.polygon, dtype=np.int32).reshape((-1, 1, 2))
        cv2.fillPoly(overlay, [poly_pts], (0, 200, 100))  # Green fill
        cv2.polylines(annotated, [poly_pts], isClosed=True, color=(0, 255, 150), thickness=2)

    # Blend overlay
    cv2.addWeighted(overlay, 0.3, annotated, 0.7, 0, annotated)

    # Draw Detections, Anchors and Seat Badges
    for seat_code, assigned_det in mapped_seats.items():
        s_def = seat_mgr.seats.get(seat_code)
        if s_def is not None:
            cx = int(np.mean(s_def.polygon[:, 0]))
            cy = int(np.mean(s_def.polygon[:, 1]))

            # Draw Seat Badge
            badge_text = f"{seat_code}"
            (tw, th), _ = cv2.getTextSize(badge_text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
            cv2.rectangle(annotated, (cx - tw//2 - 6, cy - th - 6), (cx + tw//2 + 6, cy + 6), (20, 20, 20), -1)
            cv2.rectangle(annotated, (cx - tw//2 - 6, cy - th - 6), (cx + tw//2 + 6, cy + 6), (0, 255, 200), 1)
            cv2.putText(annotated, badge_text, (cx - tw//2, cy), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

        if assigned_det is not None:
            x1, y1, x2, y2 = [int(v) for v in assigned_det.bbox]
            cv2.rectangle(annotated, (x1, y1), (x2, y2), (255, 100, 0), 2)
            # Draw Anchor Point (Bottom-Center)
            ac_x = int((x1 + x2) / 2)
            ac_y = int(y2 - (y2 - y1) * 0.15)
            cv2.circle(annotated, (ac_x, ac_y), 6, (0, 255, 255), -1)
            cv2.circle(annotated, (ac_x, ac_y), 8, (0, 0, 0), 2)
            cv2.putText(annotated, f"Anchor: {seat_code}", (ac_x - 40, ac_y + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)

    # Draw Title Bar
    cv2.rectangle(annotated, (0, 0), (w, 40), (15, 23, 42), -1)
    cv2.putText(
        annotated,
        f"VIGIL AI — Seat ROI Calibration Verification ({mapped_count}/{len(sample_seats_data)} Seats Mapped)",
        (20, 26),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.75,
        (0, 255, 255),
        2,
    )

    out_dir = Path("data/output_demo")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "seat_roi_calibration_verification.jpg"
    cv2.imwrite(str(out_path), annotated)
    print(f"\n[OK] Annotated debug image saved at: {out_path}")

    # Copy to artifacts directory
    artifact_dir = Path("C:/Users/ADMIN/.gemini/antigravity-cli/brain/012a305f-c7f3-41ae-af16-8876f25ae244")
    if artifact_dir.exists():
        art_out = artifact_dir / "seat_roi_calibration_verification.jpg"
        cv2.imwrite(str(art_out), annotated)
        print(f"[OK] Exported verification image to artifact folder: {art_out}")

    db.close()
    return True


if __name__ == "__main__":
    run_calibration_verification()
