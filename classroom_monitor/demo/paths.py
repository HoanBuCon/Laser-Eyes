"""Filesystem roots for Classroom demo runs and the canonical replay packages.

Both roots are resolved on every call so tests and alternative deployments can
redirect them through environment variables without touching the real
``data/`` directory.
"""

from __future__ import annotations

import os
from pathlib import Path


def demo_runs_root() -> Path:
    """Per-run LIVE output directories (``data/demo_runs`` by default)."""
    return Path(os.getenv("VIGIL_DEMO_RUNS_ROOT", "data/demo_runs"))


def demo_final_root() -> Path:
    """Canonical replay packages, one directory per preset (``data/demo_final`` by default)."""
    return Path(os.getenv("VIGIL_DEMO_FINAL_ROOT", "data/demo_final"))


def replay_package_dir(preset: str) -> Path:
    return demo_final_root() / preset


# Generated files a replay package may contain; anything else is left alone.
REPLAY_PACKAGE_PATTERNS = ("*.json", "*.mp4", "*.csv")


def purge_replay_package(package_dir: Path) -> None:
    """Remove generated artifacts so a new package never mixes with an older run."""
    package_dir = Path(package_dir)
    for pattern in REPLAY_PACKAGE_PATTERNS:
        for stale_file in package_dir.glob(pattern):
            try:
                stale_file.unlink()
            except OSError:
                pass
    evidence_dir = package_dir / "evidence"
    if evidence_dir.exists():
        for stale_ev in evidence_dir.glob("*"):
            if stale_ev.is_file():
                try:
                    stale_ev.unlink()
                except OSError:
                    pass
