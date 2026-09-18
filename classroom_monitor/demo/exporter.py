"""Standardized Artifact Exporter for VIGIL AI SRS v2.0 Demonstrations.

Exports:
- episodes.json: Deduplicated canonical temporal episodes (indexed by episode_id).
- events.json: Cheating review triggers with canonical statuses (FLAGGED_FOR_REVIEW).
- patterns.json: Multi-frame relational behavior patterns.
- demo_summary.json: High-level executive statistics and seat risk rankings.
- runtime_profile.json: End-to-end FPS and per-stage latency breakdown.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import torch

from classroom_monitor.behavior_pattern_engine import BehaviorPattern
from classroom_monitor.models import ClassroomEvent
from classroom_monitor.seat_risk_tracker import SeatRiskTracker
from classroom_monitor.temporal_episode_engine import TemporalEpisode

logger = logging.getLogger("DemoExporter")


def export_demo_artifacts(
    output_dir: Path,
    episodes: List[TemporalEpisode],
    events: List[ClassroomEvent],
    patterns: List[BehaviorPattern],
    risk_tracker: SeatRiskTracker,
    scene_metadata: Dict[str, Any],
    runtime_stats: Dict[str, Any],
    benchmark_summary: Optional[Dict[str, Any]] = None,
) -> Dict[str, Path]:
    """Export standardized JSON artifacts into output directory and return dictionary of paths."""
    out_p = Path(output_dir)
    out_p.mkdir(parents=True, exist_ok=True)
    evidence_dir = out_p / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    # 1. Canonical Episodes (Deduplicated strictly by episode_id)
    seen_ids: Set[str] = set()
    canonical_episodes: List[Dict[str, Any]] = []

    for ep in episodes:
        if not ep.episode_id or ep.episode_id in seen_ids:
            continue
        seen_ids.add(ep.episode_id)

        ep_type = ep.episode_type.value if hasattr(ep.episode_type, "value") else str(ep.episode_type)
        seat_code = getattr(ep, "seat_code", None) or getattr(ep, "seat_id", "")
        start_t = getattr(ep, "start_timestamp_ms", None)
        if start_t is None:
            start_t = getattr(ep, "start_ms", 0.0)
        end_t = getattr(ep, "end_timestamp_ms", None)
        if end_t is None:
            end_t = getattr(ep, "end_ms", start_t + ep.duration_ms)

        peak_val = getattr(ep, "peak_intensity", None)
        if peak_val is None:
            peak_val = getattr(ep, "peak_value", 0.0)

        canonical_episodes.append({
            "episode_id": ep.episode_id,
            "seat_code": seat_code,
            "episode_type": ep_type,
            "start_ms": round(float(start_t), 1),
            "end_ms": round(float(end_t), 1) if end_t is not None else None,
            "duration_ms": round(float(ep.duration_ms), 1),
            "peak_value": round(float(peak_val), 2),
            "confidence": round(float(ep.confidence), 3),
            "metadata": ep.metadata or {},
        })

    # Sort episodes chronologically by start_ms
    canonical_episodes.sort(key=lambda x: (x["start_ms"], str(x["seat_code"])))

    episodes_file = out_p / "episodes.json"
    with open(episodes_file, "w", encoding="utf-8") as f:
        json.dump(canonical_episodes, f, indent=2, ensure_ascii=False)

    # 2. Events (FLAGGED_FOR_REVIEW canonical statuses)
    canonical_events: List[Dict[str, Any]] = []
    for evt in events:
        sev_val = evt.severity.value if hasattr(evt.severity, "value") else str(evt.severity)
        stat_val = "FLAGGED_FOR_REVIEW"  # Strictly enforce review protocol (no auto CHEATING verdicts)

        meta = dict(evt.metadata or {}) if hasattr(evt, "metadata") and evt.metadata else {}
        ts_ms = getattr(evt, "timestamp_ms", None)
        if ts_ms is None:
            ts_ms = (getattr(evt, "timestamp", 0.0) or 0.0) * 1000.0

        canonical_events.append({
            "event_id": getattr(evt, "event_id", getattr(evt, "id", "")),
            "room_id": getattr(evt, "room_id", ""),
            "camera_id": getattr(evt, "camera_id", ""),
            "seat_code": getattr(evt, "seat_id", getattr(evt, "seat_code", "")),
            "actor_track_id": getattr(evt, "track_id", None),
            "timestamp_ms": round(float(ts_ms), 1),
            "severity": sev_val,
            "status": stat_val,
            "title": getattr(evt, "behavior", getattr(evt, "title", "REVIEW_TRIGGER")),
            "description": getattr(evt, "reviewer_note", getattr(evt, "description", "")),
            "evidence_clip_path": getattr(evt, "evidence_video_path", None) or meta.get("evidence_clip_path"),
            "evidence_snapshot_path": getattr(evt, "evidence_path", None) or meta.get("evidence_snapshot_path"),
            "sha256_hash": meta.get("sha256_hash"),
            "metadata": meta,
        })

    events_file = out_p / "events.json"
    with open(events_file, "w", encoding="utf-8") as f:
        json.dump(canonical_events, f, indent=2, ensure_ascii=False)

    # 3. Patterns JSON
    canonical_patterns: List[Dict[str, Any]] = []
    for pat in patterns:
        p_type = pat.pattern_type.value if hasattr(pat.pattern_type, "value") else str(pat.pattern_type)
        seat_code = getattr(pat, "seat_id", getattr(pat, "seat_code", ""))
        start_ms = float(getattr(pat, "start_timestamp_ms", getattr(pat, "start_ms", 0.0)))
        end_ms = float(getattr(pat, "end_timestamp_ms", getattr(pat, "end_ms", start_ms)))
        dur_ms = getattr(pat, "duration_ms", None)
        if dur_ms is None:
            dur_ms = max(0.0, end_ms - start_ms)

        canonical_patterns.append({
            "pattern_id": pat.pattern_id,
            "pattern_type": p_type,
            "seat_code": seat_code,
            "start_ms": round(start_ms, 1),
            "end_ms": round(end_ms, 1),
            "duration_ms": round(float(dur_ms), 1),
            "confidence": round(float(pat.confidence), 3),
            "involved_seats": [pat.target_neighbor_id] if getattr(pat, "target_neighbor_id", None) else getattr(pat, "involved_seat_ids", []),
            "metadata": getattr(pat, "metadata", {}) or {},
        })

    patterns_file = out_p / "patterns.json"
    with open(patterns_file, "w", encoding="utf-8") as f:
        json.dump(canonical_patterns, f, indent=2, ensure_ascii=False)

    # 4. Seat Risk Summaries
    seat_rankings: List[Dict[str, Any]] = []
    if hasattr(risk_tracker, "profiles"):
        for seat_id, profile in risk_tracker.profiles.items():
            seat_rankings.append({
                "seat_code": seat_id,
                "peak_risk": round(float(profile.peak_risk_score), 1),
                "current_risk": round(float(profile.risk_score), 1),
                "state": str(profile.current_state),
                "total_episodes_assigned": len(profile.processed_episode_ids),
                "last_active_ms": round(float(profile.last_update_timestamp_ms), 1),
            })
    seat_rankings.sort(key=lambda x: x["peak_risk"], reverse=True)

    # Episode distribution by type
    ep_counts: Dict[str, int] = {}
    for ep in canonical_episodes:
        t = ep["episode_type"]
        ep_counts[t] = ep_counts.get(t, 0) + 1

    # Event distribution by severity
    evt_counts: Dict[str, int] = {}
    for ev in canonical_events:
        s = ev["severity"]
        evt_counts[s] = evt_counts.get(s, 0) + 1

    # Count actual evidence files saved
    evidence_clips = list(evidence_dir.glob("*.mp4"))
    evidence_snapshots = list(evidence_dir.glob("*.jpg"))

    # 5. Demo Summary JSON
    summary_data = {
        "scene_id": scene_metadata.get("scene_id", "demo"),
        "room_code": scene_metadata.get("room_code", "ROOM-01"),
        "camera_id": scene_metadata.get("camera_id", "CAM-01"),
        "video_file": scene_metadata.get("video_file", ""),
        "duration_sec": round(float(scene_metadata.get("duration_sec", 0.0)), 2),
        "total_frames_processed": runtime_stats.get("total_frames", 0),
        "fps": round(float(scene_metadata.get("fps", 30.0)), 2),
        "total_calibrated_seats": scene_metadata.get("total_seats", len(seat_rankings)),
        "active_occupied_seats": scene_metadata.get("occupied_seats", 0),
        "total_canonical_episodes": len(canonical_episodes),
        "episodes_by_type": ep_counts,
        "total_review_events": len(canonical_events),
        "events_by_severity": evt_counts,
        "total_detected_patterns": len(canonical_patterns),
        "evidence_clips_saved": len(evidence_clips),
        "evidence_snapshots_saved": len(evidence_snapshots),
        "seat_risk_rankings": seat_rankings,
        "benchmark_ground_truth": benchmark_summary,
    }

    summary_file = out_p / "demo_summary.json"
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=2, ensure_ascii=False)

    # 6. Runtime Profile JSON
    gpu_name = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU"
    runtime_profile = {
        "overall_fps": round(float(runtime_stats.get("overall_fps", 0.0)), 2),
        "total_wall_time_sec": round(float(runtime_stats.get("wall_time_sec", 0.0)), 2),
        "total_video_time_sec": round(float(scene_metadata.get("duration_sec", 0.0)), 2),
        "realtime_factor": round(float(runtime_stats.get("realtime_factor", 0.0)), 2),
        "hardware": {
            "device": "cuda" if torch.cuda.is_available() else "cpu",
            "gpu_name": gpu_name,
            "torch_version": torch.__version__,
        },
        "stage_latency_averages_ms": {
            "perception_yolo_pose": round(float(runtime_stats.get("lat_perception_avg_ms", 0.0)), 2),
            "observation_6drepnet": round(float(runtime_stats.get("lat_6drepnet_avg_ms", 0.0)), 2),
            "temporal_episode_engine": round(float(runtime_stats.get("lat_temporal_avg_ms", 0.0)), 2),
            "behavior_pattern_engine": round(float(runtime_stats.get("lat_pattern_avg_ms", 0.0)), 2),
            "seat_risk_tracker": round(float(runtime_stats.get("lat_risk_avg_ms", 0.0)), 2),
            "rendering_hud": round(float(runtime_stats.get("lat_render_avg_ms", 0.0)), 2),
            "video_writer_io": round(float(runtime_stats.get("lat_writer_avg_ms", 0.0)), 2),
        },
    }

    runtime_file = out_p / "runtime_profile.json"
    with open(runtime_file, "w", encoding="utf-8") as f:
        json.dump(runtime_profile, f, indent=2, ensure_ascii=False)

    logger.info("Exported all demo artifacts to %s", out_p.resolve())
    return {
        "episodes": episodes_file,
        "events": events_file,
        "patterns": patterns_file,
        "summary": summary_file,
        "runtime": runtime_file,
    }
