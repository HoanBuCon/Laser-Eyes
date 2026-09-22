"""Audit script to verify raw YAML scene calibration values against parsed SeatContext objects."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from classroom_monitor.scene_context import SceneProfile


def audit_scene(yaml_path: Path) -> dict:
    with open(yaml_path, "r", encoding="utf-8") as f:
        raw_yaml = yaml.safe_load(f)

    profile = SceneProfile.from_file(str(yaml_path))
    seat_graph = profile.seat_graph

    raw_seats = raw_yaml.get("seats", [])
    seats_audit = []

    for raw_s in raw_seats:
        s_code = raw_s.get("seat_code") or raw_s.get("seat_id")
        parsed_ctx = seat_graph.get_context(s_code)

        # Raw values
        raw_yaw = raw_s.get("baseline_yaw")
        if raw_yaw is None and isinstance(raw_s.get("reference_directions"), dict):
            raw_yaw = raw_s.get("reference_directions", {}).get("baseline_yaw")

        raw_pitch = raw_s.get("baseline_pitch")
        if raw_pitch is None and isinstance(raw_s.get("reference_directions"), dict):
            raw_pitch = raw_s.get("reference_directions", {}).get("baseline_pitch")

        raw_desk_y = raw_s.get("desk_boundary_y")
        if raw_desk_y is None:
            raw_desk_y = raw_s.get("desk_y")
        if raw_desk_y is None and isinstance(raw_s.get("desk_geometry"), dict):
            raw_desk_y = raw_s.get("desk_geometry", {}).get("desk_boundary_y")

        # Parsed values
        parsed_yaw = parsed_ctx.reference_directions.baseline_yaw if parsed_ctx else None
        parsed_pitch = parsed_ctx.reference_directions.baseline_pitch if parsed_ctx else None
        parsed_desk_y = parsed_ctx.desk_geometry.desk_boundary_y if (parsed_ctx and parsed_ctx.desk_geometry) else None
        parsed_desk_cap = parsed_ctx.capabilities.desk_hand_interaction.value if parsed_ctx else "DISABLED"

        seats_audit.append({
            "seat_code": s_code,
            "raw_baseline_yaw": raw_yaw,
            "parsed_baseline_yaw": parsed_yaw,
            "yaw_match": bool(raw_yaw is not None and parsed_yaw is not None and abs(float(raw_yaw) - float(parsed_yaw)) < 1e-4) if raw_yaw is not None else (parsed_yaw == 0.0),
            "raw_baseline_pitch": raw_pitch,
            "parsed_baseline_pitch": parsed_pitch,
            "pitch_match": bool(raw_pitch is not None and parsed_pitch is not None and abs(float(raw_pitch) - float(parsed_pitch)) < 1e-4) if raw_pitch is not None else (parsed_pitch == 0.0),
            "raw_desk_boundary_y": raw_desk_y,
            "parsed_desk_boundary_y": parsed_desk_y,
            "desk_boundary_match": bool(raw_desk_y is not None and parsed_desk_y is not None and abs(float(raw_desk_y) - float(parsed_desk_y)) < 1e-4) if raw_desk_y is not None else (parsed_desk_y is None),
            "desk_capability": parsed_desk_cap,
        })

    return {
        "yaml_file": str(yaml_path.resolve()),
        "scene_id": profile.scene_id,
        "room_code": profile.room_code,
        "total_seats": len(seats_audit),
        "all_yaw_matched": all(s["yaw_match"] for s in seats_audit),
        "all_pitch_matched": all(s["pitch_match"] for s in seats_audit),
        "all_desk_matched": all(s["desk_boundary_match"] for s in seats_audit),
        "seats": seats_audit,
    }


def main():
    root = Path(__file__).resolve().parent.parent
    scenes_dir = root / "configs" / "scenes"
    out_dir = root / "data" / "diagnostics"
    out_dir.mkdir(parents=True, exist_ok=True)

    audit_results = {}
    for scene_file in sorted(scenes_dir.glob("*.yaml")):
        audit_results[scene_file.stem] = audit_scene(scene_file)

    out_file = out_dir / "scene_calibration_parse_audit.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(audit_results, f, indent=2, ensure_ascii=False)

    print(f"Audit completed. Results saved to {out_file.resolve()}")
    for s_name, res in audit_results.items():
        print(f"Scene: {s_name} | Seats: {res['total_seats']} | Yaw Match: {res['all_yaw_matched']} | Desk Match: {res['all_desk_matched']}")


if __name__ == "__main__":
    main()
