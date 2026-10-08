"""Tell seated people from people walking the room, without tracking IDs.

A seated student's torso stays at the same spot, so that spot has had a person
in it for nearly every recent frame.  A teacher or proctor walking between the
rows is at any given spot only briefly.  ``ResidencyMap`` keeps a short history
of where people were and reports, for each current person, how much of the
recent window their spot was occupied.  People below ``min_residency`` are
*passers-by* and are kept out of seat counts.

There are no track identities to swap when two people stand close together,
which is common when a teacher leans over a desk.  Someone who stops at a desk
becomes resident after about ``min_residency * window_ms`` and then counts,
so a visitor who stays is still reported.
"""

from __future__ import annotations

from collections import deque
from typing import Deque, List, Sequence, Tuple

import numpy as np

# (anchor_x, anchor_y, scale): scale is the person's shoulder width in pixels
Anchor = Tuple[float, float, float]


class ResidencyMap:
    def __init__(
        self,
        window_ms: float = 10000.0,
        sample_interval_ms: float = 200.0,
        same_spot_shoulder_widths: float = 0.5,
        min_residency: float = 0.5,
        min_history_ms: float = 3000.0,
    ) -> None:
        self.window_ms = window_ms
        self.sample_interval_ms = sample_interval_ms
        self.same_spot_shoulder_widths = same_spot_shoulder_widths
        self.min_residency = min_residency
        self.min_history_ms = min_history_ms
        self._history: Deque[Tuple[float, np.ndarray]] = deque()

    def residency(self, anchors: Sequence[Anchor], timestamp_ms: float) -> List[float]:
        """Fraction of the recent history in which each anchor's spot was occupied.

        Before ``min_history_ms`` of history exists everyone counts as resident.
        """
        points = np.asarray([(x, y) for x, y, _s in anchors], dtype=np.float32).reshape(-1, 2)
        radii = np.asarray(
            [self.same_spot_shoulder_widths * max(s, 1.0) for _x, _y, s in anchors], dtype=np.float32
        )

        while self._history and timestamp_ms - self._history[0][0] > self.window_ms:
            self._history.popleft()

        if self._history and timestamp_ms - self._history[0][0] >= self.min_history_ms and len(points):
            hits = np.zeros(len(points), dtype=np.float32)
            for _ts, past in self._history:
                if len(past) == 0:
                    continue
                dist = np.linalg.norm(points[:, None, :] - past[None, :, :], axis=2)
                hits += (dist.min(axis=1) <= radii).astype(np.float32)
            scores = (hits / len(self._history)).tolist()
        else:
            scores = [1.0] * len(points)

        if not self._history or timestamp_ms - self._history[-1][0] >= self.sample_interval_ms:
            self._history.append((timestamp_ms, points))
        return scores

    def passers_by(self, anchors: Sequence[Anchor], timestamp_ms: float) -> List[bool]:
        """True for each anchor whose spot was mostly empty in the recent window."""
        return [score < self.min_residency for score in self.residency(anchors, timestamp_ms)]
