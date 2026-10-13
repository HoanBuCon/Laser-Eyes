"""Per-seat risk score over time, for the playback timeline.

One sample per seat every ``interval_ms`` of source time (default 1 s), taken
from the seat risk tracker while the analysis runs.  Seats that never rise
above zero are left out, so a 45-minute session stays a few hundred KB.
Written as ``seat_timeline.json`` next to the run's other artifacts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Mapping

TIMELINE_FILE = "seat_timeline.json"


class SeatTimelineRecorder:
    def __init__(self, interval_ms: float = 1000.0) -> None:
        self.interval_ms = float(interval_ms)
        self._next_ms = 0.0
        self._samples = 0
        self._series: Dict[str, List[int]] = {}

    def observe(self, timestamp_ms: float, scores: Mapping[str, float]) -> bool:
        """Record ``scores`` (seat code → 0..100) if a sample is due; True when recorded."""
        if timestamp_ms < self._next_ms:
            return False
        for code, score in scores.items():
            series = self._series.get(code)
            if series is None:
                series = self._series[code] = [0] * self._samples
            series.append(int(round(max(0.0, min(100.0, float(score))))))
        self._samples += 1
        for series in self._series.values():
            if len(series) < self._samples:
                series.append(0)
        self._next_ms = self._samples * self.interval_ms
        return True

    def to_dict(self) -> dict:
        seats = {code: values for code, values in sorted(self._series.items()) if any(values)}
        return {"interval_ms": self.interval_ms, "samples": self._samples, "seats": seats}

    def write(self, directory: Path) -> Path:
        path = Path(directory) / TIMELINE_FILE
        path.write_text(json.dumps(self.to_dict(), separators=(",", ":")), encoding="utf-8")
        return path


def load_timeline(directory: Path) -> dict | None:
    path = Path(directory) / TIMELINE_FILE
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
