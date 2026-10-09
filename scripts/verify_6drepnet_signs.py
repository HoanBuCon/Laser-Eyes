"""Verify raw 6DRepNet Euler angle sign conventions on real classroom frames."""

import cv2
import numpy as np
import torch
from sixdrepnet import SixDRepNet

def main():
    model = SixDRepNet(gpu_id=0 if torch.cuda.is_available() else -1)

    cap = cv2.VideoCapture("demo_video/india_classroom.mp4")
    # Grab frames where we know human GT behavior
    # Seat 04 turns RIGHT at 23543 ms (frame ~706)
    # Seat 15 turns LEFT at 25792 ms (frame ~774)
    # Seat 02 turns RIGHT at 45178 ms (frame ~1355)

    test_targets = [
        ("SEAT_04_TURN_RIGHT", 710, [466, 308, 682, 491]),   # x1, y1, x2, y2
        ("SEAT_15_TURN_LEFT", 780, [214, 279, 362, 390]),
        ("SEAT_02_TURN_RIGHT", 1360, [954, 405, 1200, 685]),
    ]

    for name, frame_idx, seat_bbox in test_targets:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        if not ret:
            print(f"Failed to read frame {frame_idx}")
            continue

        x1, y1, x2, y2 = seat_bbox
        # Crop upper portion of seat for head
        head_h = int((y2 - y1) * 0.45)
        crop = frame[y1:y1 + head_h, x1:x2]
        if crop.size == 0:
            continue

        p, y, r = model.predict(crop)
        print(f"[{name}] Frame={frame_idx}: Raw 6DRepNet -> Pitch={p[0]:.2f}, Yaw={y[0]:.2f}, Roll={r[0]:.2f}")

    cap.release()

if __name__ == "__main__":
    main()
