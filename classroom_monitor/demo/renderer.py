"""HUD Overlay and Telemetry Renderer for VIGIL AI SRS v2.0 Demo Execution.

Renders:
- Calibrated Seat Polygons with risk-state color encoding.
- Person Keypoint Skeletons and 3D Head Orientation Vectors.
- Isolated Roaming Actor / Proctor indicators.
- Non-polluting Top HUD banner (Room, Time, FPS, Occupancy, Active Alerts).
- Bottom Event Ticker for high-priority review triggers.
- Optional `--debug-overlay` panel with per-stage latency breakdown and telemetry.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from classroom_monitor.models import ClassroomEvent, Detection
from classroom_monitor.observation_extractor import RawObservation
from classroom_monitor.scene_context import SeatGraph
from classroom_monitor.seat_manager import SeatManager, SeatState
from classroom_monitor.seat_risk_tracker import RiskState, SeatRiskTracker
from classroom_monitor.temporal_episode_engine import TemporalEpisode

# COCO 17 Keypoints Skeleton Connections
SKELETON_PAIRS = [
    (0, 1), (0, 2), (1, 3), (2, 4),           # Face
    (5, 6), (5, 7), (7, 9), (6, 8), (8, 10),  # Arms / Shoulders
    (5, 11), (6, 12), (11, 12),               # Torso
    (11, 13), (13, 15), (12, 14), (14, 16),   # Legs
]


class DemoHUDOverlayRenderer:
    """Renders calibrated seat boundaries, detections, head vectors, and HUD telemetry."""

    def __init__(self, debug_overlay: bool = False):
        self.debug_overlay = debug_overlay

    def render_frame(
        self,
        frame: np.ndarray,
        frame_idx: int,
        timestamp_ms: float,
        fps: float,
        room_code: str,
        camera_id: str,
        seat_mgr: SeatManager,
        seat_graph: Optional[SeatGraph],
        risk_tracker: SeatRiskTracker,
        active_episodes: List[TemporalEpisode],
        recent_events: List[ClassroomEvent],
        raw_observations: Dict[str, List[RawObservation]],
        detections: List[Detection],
        roaming_detections: List[Detection],
        runtime_metrics: Optional[Dict[str, float]] = None,
        debug_overlay: Optional[bool] = None,
    ) -> np.ndarray:
        """Render complete HUD overlay onto frame copy and return annotated frame."""
        show_debug = self.debug_overlay if debug_overlay is None else debug_overlay
        canvas = frame.copy()

        # 1. Render Seat Polygons & Status Badges
        self._render_seats(canvas, seat_mgr, risk_tracker, raw_observations)

        # 2. Render Roaming Persons
        self._render_roaming_actors(canvas, roaming_detections)

        # 3. Render Person Skeletons and Head Orientation Vectors
        self._render_detections(canvas, detections, raw_observations, seat_graph)

        # 4. Top HUD Header Banner
        self._render_top_hud(
            canvas,
            room_code=room_code,
            camera_id=camera_id,
            timestamp_ms=timestamp_ms,
            fps=fps,
            seat_mgr=seat_mgr,
            risk_tracker=risk_tracker,
            recent_events=recent_events,
        )

        # 5. Bottom Event Ticker
        self._render_bottom_ticker(canvas, recent_events)

        # 6. Optional Debug Overlay Panel
        if show_debug:
            self._render_debug_panel(
                canvas,
                runtime_metrics=runtime_metrics or {},
                active_episodes=active_episodes,
                raw_observations=raw_observations,
            )

        return canvas

    def _render_seats(
        self,
        canvas: np.ndarray,
        seat_mgr: SeatManager,
        risk_tracker: SeatRiskTracker,
        raw_observations: Dict[str, List[RawObservation]],
    ) -> None:
        """Draw seat polygons and labels with risk-based color palette."""
        for seat_id, s_def in seat_mgr.seats.items():
            poly = s_def.polygon.astype(np.int32)
            profile = risk_tracker.profiles.get(seat_id)
            score = profile.risk_score if profile else 0.0
            state = profile.current_state if profile else RiskState.NORMAL.value
            occ = seat_mgr.occupancies.get(seat_id)

            # Determine polygon color
            if occ is None or occ.state == SeatState.EMPTY:
                poly_color = (130, 130, 130)  # Gray: Empty
                thickness = 1
            elif state in (RiskState.FLAGGED_FOR_REVIEW.value, RiskState.SUSPICIOUS.value):
                poly_color = (40, 40, 230)    # Red: High Suspicion
                thickness = 2
            elif state == RiskState.OBSERVE.value or score > 20.0:
                poly_color = (0, 190, 255)    # Orange / Yellow: Active anomaly
                thickness = 2
            else:
                poly_color = (60, 200, 60)    # Green: Normal occupied
                thickness = 1

            cv2.polylines(canvas, [poly], isClosed=True, color=poly_color, thickness=thickness)

            # Draw desk boundary line if calibrated
            if s_def.desk_y is not None:
                min_x = int(np.min(poly[:, 0]))
                max_x = int(np.max(poly[:, 0]))
                d_y = int(s_def.desk_y)
                cv2.line(canvas, (min_x, d_y), (max_x, d_y), (100, 160, 255), 1, cv2.LINE_AA)

            # Label centroid
            cx = int(np.mean(poly[:, 0]))
            cy = int(np.mean(poly[:, 1]))
            label_txt = f"{s_def.seat_label or seat_id}"
            if score > 0.0:
                label_txt += f" ({score:.0f})"

            # Render compact text background
            (tw, th), _ = cv2.getTextSize(label_txt, cv2.FONT_HERSHEY_SIMPLEX, 0.35, 1)
            cv2.rectangle(canvas, (cx - tw // 2 - 2, cy - th - 2), (cx + tw // 2 + 2, cy + 2), (20, 20, 20), -1)
            cv2.putText(canvas, label_txt, (cx - tw // 2, cy - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.35, poly_color, 1, cv2.LINE_AA)

    def _render_roaming_actors(self, canvas: np.ndarray, roaming_detections: List[Detection]) -> None:
        """Render isolated roaming persons or invigilators."""
        for det in roaming_detections:
            x1, y1, x2, y2 = [int(v) for v in det.bbox]
            cv2.rectangle(canvas, (x1, y1), (x2, y2), (0, 140, 255), 2)
            lbl = "ROAMING ACTOR"
            (tw, th), _ = cv2.getTextSize(lbl, cv2.FONT_HERSHEY_SIMPLEX, 0.40, 1)
            cv2.rectangle(canvas, (x1, y1 - th - 4), (x1 + tw + 4, y1), (0, 100, 200), -1)
            cv2.putText(canvas, lbl, (x1 + 2, y1 - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (255, 255, 255), 1, cv2.LINE_AA)

    def _render_detections(
        self,
        canvas: np.ndarray,
        detections: List[Detection],
        raw_observations: Dict[str, List[RawObservation]],
        seat_graph: Optional[SeatGraph],
    ) -> None:
        """Render skeletons and 3D head orientation rays on detected students."""
        for det in detections:
            kps = det.keypoints
            if kps is None or len(kps) < 5:
                continue

            # Draw Skeleton Limbs
            for p1_idx, p2_idx in SKELETON_PAIRS:
                if p1_idx < len(kps) and p2_idx < len(kps):
                    pt1 = kps[p1_idx]
                    pt2 = kps[p2_idx]
                    if len(pt1) >= 3 and len(pt2) >= 3 and pt1[2] > 0.30 and pt2[2] > 0.30:
                        x1, y1 = int(pt1[0]), int(pt1[1])
                        x2, y2 = int(pt2[0]), int(pt2[1])
                        cv2.line(canvas, (x1, y1), (x2, y2), (0, 220, 220), 1, cv2.LINE_AA)

            # Draw Keypoint Joints
            for kp in kps:
                if len(kp) >= 3 and kp[2] > 0.35:
                    cv2.circle(canvas, (int(kp[0]), int(kp[1])), 2, (0, 255, 0), -1, cv2.LINE_AA)

    def _render_top_hud(
        self,
        canvas: np.ndarray,
        room_code: str,
        camera_id: str,
        timestamp_ms: float,
        fps: float,
        seat_mgr: SeatManager,
        risk_tracker: SeatRiskTracker,
        recent_events: List[ClassroomEvent],
    ) -> None:
        """Draw compact top HUD banner with room stats, occupancy, and risk counters."""
        h, w = canvas.shape[:2]
        banner_h = 32

        # Draw semi-transparent header bar
        overlay = canvas.copy()
        cv2.rectangle(overlay, (0, 0), (w, banner_h), (15, 15, 15), -1)
        cv2.addWeighted(overlay, 0.75, canvas, 0.25, 0, canvas)
        cv2.line(canvas, (0, banner_h), (w, banner_h), (80, 80, 80), 1)

        # Metrics text
        time_sec = timestamp_ms / 1000.0
        time_str = f"{int(time_sec // 60):02d}:{time_sec % 60:04.1f}"

        total_seats = len(seat_mgr.seats)
        occupied_seats = sum(1 for occ in seat_mgr.occupancies.values() if occ.state == SeatState.OCCUPIED)
        high_risk_count = sum(
            1 for p in risk_tracker.profiles.values()
            if p.current_state in (RiskState.SUSPICIOUS.value, RiskState.FLAGGED_FOR_REVIEW.value)
        )

        txt_left = f"VIGIL AI | Room: {room_code} | Cam: {camera_id}"
        txt_mid = f"Time: {time_str} ({timestamp_ms:.0f}ms) | FPS: {fps:.1f} | Occupancy: {occupied_seats}/{total_seats}"
        txt_right = f"Events: {len(recent_events)} | High Priority: {high_risk_count}"

        font = cv2.FONT_HERSHEY_SIMPLEX
        scale = 0.40
        y_pos = 20

        cv2.putText(canvas, txt_left, (12, y_pos), font, scale, (220, 220, 220), 1, cv2.LINE_AA)

        (mw, _), _ = cv2.getTextSize(txt_mid, font, scale, 1)
        cv2.putText(canvas, txt_mid, (w // 2 - mw // 2, y_pos), font, scale, (100, 230, 255), 1, cv2.LINE_AA)

        (rw, _), _ = cv2.getTextSize(txt_right, font, scale, 1)
        color_r = (50, 100, 255) if high_risk_count > 0 else (180, 180, 180)
        cv2.putText(canvas, txt_right, (w - rw - 12, y_pos), font, scale, color_r, 1, cv2.LINE_AA)

    def _render_bottom_ticker(self, canvas: np.ndarray, recent_events: List[ClassroomEvent]) -> None:
        """Render bottom notification ticker for active review triggers."""
        if not recent_events:
            return

        h, w = canvas.shape[:2]
        ticker_h = 26
        latest = recent_events[-1]

        overlay = canvas.copy()
        cv2.rectangle(overlay, (0, h - ticker_h), (w, h), (10, 10, 25), -1)
        cv2.addWeighted(overlay, 0.70, canvas, 0.30, 0, canvas)
        cv2.line(canvas, (0, h - ticker_h), (w, h - ticker_h), (0, 100, 220), 1)

        t_sec = (latest.timestamp_ms / 1000.0) if latest.timestamp_ms is not None else (latest.timestamp or 0.0)
        seat_txt = latest.seat_id or "UNKNOWN"
        sev_txt = latest.severity.value if hasattr(latest.severity, "value") else str(latest.severity)
        msg = f"[REVIEW TRIGGER @ {t_sec:.1f}s] Seat {seat_txt}: {latest.behavior} (Severity: {sev_txt})"
        cv2.putText(canvas, msg, (14, h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (80, 200, 255), 1, cv2.LINE_AA)

    def _render_debug_panel(
        self,
        canvas: np.ndarray,
        runtime_metrics: Dict[str, float],
        active_episodes: List[TemporalEpisode],
        raw_observations: Dict[str, List[RawObservation]],
    ) -> None:
        """Render semi-transparent debug telemetry panel on top right."""
        h, w = canvas.shape[:2]
        panel_w = 260
        panel_h = 190
        px1 = w - panel_w - 10
        py1 = 40
        px2 = w - 10
        py2 = py1 + panel_h

        overlay = canvas.copy()
        cv2.rectangle(overlay, (px1, py1), (px2, py2), (20, 20, 20), -1)
        cv2.addWeighted(overlay, 0.82, canvas, 0.18, 0, canvas)
        cv2.rectangle(canvas, (px1, py1), (px2, py2), (70, 70, 70), 1)

        font = cv2.FONT_HERSHEY_SIMPLEX
        cv2.putText(canvas, "DEBUG TELEMETRY", (px1 + 10, py1 + 18), font, 0.40, (0, 220, 255), 1, cv2.LINE_AA)

        lines = [
            f"Perception (YOLO-Pose): {runtime_metrics.get('lat_perception_ms', 0.0):.1f} ms",
            f"6DRepNet Subsampled:    {runtime_metrics.get('lat_6drepnet_ms', 0.0):.1f} ms",
            f"Temporal Episodes:      {runtime_metrics.get('lat_temporal_ms', 0.0):.1f} ms",
            f"Pattern & Risk Tracker: {runtime_metrics.get('lat_risk_ms', 0.0):.1f} ms",
            f"Renderer & Buffer:      {runtime_metrics.get('lat_render_ms', 0.0):.1f} ms",
            f"Total Frame Pipeline:   {runtime_metrics.get('lat_total_ms', 0.0):.1f} ms",
            f"Active Episodes Count:  {len(active_episodes)}",
            f"Active Observations:    {len(raw_observations)}",
        ]

        curr_y = py1 + 38
        for l in lines:
            cv2.putText(canvas, l, (px1 + 10, curr_y), font, 0.32, (200, 200, 200), 1, cv2.LINE_AA)
            curr_y += 18
