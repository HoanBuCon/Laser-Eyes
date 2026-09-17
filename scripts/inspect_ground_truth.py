import sqlite3
import json
import sys

# Ensure UTF-8 output on Windows
sys.stdout.reconfigure(encoding='utf-8')

conn = sqlite3.connect("data/vigil_proctoring.db")
cur = conn.cursor()

print("=== MEDIA ASSETS (VIDEOS) ===")
for r in cur.execute("SELECT id, file_name, file_path, duration_seconds, fps FROM media_assets WHERE file_name LIKE '%.mp4'").fetchall():
    print(r)

print("\n=== EXAM ROOMS ===")
for r in cur.execute("SELECT id, room_code, name FROM exam_rooms").fetchall():
    print(r)

print("\n=== SEATS ===")
for r in cur.execute("SELECT id, room_id, seat_code, seat_label, polygon_json FROM seats").fetchall():
    print(r[0], r[1], r[2], r[3], r[4])

print("\n=== TEMPORAL EPISODE ANNOTATIONS ===")
rows = cur.execute("SELECT id, asset_id, seat_code, episode_type, start_ms, end_ms, duration_ms, is_ai_proposal, review_status, notes FROM temporal_episode_annotations").fetchall()
for r in rows:
    print(r)
print(f"Total annotations: {len(rows)}")
