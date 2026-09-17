import sqlite3
import json
import sys

sys.stdout.reconfigure(encoding='utf-8')
conn = sqlite3.connect("data/vigil_proctoring.db")
cur = conn.cursor()

print("--- ROOMS ---")
for r in cur.execute("SELECT id, room_code, name FROM exam_rooms").fetchall():
    print(r)

print("\n--- SEATS FOR ROOM-CALIB-01 ---")
for s in cur.execute("SELECT id, seat_code, seat_label, polygon_json, context_json, enabled FROM seats WHERE room_id='057e724c-de6b-4157-8a97-ad753fb40a6e'").fetchall():
    print(f"Seat: {s[1]} | Label: {s[2]} | Enabled: {s[5]}")
    print(f"  Polygon: {s[3]}")
    print(f"  Context: {s[4]}")
