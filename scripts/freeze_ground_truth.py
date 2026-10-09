"""Freeze Human Temporal Episode Annotations into Ground Truth JSON Regression Set."""

import json
import sqlite3
from pathlib import Path

DB_PATH = Path("data/vigil_proctoring.db")
OUTPUT_DIR = Path("data/ground_truth")
OUTPUT_FILE = OUTPUT_DIR / "india_classroom_gt.json"

def freeze_ground_truth():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    # Find the target video asset
    cur.execute(
        "SELECT id, file_name, file_path, duration_seconds, fps FROM media_assets WHERE file_name = 'india_classroom.mp4'"
    )
    asset_row = cur.fetchone()
    if not asset_row:
        raise RuntimeError("india_classroom.mp4 asset not found in database!")

    asset_id, file_name, file_path, duration_sec, fps = asset_row

    # Fetch all human annotations (is_ai_proposal == 0)
    cur.execute(
        """
        SELECT id, asset_id, seat_id, seat_code, episode_type, start_ms, peak_ms, end_ms, duration_ms,
               target_neighbor_id, confidence, is_ai_proposal, review_status, notes, created_at
        FROM temporal_episode_annotations
        WHERE asset_id = ? AND is_ai_proposal = 0 AND review_status != 'REJECTED'
        ORDER BY start_ms ASC
        """,
        (asset_id,),
    )
    rows = cur.fetchall()

    gt_episodes = []
    for r in rows:
        gt_episodes.append({
            "id": r[0],
            "asset_id": r[1],
            "seat_id": r[2],
            "seat_code": r[3],
            "episode_type": r[4],
            "start_ms": float(r[5]),
            "peak_ms": float(r[6]),
            "end_ms": float(r[7]),
            "duration_ms": float(r[8]),
            "target_neighbor_id": r[9],
            "confidence": float(r[10]) if r[10] is not None else 1.0,
            "is_ai_proposal": bool(r[11]),
            "review_status": r[12],
            "notes": r[13] or "",
            "created_at": r[14],
        })

    gt_data = {
        "dataset_name": "VIGIL_AI_INDIA_CLASSROOM_GROUND_TRUTH",
        "version": "1.0.0-frozen",
        "asset_id": asset_id,
        "video_file": file_name,
        "video_path": file_path,
        "duration_seconds": duration_sec,
        "fps": fps,
        "total_episodes": len(gt_episodes),
        "episodes": gt_episodes,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(gt_data, f, indent=2, ensure_ascii=False)

    print(f"[OK] Frozen {len(gt_episodes)} ground-truth episodes to {OUTPUT_FILE}")
    for idx, ep in enumerate(gt_episodes, 1):
        print(f"  #{idx:02d}: Seat={ep['seat_code']} | Type={ep['episode_type']:<28} | Range=[{ep['start_ms']:.0f} - {ep['end_ms']:.0f} ms] (Dur: {ep['duration_ms']:.0f} ms)")

if __name__ == "__main__":
    freeze_ground_truth()
