"""Fingerprint of the Hub's code on disk.

The Hub reports the fingerprint it started with (``/health``); ``vigil.py`` compares it with
the code on disk to notice a Hub left running from before a ``git pull`` or a branch switch —
an old Hub silently refuses or misreads what a newer VIGIL One sends.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
_PARTS = ("api", "storage", "classroom_monitor", "exam_monitor", "dashboard")
_SUFFIXES = {".py", ".html", ".js", ".css"}


def fingerprint(root: Path = ROOT) -> str:
    digest = hashlib.sha1()
    files = [root / "server.py", root / "vigil_build.py"]
    for part in _PARTS:
        folder = root / part
        if folder.is_dir():
            files.extend(p for p in folder.rglob("*") if p.suffix in _SUFFIXES and "__pycache__" not in p.parts)
    for path in sorted(files):
        try:
            stat = path.stat()
        except OSError:
            continue
        digest.update(f"{path.relative_to(root).as_posix()}:{stat.st_size}:{stat.st_mtime_ns}\n".encode())
    return digest.hexdigest()[:12]
