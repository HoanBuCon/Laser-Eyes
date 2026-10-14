"""Where VIGIL One keeps its files, from source or installed.

* From source: everything under the project's ``data/`` folder (as before).
* Installed (PyInstaller build): ``%LOCALAPPDATA%\\VIGIL One\\data`` — the
  install folder may be read-only and is replaced on every update.
* ``VIGIL_ONE_DATA`` overrides both (tests, several copies on one machine).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent


def is_installed() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_data_dir() -> Path:
    override = os.environ.get("VIGIL_ONE_DATA")
    if override:
        return Path(override)
    if is_installed() and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "VIGIL One" / "data"
    return PROJECT_DIR / "data"


def resource(*parts: str) -> Path:
    """A file shipped with the app (logo, models, font), from source or from the build."""
    base = Path(getattr(sys, "_MEIPASS", PROJECT_DIR))
    return base.joinpath(*parts)
