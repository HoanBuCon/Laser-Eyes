"""HUD Overlay and Telemetry Renderer for VIGIL AI SRS v2.0 Demo Execution.

Implements Two Distinct Display Modes:
1. Clean Proctor Mode (DEFAULT):
   - Unobtrusive, production-grade AI co-pilot view for exam proctors.
   - Zero visual clutter: Person bboxes OFF, Skeletons OFF, Head rays OFF, Desk lines OFF.
   - Normal/Empty seats: Dim minimal label [S01] without polygon borders.
   - Incident / Suspicious seats: Distinct colored border and floating Incident Badge ONLY on active seats.
   - Clean ASCII labels (e.g. S01, S08) eliminating cv2.putText Unicode glyph errors.
   - Clean Top Status Bar and Bottom Incident Ticker.

2. Developer Debug Mode (--debug-overlay or 'D' hotkey):
   - Full computer vision telemetry: YOLO-Pose bounding boxes, 17-keypoint skeletons,
     3D Head Orientation yaw/pitch rays, Full Seat ROI polygons, Desk lines, and
     Top-Right Stage Latency / Telemetry panel.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from classroom_monitor.models import ClassroomEvent, Detection
from classroom_monitor.observation_extractor import RawObservation
from classroom_monitor.scene_context import SeatGraph
from classroom_monitor.seat_manager import SeatManager, SeatState
from classroom_monitor.seat_risk_tracker import RiskState, SeatRiskTracker
from classroom_monitor.temporal_episode_engine import TemporalEpisode

# COCO 17 Keypoints Skeleton Connections for Debug Mode
SKELETON_PAIRS = [
    (0, 1), (0, 2), (1, 3), (2, 4),           # Face
    (5, 6), (5, 7), (7, 9), (6, 8), (8, 10),  # Arms / Shoulders
    (5, 11), (6, 12), (11, 12),               # Torso
    (11, 13), (13, 15), (12, 14), (14, 16),   # Legs
]


def get_short_seat_label(seat_id: str, label: str = "") -> str:
    """Format clean short ASCII label (e.g. 'S01', 'S08') avoiding Unicode encoding glitches."""
    digits = re.findall(r"\d+", seat_id)
    if digits:
        last_num = int(digits[-1])
        return f"S{last_num:02d}"
    clean = re.sub(r"[^a-zA-Z0-9_-]", "", seat_id)
    return clean[:6] if clean else "SEAT"


class DemoHUDOverlayRenderer:
    """Renders clean proctor overlays (default) or rich developer telemetry."""

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
        """Render HUD overlay according to active display mode."""
        show_debug = self.debug_overlay if debug_overlay is None else debug_overlay
        canvas = frame.copy()

        if show_debug:
            # === DEVELOPER DEBUG MODE ===
            self._render_debug_detections(canvas, detections, raw_observations)
            self._render_debug_seats(canvas, seat_mgr, risk_tracker)
            self._render_debug_roaming(canvas, roaming_detections)
            self._render_top_hud(canvas, room_code, camera_id, timestamp_ms, fps, seat_mgr, risk_tracker, recent_events)
            self._render_bottom_ticker(canvas, recent_events, timestamp_ms)
            self._render_debug_panel(canvas, runtime_metrics or {}, active_episodes, raw_observations)
        else:
            # === CLEAN PROCTOR MODE (DEFAULT PRESENTATION) ===
            self._render_proctor_seats(canvas, seat_mgr, risk_tracker, active_episodes, recent_events)
            self._render_proctor_roaming(canvas, roaming_detections)
            self._render_top_hud(canvas, room_code, camera_id, timestamp_ms, fps, seat_mgr, risk_tracker, recent_events)
            self._render_bottom_ticker(canvas, recent_events, timestamp_ms)

        return canvas

    # -------------------------------------------------------------------------
    # CLEAN PROCTOR MODE RENDERING
    # -------------------------------------------------------------------------

    def _render_proctor_seats(
        self,
        canvas: np.ndarray,
        seat_mgr: SeatManager,
        risk_tracker: SeatRiskTracker,
        active_episodes: List[TemporalEpisode],
        recent_events: List[ClassroomEvent],
    ) -> None:
        """Render seats in clean proctor mode: only highlight seats requiring human attention."""
        flagged_seats = []
        suspicious_seats = []
        normal_seats = []

        for seat_id, s_def in seat_mgr.seats.items():
            poly = s_def.polygon.astype(np.int32)
            if len(poly) == 0:
                continue

            cx = int(np.mean(poly[:, 0]))
            cy = int(np.mean(poly[:, 1]))
            top_y = int(np.min(poly[:, 1]))

            profile = risk_tracker.profiles.get(seat_id)
            score = profile.risk_score if profile else 0.0
            state = profile.current_state if profile else RiskState.NORMAL.value
            short_lbl = get_short_seat_label(seat_id, s_def.seat_label)

            occ = seat_mgr.occupancies.get(seat_id)
            is_occupied = occ is not None and occ.state == SeatState.OCCUPIED
            has_active_ep = any(getattr(ep, "seat_id", getattr(ep, "seat_code", "")) == seat_id for ep in active_episodes)

            # Determine severity state (only occupied or active seats can be flagged)
            is_flagged = (
                (state in (RiskState.FLAGGED_FOR_REVIEW.value, RiskState.COOLDOWN.value) or score >= 75.0)
                and (is_occupied or has_active_ep)
            )
            is_suspicious = (
                (state == RiskState.SUSPICIOUS.value or (score >= 50.0 and not is_flagged))
                and (is_occupied or has_active_ep)
            )

            if is_flagged:
                flagged_seats.append((seat_id, s_def, poly, cx, cy, top_y, score, short_lbl))
            elif is_suspicious:
                suspicious_seats.append((seat_id, s_def, poly, cx, cy, top_y, score, short_lbl))
            else:
                normal_seats.append((seat_id, s_def, poly, cx, cy, top_y, score, short_lbl))

        # PASS 1: Render NORMAL / EMPTY seats (minimal unobtrusive pill badges)
        for seat_id, s_def, poly, cx, cy, top_y, score, short_lbl in normal_seats:
            (tw, th), _ = cv2.getTextSize(short_lbl, cv2.FONT_HERSHEY_SIMPLEX, 0.32, 1)
            cv2.rectangle(canvas, (cx - tw // 2 - 3, cy - th - 2), (cx + tw // 2 + 3, cy + 2), (15, 15, 15), -1)
            cv2.putText(canvas, short_lbl, (cx - tw // 2, cy), cv2.FONT_HERSHEY_SIMPLEX, 0.32, (170, 170, 170), 1, cv2.LINE_AA)

        # PASS 2: Render SUSPICIOUS seats (Amber border + score badge)
        for seat_id, s_def, poly, cx, cy, top_y, score, short_lbl in suspicious_seats:
            cv2.polylines(canvas, [poly], isClosed=True, color=(0, 165, 255), thickness=2, lineType=cv2.LINE_AA)
            badge_txt = f"{short_lbl} ({score:.0f})"
            (tw, th), _ = cv2.getTextSize(badge_txt, cv2.FONT_HERSHEY_SIMPLEX, 0.36, 1)
            cv2.rectangle(canvas, (cx - tw // 2 - 3, cy - th - 3), (cx + tw // 2 + 3, cy + 3), (20, 20, 20), -1)
            cv2.putText(canvas, badge_txt, (cx - tw // 2, cy - 1), cv2.FONT_HERSHEY_SIMPLEX, 0.36, (0, 190, 255), 1, cv2.LINE_AA)

        # PASS 3: Render FLAGGED seats on top (High-contrast RED border and SOLID floating Incident Badge)
        for seat_id, s_def, poly, cx, cy, top_y, score, short_lbl in flagged_seats:
            cv2.polylines(canvas, [poly], isClosed=True, color=(30, 30, 235), thickness=2 if canvas.shape[1] < 800 else 3, lineType=cv2.LINE_AA)

            seat_act_eps = [
                ep for ep in active_episodes
                if getattr(ep, "seat_id", getattr(ep, "seat_code", "")) == seat_id
            ]
            recent_evt = next((e for e in reversed(recent_events) if getattr(e, "seat_id", getattr(e, "seat_code", "")) == seat_id), None)

            if seat_act_eps:
                raw_type = seat_act_eps[0].episode_type
                beh_str = raw_type.value if hasattr(raw_type, "value") else str(raw_type)
                beh_str = beh_str.replace("_", " ").title()
            elif recent_evt:
                beh_str = recent_evt.behavior.replace("_", " ").title()
            else:
                beh_str = "Suspicious Posture"

            f_title = 0.36 if canvas.shape[1] < 800 else 0.42
            f_sub = 0.30 if canvas.shape[1] < 800 else 0.35

            card_title = f"{short_lbl}: REVIEW REQUIRED"
            card_sub = f"{beh_str} (Score: {score:.0f})"

            (w1, h1), _ = cv2.getTextSize(card_title, cv2.FONT_HERSHEY_SIMPLEX, f_title, 1)
            (w2, h2), _ = cv2.getTextSize(card_sub, cv2.FONT_HERSHEY_SIMPLEX, f_sub, 1)
            card_w = max(w1, w2) + 14
            card_h = h1 + h2 + 10

            bx1 = max(4, min(canvas.shape[1] - card_w - 4, cx - card_w // 2))
            by1 = max(34, top_y - card_h - 4)
            bx2 = bx1 + card_w
            by2 = by1 + card_h

            # Solid Opaque Dark Background (prevents text bleed-through)
            cv2.rectangle(canvas, (bx1, by1), (bx2, by2), (18, 18, 24), -1)
            cv2.rectangle(canvas, (bx1, by1), (bx2, by2), (40, 40, 240), 1, cv2.LINE_AA)

            # Card Header & Subtitle
            cv2.putText(canvas, card_title, (bx1 + 6, by1 + h1 + 3), cv2.FONT_HERSHEY_SIMPLEX, f_title, (60, 80, 255), 1, cv2.LINE_AA)
            cv2.putText(canvas, card_sub, (bx1 + 6, by2 - 3), cv2.FONT_HERSHEY_SIMPLEX, f_sub, (230, 230, 230), 1, cv2.LINE_AA)

    def _render_proctor_roaming(self, canvas: np.ndarray, roaming_detections: List[Detection]) -> None:
        """Render roaming persons subtly in proctor mode."""
        for det in roaming_detections:
            x1, y1, x2, y2 = [int(v) for v in det.bbox]
            # Thin, subtle orange dotted/dashed frame
            cv2.rectangle(canvas, (x1, y1), (x2, y2), (0, 140, 255), 1, cv2.LINE_AA)
            lbl = "ROAMING"
            (tw, th), _ = cv2.getTextSize(lbl, cv2.FONT_HERSHEY_SIMPLEX, 0.32, 1)
            cv2.rectangle(canvas, (x1, y1 - th - 3), (x1 + tw + 4, y1), (20, 20, 20), -1)
            cv2.putText(canvas, lbl, (x1 + 2, y1 - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.32, (0, 180, 255), 1, cv2.LINE_AA)

    # -------------------------------------------------------------------------
    # DEVELOPER DEBUG MODE RENDERING
    # -------------------------------------------------------------------------

    def _render_debug_detections(
        self,
        canvas: np.ndarray,
        detections: List[Detection],
        raw_observations: Dict[str, List[RawObservation]],
    ) -> None:
        """Draw bounding boxes, keypoint skeletons, and 3D head rays for development telemetry."""
        for det in detections:
            x1, y1, x2, y2 = [int(v) for v in det.bbox]
            # Yellow detection bbox
            cv2.rectangle(canvas, (x1, y1), (x2, y2), (0, 230, 255), 1, cv2.LINE_AA)

            kps = det.keypoints
            if kps is None or len(kps) < 5:
                continue

            # Draw Skeleton Bones
            for p1_idx, p2_idx in SKELETON_PAIRS:
                if p1_idx < len(kps) and p2_idx < len(kps):
                    pt1 = kps[p1_idx]
                    pt2 = kps[p2_idx]
                    if len(pt1) >= 3 and len(pt2) >= 3 and pt1[2] > 0.25 and pt2[2] > 0.25:
                        cv2.line(
                            canvas,
                            (int(pt1[0]), int(pt1[1])),
                            (int(pt2[0]), int(pt2[1])),
                            (0, 220, 220),
                            1,
                            cv2.LINE_AA,
                        )

            # Draw Keypoint Joints
            for kp in kps:
                if len(kp) >= 3 and kp[2] > 0.30:
                    cv2.circle(canvas, (int(kp[0]), int(kp[1])), 2, (0, 255, 0), -1, cv2.LINE_AA)

            # Draw 3D Head Ray from Nose
            nose = kps[0]
            if len(nose) >= 3 and nose[2] > 0.30:
                nx, ny = int(nose[0]), int(nose[1])
                # Calculate yaw/pitch from nose & ears
                if len(kps) > 4 and kps[3][2] > 0.25 and kps[4][2] > 0.25:
                    ear_mid_x = (kps[3][0] + kps[4][0]) / 2.0
                    ear_dist = max(1.0, float(np.linalg.norm(kps[3][:2] - kps[4][:2])))
                    yaw_ratio = (nose[0] - ear_mid_x) / (ear_dist / 2.0)
                    deg_yaw = float(np.clip(yaw_ratio * 45.0, -90.0, 90.0))

                    rad_yaw = math.radians(deg_yaw)
                    arrow_len = 32.0
                    dx = int(arrow_len * math.sin(rad_yaw))
                    dy = int(arrow_len * 0.3)

                    ray_col = (0, 60, 255) if abs(deg_yaw) >= 28.0 else (50, 255, 50)
                    cv2.arrowedLine(canvas, (nx, ny), (nx + dx, ny + dy), ray_col, 2, cv2.LINE_AA, tipLength=0.35)

    def _render_debug_seats(
        self,
        canvas: np.ndarray,
        seat_mgr: SeatManager,
        risk_tracker: SeatRiskTracker,
    ) -> None:
        """Draw full seat polygon boundaries, desk boundaries, and debug labels."""
        for seat_id, s_def in seat_mgr.seats.items():
            poly = s_def.polygon.astype(np.int32)
            cv2.polylines(canvas, [poly], isClosed=True, color=(100, 255, 100), thickness=1, lineType=cv2.LINE_AA)

            if s_def.desk_y is not None:
                min_x = int(np.min(poly[:, 0]))
                max_x = int(np.max(poly[:, 0]))
                d_y = int(s_def.desk_y)
                cv2.line(canvas, (min_x, d_y), (max_x, d_y), (100, 180, 255), 1, cv2.LINE_AA)

            cx = int(np.mean(poly[:, 0]))
            cy = int(np.mean(poly[:, 1]))
            short_lbl = get_short_seat_label(seat_id, s_def.seat_label)
            profile = risk_tracker.profiles.get(seat_id)
            score = profile.risk_score if profile else 0.0

            dbg_txt = f"{short_lbl}:{score:.0f}"
            (tw, th), _ = cv2.getTextSize(dbg_txt, cv2.FONT_HERSHEY_SIMPLEX, 0.34, 1)
            cv2.rectangle(canvas, (cx - tw // 2 - 2, cy - th - 2), (cx + tw // 2 + 2, cy + 2), (10, 10, 10), -1)
            cv2.putText(canvas, dbg_txt, (cx - tw // 2, cy - 1), cv2.FONT_HERSHEY_SIMPLEX, 0.34, (200, 255, 200), 1, cv2.LINE_AA)

    def _render_debug_roaming(self, canvas: np.ndarray, roaming_detections: List[Detection]) -> None:
        for det in roaming_detections:
            x1, y1, x2, y2 = [int(v) for v in det.bbox]
            cv2.rectangle(canvas, (x1, y1), (x2, y2), (0, 140, 255), 2, cv2.LINE_AA)
            cv2.putText(canvas, "ROAMING", (x1 + 4, y1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 140, 255), 1, cv2.LINE_AA)

    def _render_debug_panel(
        self,
        canvas: np.ndarray,
        runtime_metrics: Dict[str, float],
        active_episodes: List[TemporalEpisode],
        raw_observations: Dict[str, List[RawObservation]],
    ) -> None:
        """Render telemetry box on top right."""
        h, w = canvas.shape[:2]
        panel_w = 260
        panel_h = 185
        px1 = w - panel_w - 12
        py1 = 42
        px2 = w - 12
        py2 = py1 + panel_h

        overlay = canvas.copy()
        cv2.rectangle(overlay, (px1, py1), (px2, py2), (15, 15, 20), -1)
        cv2.addWeighted(overlay, 0.85, canvas, 0.15, 0, canvas)
        cv2.rectangle(canvas, (px1, py1), (px2, py2), (80, 80, 80), 1)

        font = cv2.FONT_HERSHEY_SIMPLEX
        cv2.putText(canvas, "DEBUG TELEMETRY [D]", (px1 + 10, py1 + 18), font, 0.40, (0, 230, 255), 1, cv2.LINE_AA)

        lines = [
            f"Perception (YOLO-Pose): {runtime_metrics.get('lat_perception_ms', 0.0):.1f} ms",
            f"6DRepNet Subsampled:    {runtime_metrics.get('lat_6drepnet_ms', 0.0):.1f} ms",
            f"Temporal Episodes:      {runtime_metrics.get('lat_temporal_ms', 0.0):.1f} ms",
            f"Pattern Engine:         {runtime_metrics.get('lat_pat_ms', 0.0):.1f} ms",
            f"Seat Risk Tracker:      {runtime_metrics.get('lat_risk_ms', 0.0):.1f} ms",
            f"Rendering & I/O:        {runtime_metrics.get('lat_render_ms', 0.0):.1f} ms",
            f"Total Frame Pipeline:   {runtime_metrics.get('lat_total_ms', 0.0):.1f} ms",
            f"Active Episodes:        {len(active_episodes)}",
        ]

        curr_y = py1 + 38
        for l in lines:
            cv2.putText(canvas, l, (px1 + 10, curr_y), font, 0.32, (210, 210, 210), 1, cv2.LINE_AA)
            curr_y += 18

    # -------------------------------------------------------------------------
    # SHARED HUD BANNERS
    # -------------------------------------------------------------------------

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
        """Top non-polluting proctor status bar with responsive text spacing."""
        h, w = canvas.shape[:2]
        banner_h = 28 if w < 800 else 32

        overlay = canvas.copy()
        cv2.rectangle(overlay, (0, 0), (w, banner_h), (18, 18, 22), -1)
        cv2.addWeighted(overlay, 0.85, canvas, 0.15, 0, canvas)
        cv2.line(canvas, (0, banner_h), (w, banner_h), (60, 60, 70), 1)

        time_sec = timestamp_ms / 1000.0
        time_str = f"{int(time_sec // 60):02d}:{time_sec % 60:04.1f}"

        total_seats = len(seat_mgr.seats)
        occupied_seats = sum(1 for occ in seat_mgr.occupancies.values() if occ.state == SeatState.OCCUPIED)
        review_count = sum(
            1 for p in risk_tracker.profiles.values()
            if p.current_state in (RiskState.FLAGGED_FOR_REVIEW.value, RiskState.COOLDOWN.value)
            or p.risk_score >= 75.0
        )

        font = cv2.FONT_HERSHEY_SIMPLEX
        y_pos = 19 if w < 800 else 21

        if w < 800:
            scale = 0.31
            txt_left = f"VIGIL | {room_code}"
            txt_mid = f"{time_str} | {occupied_seats}/{total_seats} Occ"
            txt_right = f"[!] {review_count} REVIEW" if review_count > 0 else "OK"
        else:
            scale = 0.40
            txt_left = f"VIGIL AI | Room: {room_code} | Cam: {camera_id}"
            txt_mid = f"Time: {time_str} ({timestamp_ms:.0f}ms) | {occupied_seats}/{total_seats} Occupied"
            txt_right = f"[ ! ] {review_count} SEAT(S) IN REVIEW" if review_count > 0 else "Normal Surveillance"

        cv2.putText(canvas, txt_left, (10, y_pos), font, scale, (220, 220, 220), 1, cv2.LINE_AA)

        (mw, _), _ = cv2.getTextSize(txt_mid, font, scale, 1)
        cv2.putText(canvas, txt_mid, (w // 2 - mw // 2, y_pos), font, scale, (100, 230, 255), 1, cv2.LINE_AA)

        color_r = (50, 70, 255) if review_count > 0 else (140, 220, 140)
        (rw, _), _ = cv2.getTextSize(txt_right, font, scale, 1)
        cv2.putText(canvas, txt_right, (w - rw - 10, y_pos), font, scale, color_r, 1, cv2.LINE_AA)

    def _render_bottom_ticker(
        self,
        canvas: np.ndarray,
        recent_events: List[ClassroomEvent],
        current_timestamp_ms: float,
    ) -> None:
        """Bottom incident ticker for recent review triggers with responsive scaling."""
        if not recent_events:
            return

        latest = recent_events[-1]
        evt_ts = getattr(latest, "timestamp_ms", None)
        if evt_ts is None:
            evt_ts = (getattr(latest, "timestamp", 0.0) or 0.0) * 1000.0

        # Only display if triggered within last 6 seconds
        if (current_timestamp_ms - evt_ts) > 6000.0:
            return

        h, w = canvas.shape[:2]
        ticker_h = 24 if w < 800 else 28

        overlay = canvas.copy()
        cv2.rectangle(overlay, (0, h - ticker_h), (w, h), (14, 14, 20), -1)
        cv2.addWeighted(overlay, 0.85, canvas, 0.15, 0, canvas)
        cv2.line(canvas, (0, h - ticker_h), (w, h - ticker_h), (40, 40, 220), 1)

        t_sec = evt_ts / 1000.0
        time_str = f"{int(t_sec // 60):02d}:{t_sec % 60:04.1f}"
        seat_raw = getattr(latest, "seat_id", getattr(latest, "seat_code", "UNKNOWN"))
        short_seat = get_short_seat_label(seat_raw)
        beh_name = getattr(latest, "behavior", getattr(latest, "title", "Review Trigger")).replace("_", " ").title()
        sev_name = latest.severity.value if hasattr(latest.severity, "value") else str(latest.severity)

        scale = 0.31 if w < 800 else 0.38
        y_text = h - 7 if w < 800 else h - 9

        if w < 800:
            msg = f"[REVIEW @ {time_str}] Seat {short_seat}: {beh_name} ({sev_name})"
        else:
            msg = f"[REVIEW REQUIRED @ {time_str}] Seat {short_seat}: {beh_name} (Priority: {sev_name})"

        cv2.putText(canvas, msg, (10, y_text), cv2.FONT_HERSHEY_SIMPLEX, scale, (80, 180, 255), 1, cv2.LINE_AA)
