"""System maintenance for demos and testing (the /admin page).

Every destructive action needs a typed confirmation phrase, refuses to run
while an analysis is in progress and is written to the audit log; actions that
delete database rows back the SQLite database up first.  Rooms, cameras and
Seat ROIs survive the incident reset, and the audit log is never deleted.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.dependencies import get_db
from classroom_monitor.demo.config import DEMO_PRESETS
from classroom_monitor.demo.paths import demo_final_root, demo_runs_root
from classroom_monitor.demo.runtime import DemoRuntime, DemoState
from classroom_monitor.evidence_playback import playback_cache_root
from storage import database
from storage.db_models import AuditLog
from storage.maintenance import (
    apply_cleanup,
    backup_sqlite,
    clear_directory,
    directory_size,
    orphan_run_dirs,
    plan_test_room_cleanup,
    remove_directories,
    reset_incident_data,
    run_ids_in_files,
    table_counts,
)

router = APIRouter(prefix="/admin", tags=["System maintenance"])

CONFIRM_PHRASES = {
    "test-rooms": "DELETE",
    "orphan-runs": "DELETE",
    "playback-cache": "DELETE",
    "reset-incidents": "RESET ALL",
}


class AdminAction(BaseModel):
    confirm: str = Field("", description="Typed confirmation phrase for the action")
    actor: Optional[str] = Field(None, description="Who performed the action (audit log)")


def _orphan_runs(db: Session, active_run_id: str):
    # Replay packages refer to clips inside run folders; those runs are kept too
    return orphan_run_dirs(db, demo_runs_root(), active_run_id, also_keep=run_ids_in_files(demo_final_root()))


def _demo_room_codes():
    return [preset["room_code"] for preset in DEMO_PRESETS.values() if preset.get("room_code")]


def _active_run() -> Dict[str, Any]:
    status = DemoRuntime.get_instance().get_status()
    busy = status.get("state") in (DemoState.RUNNING.value, DemoState.PAUSED.value)
    return {"busy": busy, "run_id": status.get("run_id") or "", "state": status.get("state")}


def _guard(action: str, payload: AdminAction) -> None:
    phrase = CONFIRM_PHRASES[action]
    if payload.confirm.strip().upper() != phrase:
        raise HTTPException(status_code=400, detail=f"Type '{phrase}' to confirm this action.")
    if _active_run()["busy"]:
        raise HTTPException(status_code=409, detail="An analysis is running. Stop it before maintenance.")


def _audit(db: Session, payload: AdminAction, action: str, details: Dict[str, Any]) -> None:
    db.add(AuditLog(
        actor_id=((payload.actor or "").strip() or "admin")[:100],
        action=f"ADMIN_{action.upper().replace('-', '_')}",
        resource_type="SYSTEM",
        resource_id=action,
        metadata_json=json.dumps(details, default=str),
    ))
    db.commit()


def _backup() -> Optional[str]:
    target = backup_sqlite(database.DATABASE_URL)
    return str(target) if target else None


def _dir_entry(path: Path) -> Dict[str, Any]:
    size, files = directory_size(path)
    return {"path": str(path), "bytes": size, "files": files}


def _database_file() -> Optional[Dict[str, Any]]:
    if not database.DATABASE_URL.startswith("sqlite:///"):
        return None
    path = Path(database.DATABASE_URL.replace("sqlite:///", "", 1))
    if not path.is_file():
        return None
    wal = path.with_name(path.name + "-wal")
    size = path.stat().st_size + (wal.stat().st_size if wal.is_file() else 0)
    return {"path": str(path), "bytes": size}


@router.get("/overview")
def overview(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Database rows, disk usage and what each cleanup would remove."""
    active = _active_run()
    runs_root = demo_runs_root()
    orphans = _orphan_runs(db, active["run_id"])
    run_count = len([d for d in runs_root.iterdir() if d.is_dir()]) if runs_root.is_dir() else 0
    plan = plan_test_room_cleanup(db, keep_room_codes=_demo_room_codes())
    return {
        "runtime": active,
        "database": _database_file(),
        "tables": table_counts(db),
        "storage": {
            "demo_runs": {**_dir_entry(runs_root), "runs": run_count},
            "replay_packages": _dir_entry(demo_final_root()),
            "playback_cache": _dir_entry(playback_cache_root()),
        },
        "cleanup": {
            "test_rooms": {
                "rooms": len(plan.rooms),
                "sessions": len(plan.session_ids),
                "incidents": len(plan.event_ids),
                "examples": [f"{room.room_code or room.id} — {room.name}" for room in plan.rooms[:8]],
            },
            "orphan_runs": {
                "runs": len(orphans),
                "bytes": sum(directory_size(d)[0] for d in orphans),
                "examples": [d.name for d in orphans[:8]],
            },
        },
        "confirm_phrases": CONFIRM_PHRASES,
    }


@router.post("/cleanup/test-rooms")
def cleanup_test_rooms(payload: AdminAction, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Remove rooms without Seat ROIs (left by tests) with their sessions and incidents."""
    _guard("test-rooms", payload)
    plan = plan_test_room_cleanup(db, keep_room_codes=_demo_room_codes())
    result: Dict[str, Any] = {"rooms": len(plan.rooms), "sessions": len(plan.session_ids), "incidents": len(plan.event_ids)}
    if plan.rooms:
        result["backup"] = _backup()
        apply_cleanup(db, plan)
    _audit(db, payload, "test-rooms", result)
    return result


@router.post("/cleanup/orphan-runs")
def cleanup_orphan_runs(payload: AdminAction, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Delete demo run folders that no incident in the database refers to."""
    _guard("orphan-runs", payload)
    orphans = _orphan_runs(db, _active_run()["run_id"])
    freed = sum(directory_size(d)[0] for d in orphans)
    result = {"runs": remove_directories(orphans), "bytes": freed}
    _audit(db, payload, "orphan-runs", result)
    return result


@router.post("/cleanup/playback-cache")
def cleanup_playback_cache(payload: AdminAction, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Drop browser-playable copies of evidence clips; they are rebuilt on demand."""
    _guard("playback-cache", payload)
    root = playback_cache_root()
    freed = directory_size(root)[0]
    result = {"entries": clear_directory(root), "bytes": freed}
    _audit(db, payload, "playback-cache", result)
    return result


@router.post("/reset/incidents")
def reset_incidents(payload: AdminAction, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Start from a clean slate: delete every session, incident, review and bookmark."""
    _guard("reset-incidents", payload)
    backup = _backup()
    counts = reset_incident_data(db)
    DemoRuntime.get_instance().reset()
    result = {**counts, "backup": backup}
    _audit(db, payload, "reset-incidents", result)
    return result
