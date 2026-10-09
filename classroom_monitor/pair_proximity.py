"""Two desk mates bringing their heads together over one paper.

Head-pose yaw cannot tell "looking at my own paper" from "looking at my
neighbour's" when both lie in the same direction from the camera, and a
student whose head is over the neighbour's desk all the time makes that
the "normal" posture of any per-student baseline.  The distance between the
two noses does not have that problem: desk mates normally sit about one and
a half shoulder widths apart, and copying from the same sheet brings the
heads much closer.

A pair counts as heads together when the horizontal gap between the two
noses, in shoulder widths, is

* at most ``room_ratio`` of the room's usual neighbour gap, and
* at most ``own_ratio`` of the pair's own gap when sitting apart (the 90th
  percentile of its recent gaps), so a pair that always looks close in the
  image (perspective) is not flagged,

for at least ``min_duration_ms`` with nobody else at either seat.  Only
left/right neighbours of the seat graph are paired, which are desk mates in
the same row.  The look counts again every ``repeat_ms`` while it lasts.
"""

from __future__ import annotations

import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple

import numpy as np

from classroom_monitor.behavior_pattern_engine import BehaviorPattern, PatternType
from classroom_monitor.models import Detection
from classroom_monitor.scene_context import SeatGraph

KEYPOINT_CONF = 0.3


@dataclass
class _PairState:
    gaps: Deque[Tuple[float, float]] = field(default_factory=deque)  # (ts, gap)
    close_since: Optional[float] = None
    last_close: Optional[float] = None
    times_counted: int = 0
    last_counted: float = 0.0


def _nose_and_width(det: Optional[Detection]) -> Optional[Tuple[float, float, float]]:
    """Nose x, shoulder width and keypoint quality, or None if not all visible."""
    if det is None or det.keypoints is None or len(det.keypoints) < 7:
        return None
    nose, ls, rs = det.keypoints[0], det.keypoints[5], det.keypoints[6]
    if min(nose[2], ls[2], rs[2]) < KEYPOINT_CONF:
        return None
    width = float(np.hypot(ls[0] - rs[0], ls[1] - rs[1]))
    if width < 1.0:
        return None
    return float(nose[0]), width, float(np.mean([nose[2], ls[2], rs[2]]))


class HeadsTogetherMonitor:
    def __init__(
        self,
        seat_graph: SeatGraph,
        room_ratio: float = 0.50,
        own_ratio: float = 0.55,
        min_duration_ms: float = 2000.0,
        max_gap_ms: float = 500.0,
        repeat_ms: float = 3000.0,
        room_window_ms: float = 60000.0,
        own_window_ms: float = 120000.0,
        sample_interval_ms: float = 200.0,
        min_samples: int = 25,
    ) -> None:
        self.room_ratio = room_ratio
        self.own_ratio = own_ratio
        self.min_duration_ms = min_duration_ms
        self.max_gap_ms = max_gap_ms
        self.repeat_ms = repeat_ms
        self.room_window_ms = room_window_ms
        self.own_window_ms = own_window_ms
        self.sample_interval_ms = sample_interval_ms
        self.min_samples = min_samples
        self._room: Deque[Tuple[float, float]] = deque()
        self._pairs: Dict[Tuple[str, str], _PairState] = {}
        self._last_sample_ms = -1e12
        self.pairs: List[Tuple[str, str]] = []
        seen = set()
        for seat_id, ctx in seat_graph.seats_context.items():
            for neighbour in (ctx.neighbors.left_neighbor_id, ctx.neighbors.right_neighbor_id):
                if neighbour and seat_graph.get_context(neighbour) is not None:
                    pair = tuple(sorted((ctx.seat_id, neighbour)))
                    if pair not in seen:
                        seen.add(pair)
                        self.pairs.append(pair)
        self._graph = seat_graph

    def update(
        self,
        seat_people: Dict[str, Tuple[Optional[Detection], int, int]],
        timestamp_ms: float,
    ) -> List[BehaviorPattern]:
        """``seat_people``: seat -> (seat's person, people at the seat, usual number)."""
        sampling = timestamp_ms - self._last_sample_ms >= self.sample_interval_ms
        if sampling:
            self._last_sample_ms = timestamp_ms
        while self._room and timestamp_ms - self._room[0][0] > self.room_window_ms:
            self._room.popleft()

        current: Dict[Tuple[str, str], Tuple[float, bool, float]] = {}
        for pair in self.pairs:
            a, b = (seat_people.get(seat) for seat in pair)
            if a is None or b is None:
                continue
            fa, fb = _nose_and_width(a[0]), _nose_and_width(b[0])
            if fa is None or fb is None:
                continue
            gap = abs(fa[0] - fb[0]) / ((fa[1] + fb[1]) / 2.0)
            crowded = a[1] > max(1, a[2]) or b[1] > max(1, b[2])
            current[pair] = (gap, crowded, (fa[2] + fb[2]) / 2.0)
            if sampling and not crowded:
                state = self._pairs.setdefault(pair, _PairState())
                state.gaps.append((timestamp_ms, gap))
                while state.gaps and timestamp_ms - state.gaps[0][0] > self.own_window_ms:
                    state.gaps.popleft()
                self._room.append((timestamp_ms, gap))

        if len(self._room) < self.min_samples:
            return []
        room_gap = float(np.median([g for _, g in self._room]))

        patterns: List[BehaviorPattern] = []
        for pair, (gap, crowded, quality) in current.items():
            state = self._pairs.setdefault(pair, _PairState())
            if len(state.gaps) < self.min_samples:
                continue
            apart = float(np.percentile([g for _, g in state.gaps], 90))
            limit = min(self.room_ratio * room_gap, self.own_ratio * apart)
            if gap > limit or crowded:
                if state.last_close is not None and timestamp_ms - state.last_close > self.max_gap_ms:
                    state.close_since = None
                    state.times_counted = 0
                continue
            if state.close_since is None or (state.last_close is not None and timestamp_ms - state.last_close > self.max_gap_ms):
                state.close_since = timestamp_ms
                state.times_counted = 0
            state.last_close = timestamp_ms
            if timestamp_ms - state.close_since < self.min_duration_ms:
                continue
            if state.times_counted and timestamp_ms - state.last_counted < self.repeat_ms:
                continue
            state.times_counted += 1
            state.last_counted = timestamp_ms
            patterns.extend(self._patterns(pair, state, timestamp_ms, gap, room_gap, quality))
        return patterns

    def _patterns(self, pair, state, timestamp_ms, gap, room_gap, quality) -> List[BehaviorPattern]:
        out = []
        for seat, other in (pair, pair[::-1]):
            ctx = self._graph.get_context(seat)
            direction = "LEFT" if ctx is not None and ctx.neighbors.left_neighbor_id == other else "RIGHT"
            out.append(BehaviorPattern(
                pattern_id=str(uuid.uuid4()),
                seat_id=seat,
                pattern_type=PatternType.HEADS_TOGETHER.value,
                start_timestamp_ms=state.close_since,
                end_timestamp_ms=timestamp_ms,
                confidence=quality,
                quality=quality,
                primary_direction=direction,
                target_neighbor_id=other,
                supporting_cues=[
                    f"Heads {gap:.2f} shoulder widths apart (usual in this room {room_gap:.2f})",
                    f"Together for {(timestamp_ms - state.close_since) / 1000:.1f}s with the neighbour",
                ],
                metadata={"pair": list(pair), "evidence_segment": state.times_counted - 1},
            ))
        return out
