"""Test isolation: no test may write the real SQLite DB or the real ``data/`` demo folders.

``storage.database`` reads ``DATABASE_URL`` at import time, so the temporary
database is configured here, before any test module imports it.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

_TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="vigil-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TEST_DATA_DIR / 'vigil_test.db').as_posix()}"
os.environ["VIGIL_PROCTOR_EVIDENCE_ROOT"] = str(_TEST_DATA_DIR / "proctor_bookmarks")
os.environ["VIGIL_PLAYBACK_CACHE_ROOT"] = str(_TEST_DATA_DIR / "evidence_playback")


@pytest.fixture(autouse=True)
def _isolated_demo_output_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """LIVE runs and replay packages go under the test's own tmp_path."""
    monkeypatch.setenv("VIGIL_DEMO_RUNS_ROOT", str(tmp_path / "data" / "demo_runs"))
    monkeypatch.setenv("VIGIL_DEMO_FINAL_ROOT", str(tmp_path / "data" / "demo_final"))


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    try:
        from storage.database import engine

        engine.dispose()
    except Exception:
        pass
    shutil.rmtree(_TEST_DATA_DIR, ignore_errors=True)


@pytest.fixture(scope="session")
def calibrated_demo_rooms() -> None:
    """Calibrate both demo rooms in the test database the way an operator does:
    import the bundled starting layout through the calibration API."""
    from fastapi.testclient import TestClient

    from api.main import app
    from server import ensure_calibration_sources
    from storage.database import SessionLocal, init_db
    from storage.db_models import ExamRoom

    init_db()
    db = SessionLocal()
    try:
        ensure_calibration_sources(db)
        room_ids = [r.id for r in db.query(ExamRoom).filter(ExamRoom.room_code.in_(["ROOM-CALIB-01", "ROOM-STUDENT-01"]))]
    finally:
        db.close()
    with TestClient(app) as client:
        for room_id in room_ids:
            res = client.post(f"/api/v1/rooms/{room_id}/seats/import-template", params={"replace": True})
            assert res.status_code == 200, res.text
